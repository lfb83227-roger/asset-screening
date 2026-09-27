"""债权轨道：五维量化评分引擎（总分 100）。

债权与物权的本质差别：**买的不是资产，是"收钱的权利"**。
因此关心的不是区位、租金、清场，而是"这笔钱能不能收回、多久能收回、
排在别人前面还是后面"。业务确认的维度与权重：

  coverage       抵押物覆盖倍数维度  30 分  ← 第一命门：值不值得买
  rank           担保顺位维度        25 分  ← 排在别人前面还是后面
  execution      执行进展维度        20 分  ← 多久能拿到钱
  solvency       债务人偿付能力维度  15 分  ← 差额部分的兜底来源
  documentation  债权凭证完整性维度  10 分  ← 诉讼/过户的可行性与成本

设计要点：
* `coverage` 是**唯一决定性维度**：覆盖不足 1 倍时，无论执行多顺利都收不回本金，
  因此权重最高且锚点在 1.0 处断崖（3 分），刻意制造"不足额即低分"的强信号。
* `rank` 与 `execution` 共同决定"能不能拿到"与"多久拿到"：
  首封一顺位 vs 二顺位是处置价款能否落袋的分水岭，二者不重复计入。
* 债权**没有一票否决**（业务明确要求：2、3 两类风险设权重系数作重点扣分项，
  不建议一票否决）。真正的硬否决（V6：债权不可转让 / 已过诉讼时效）在
  veto 层处理，因为那属于"根本不能买"，而非"买得划不划算"。
* 无随机数、无隐式状态 → 结果完全可复现。
"""
from __future__ import annotations

from typing import Any

from app.core.ruleset import RuleSet, clamp, interp, r2
from app.core.scoring_common import _anchor_max, _dim, _pick

DIMENSION_ORDER = ("coverage", "rank", "execution", "solvency", "documentation")

#: 分值表兜底（键不存在时按"未载明从严"口径给分）
_RANK_DEFAULT = 5.0
_EXEC_DEFAULT = 6.0
_SOLVENCY_DEFAULT = 5.0
_DOC_DEFAULT = 3.0


# ==================================================================== 维度1 覆盖倍数
def score_coverage(asset, rs: RuleSet) -> dict:
    """抵押物覆盖倍数维度（30 分）—— 债权第一命门。

    覆盖倍数 = 抵押物评估价值 ÷ 债权本息总额。

    < 1.0 说明**即使抵押物全额变现也不够还本**，差额只能指望债务人其他财产，
    回收前景本质上是"担保不足"的信用风险；锚点在 1.0 处断崖式设小分，
    就是为了让这类标的无论其他维度多好都排不上号。
    """
    weight = rs.dim_max("coverage", "debt")
    anchors = rs.coverage_anchors
    amax = _anchor_max(anchors, 30.0)

    coverage = asset.guarantee_coverage
    claim = asset.debt_total_claim
    collateral = asset.collateral_value

    inputs = {
        "抵押物评估价值": r2(collateral),
        "债权本金": r2(asset.debt_principal),
        "债权利息/违约金": r2(asset.debt_interest),
        "债权本息合计": r2(claim),
        "覆盖倍数": f"{coverage:.2f}" if coverage is not None else "—",
        "债权转让起拍价": r2(asset.debt_start_price),
    }

    if coverage is None:
        reason = ("缺少抵押物评估价值" if not collateral else "缺少债权本金")
        return _dim("coverage", "抵押物覆盖倍数维度", weight, 0.0, amax, [], inputs,
                    note=f"{reason}，无法计算覆盖倍数，该维度不计分")

    raw = interp(anchors, coverage)

    # 转让起拍价 vs 债权本息：真正的"投入产出比"。
    # 实务中债权常按本息的 3~7 折转让，折得越狠安全垫越厚。
    start = asset.debt_start_price
    price_ratio = None
    if start and claim > 0:
        price_ratio = float(start) / claim

    items = [{
        "name": "抵押物覆盖倍数",
        "value": f"{coverage:.2f} 倍",
        "raw": round(raw, 2),
        "max": round(amax, 2),
        "note": f"抵押物 {r2(collateral)} ÷ 债权本息 {r2(claim)} 元",
        "scored": True,
    }]
    if price_ratio is not None:
        items.append({
            "name": "转让价 / 债权本息",
            "value": f"{price_ratio * 100:.1f}%",
            "raw": None, "max": None,
            "note": ("收购成本低于债权面值，覆盖倍数已含安全垫"
                     if price_ratio < 1.0 else "收购价不低于债权面值，无价格折让"),
            "scored": False,
        })

    if coverage < 1.0:
        note = ("覆盖不足 1 倍，抵押物全额变现仍无法还本，差额需依赖债务人其他财产，"
                "回收前景不佳")
    elif coverage < 1.5:
        note = "勉强覆盖本金，安全垫偏薄，需关注处置折价与费用侵蚀"
    else:
        note = "覆盖充足，抵押物处置价款可覆盖本息，安全垫较厚"
    return _dim("coverage", "抵押物覆盖倍数维度", weight, raw, amax, items, inputs, note)


# ==================================================================== 维度2 担保顺位
def score_rank(asset, rs: RuleSet, penalty_hits: list[dict] | None = None) -> dict:
    """担保顺位维度（25 分）。

    处置价款按顺位分配：首封/一顺位最先受偿，二顺位必须等前顺位清偿完
    才有余额。所以"顺位"直接决定**这笔钱轮不轮得到你**，
    与覆盖倍数（值不值得）互不重复。

    另叠加「竞争者数量」扣分：已知其他债权人越多，可分配余额被摊薄越严重。
    """
    weight = rs.dim_max("rank", "debt")
    scores = rs.debt_rank_scores or {}
    amax = max((float(v) for v in scores.values()), default=25.0) or 25.0

    rank = (asset.guarantee_rank or "unknown").lower()
    if rank not in scores:
        rank = "unknown"
    raw = float(scores.get(rank, _RANK_DEFAULT))

    claims = int(asset.competing_claims or 0)
    claim_penalty = min(claims * 1.5, 6.0)
    raw = max(0.0, raw - claim_penalty)

    penalty_note = ""
    penalty_from_veto = 0.0
    for hit in penalty_hits or []:
        # 债权轨的否决规则命中（V4 权属争议等）在此额外重罚
        if hit.get("code") in ("V4", "V6"):
            penalty_from_veto = float(weight) * 0.4
            penalty_note = f"触发规则 {hit['code']} {hit['name']}，本维度额外重罚"
    raw = max(0.0, raw - penalty_from_veto)

    from app.core import constants as C
    rank_labels = C.GUARANTEE_RANKS

    items = [
        {"name": "担保顺位", "value": rank_labels.get(rank, rank),
         "raw": round(float(scores.get(rank, _RANK_DEFAULT)), 2), "max": round(amax, 2),
         "note": ("首封/一顺位最先受偿" if rank == "first" else
                  "需前顺位清偿后才有余额" if rank == "second" else
                  "非抵押担保，受偿保障弱" if rank == "other" else
                  "无担保，纯信用债权" if rank == "none" else "顺位未载明，按从严处理"),
         "scored": True},
        {"name": "其他债权人", "value": f"{claims} 家", "raw": -round(claim_penalty, 2),
         "max": 6.0, "note": "每增加 1 家竞争者扣 1.5 分，封顶 6 分（可分配余额被摊薄）",
         "scored": claims > 0},
    ]
    if penalty_from_veto:
        items.append({"name": "权属/流转风险重罚", "value": f"-{penalty_from_veto:.1f} 分",
                      "raw": -round(penalty_from_veto, 2), "max": 0.0,
                      "note": penalty_note, "scored": True})

    inputs = {"担保顺位": rank, "其他债权人数量": claims}
    note = ("首封一顺位，受偿次序最优" if rank == "first" else
            "顺位靠后，需前顺位清偿后才有余额" if rank == "second" else
            "担保方式偏弱或未载明，受偿保障不足")
    return _dim("rank", "担保顺位维度", weight, raw, amax, items, inputs, note)


# ==================================================================== 维度3 执行进展
def score_execution(asset, rs: RuleSet) -> dict:
    """执行进展维度（20 分）。

    买债权买的是**回款周期**。已挂拍/执行中的债权可能数月内回款，
    诉讼中的可能还要一两年，终本/流拍的则处置受阻、回款遥遥无期。
    """
    weight = rs.dim_max("execution", "debt")
    scores = rs.debt_execution_scores or {}
    amax = max((float(v) for v in scores.values()), default=20.0) or 20.0

    stage = (asset.execution_stage or "unknown").lower()
    if stage not in scores:
        stage = "unknown"
    raw = float(scores.get(stage, _EXEC_DEFAULT))

    from app.core import constants as C
    stage_labels = C.EXECUTION_STAGES

    items = [{
        "name": "执行阶段",
        "value": stage_labels.get(stage, stage),
        "raw": round(raw, 2),
        "max": round(amax, 2),
        "note": ("已实际回款，回款路径最短" if stage == "settled" else
                 "抵押物已挂拍，预计数月内可处置" if stage == "auctioning" else
                 "执行程序中，回款可期但需时" if stage == "executing" else
                 "已判决但尚未申请执行，需先启动程序" if stage == "judged" else
                 "诉讼/仲裁中，周期最长" if stage == "litigating" else
                 "终本或流拍，处置受阻，回款不确定" if stage == "failed" else
                 "执行阶段未载明，按从严处理"),
        "scored": True,
    }]
    inputs = {"执行阶段": stage}

    note = ("已回款或已挂拍，回款路径清晰、周期短" if stage in ("settled", "auctioning") else
            "执行推进中，回款可期" if stage in ("executing", "judged") else
            "诉讼周期长或处置受阻，回款时间与金额均不确定")
    return _dim("execution", "执行进展维度", weight, raw, amax, items, inputs, note)


# ==================================================================== 维度4 债务人偿付能力
def score_solvency(asset, rs: RuleSet) -> dict:
    """债务人偿付能力维度（15 分）。

    覆盖倍数不足时的**兜底来源**。债务人若有足额可供执行财产，
    即使抵押物不足也能补足；若已破产，则差额基本无望。
    """
    weight = rs.dim_max("solvency", "debt")
    scores = rs.debt_solvency_scores or {}
    amax = max((float(v) for v in scores.values()), default=15.0) or 15.0

    solvency = (asset.debtor_solvency or "unknown").lower()
    if solvency not in scores:
        solvency = "unknown"
    raw = float(scores.get(solvency, _SOLVENCY_DEFAULT))

    from app.core import constants as C
    solvency_labels = C.DEBTOR_SOLVENCY

    items = [{
        "name": "债务人偿付能力",
        "value": solvency_labels.get(solvency, solvency),
        "raw": round(raw, 2), "max": round(amax, 2),
        "note": ("有足额可供执行财产，差额可补" if solvency == "good" else
                 "有部分可供执行财产" if solvency == "fair" else
                 "基本无偿付能力，差额无望" if solvency == "poor" else
                 "已破产/清算，须参与破产分配" if solvency == "bankrupt" else
                 "偿付能力未载明，按从严处理"),
        "scored": True,
    }]
    inputs = {"债务人偿付能力": solvency}

    note = ("债务人有可为执行财产，对覆盖不足部分形成兜底" if solvency == "good" else
            "偿付能力一般，兜底作用有限" if solvency == "fair" else
            "债务人偿债能力弱或已破产，差额部分基本无望")
    return _dim("solvency", "债务人偿付能力维度", weight, raw, amax, items, inputs, note)


# ==================================================================== 维度5 凭证完整性
def score_documentation(asset, rs: RuleSet) -> dict:
    """债权凭证完整性维度（10 分）。

    凭证是否齐全直接决定：能不能顺利提起诉讼、能不能通过债权转让
    完成交割、会不会因送达瑕疵被发回重审。凭证不全是债权收购的常见暗雷，
    但属于"可补救"的问题（补送达、补公证），故权重最低而不设否决。
    """
    weight = rs.dim_max("documentation", "debt")
    scores = rs.debt_doc_scores or {}
    amax = max((float(v) for v in scores.values()), default=10.0) or 10.0

    level = (asset.debt_doc_level or "unknown").lower()
    if level not in scores:
        level = "unknown"
    raw = float(scores.get(level, _DOC_DEFAULT))

    # 不可转让 / 已过时效：虽由否决层拦截，此处也留痕降低凭证维度可信度
    flags: list[str] = []
    if asset.debt_transferable is False:
        flags.append("债权不可依法转让")
    if asset.debt_limitation_ok is False:
        flags.append("诉讼时效已过")
    if flags:
        raw = max(0.0, raw * 0.5)

    from app.core import constants as C
    doc_labels = C.DEBT_DOC_LEVELS

    items = [{
        "name": "债权凭证完整性",
        "value": doc_labels.get(level, level),
        "raw": round(raw, 2), "max": round(amax, 2),
        "note": ("判决书、合同、借据凭证齐全，诉讼与交割障碍小" if level == "full" else
                 "部分材料缺失，需补充送达证明等，存在时间成本" if level == "partial" else
                 "仅有借条或权属模糊，诉讼风险高" if level == "weak" else
                 "凭证完整性未载明，按从严处理"),
        "scored": True,
    }]
    if flags:
        items.append({"name": "流转/时效瑕疵", "value": "；".join(flags),
                      "raw": None, "max": None,
                      "note": "存在根本性流转或时效障碍，凭证维度得分按 50% 折算",
                      "scored": True})

    inputs = {
        "债权凭证完整性": level,
        "债权可转让": {True: "是", False: "否", None: "未载明"}[asset.debt_transferable],
        "诉讼时效有效": {True: "是", False: "否", None: "未载明"}[asset.debt_limitation_ok],
    }
    note = ("凭证齐全，诉讼与交割可行" if level == "full" else
            "凭证部分缺失，需补充材料" if level == "partial" else
            "凭证薄弱或存在流转/时效瑕疵，诉讼风险高")
    return _dim("documentation", "债权凭证完整性维度", weight, raw, amax, items, inputs, note)


# ==================================================================== 汇总
def score_all(asset, rs: RuleSet, penalty_hits: list[dict] | None = None) -> tuple[dict, float]:
    """跑完债权五维，返回 (score_detail, total_score)。"""
    dims = {
        "coverage": score_coverage(asset, rs),
        "rank": score_rank(asset, rs, penalty_hits),
        "execution": score_execution(asset, rs),
        "solvency": score_solvency(asset, rs),
        "documentation": score_documentation(asset, rs),
    }
    total = round(sum(dims[c]["score"] for c in DIMENSION_ORDER), 2)
    detail: dict[str, Any] = {
        "asset_class": "debt",
        "dimensions": dims,
        "order": list(DIMENSION_ORDER),
        "weights_total": round(rs.debt_weight_total(), 2),
        "total_score": total,
    }
    return detail, total
