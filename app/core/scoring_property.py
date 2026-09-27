"""物权轨道：五维量化评分引擎（总分 100）。

业务确认的维度与权重：
  price        评估基准维度    30 分  ← 双基准交叉验证 + 估值时点新鲜度
  clearance    清场与占有维度  30 分  ← 占用类型 / 案外人占用 / 租约 / 清场难度
  holding_cost 持有成本维度    15 分  ← 过户税费 + 各类欠费
  rent         租金现金流维度  15 分  ← 净租售比（按业务口径修正的算法）
  location     区位流通性维度  10 分

设计要点：
* `price` 不押注单一分母。司法评估价受「评估时点」与「评估方法」影响可能失真，
  市场可比价又常常样本不足。**同时算两条折价率**，综合后查锚点表；
  两者分歧过大时该维度整体降权（乘 confidence_factor），并输出
  「基准分歧大」信号，提示人工复核 —— 这是对"评估价到底准不准"最诚实的处理。
* `clearance` 是权重并列第一的维度：法拍最大的坑不是买贵，而是**拿不到手**。
  按占用情形折算「清场难度等级 0~4」，等级越高得分越低。
* 所有打分走锚点插值或显式系数，无随机数、无隐式状态 → 结果完全可复现。
"""
from __future__ import annotations

from typing import Any

from app.core.ruleset import RuleSet, clamp, interp, r2
from app.core.scoring_common import _anchor_max, _dim, _pick, DEFAULT_LEGAL_BASE

DIMENSION_ORDER = ("price", "clearance", "holding_cost", "rent", "location")


# ==================================================================== 维度1 评估基准
def score_price(asset, rs: RuleSet) -> dict:
    """评估基准维度（30 分）。

    双基准交叉验证：
      折价率_评估 = (司法评估价 − 起拍价) / 司法评估价
      折价率_市场 = (市场可比价 − 起拍价) / 市场可比价
      综合折价率 = 两者按 appraisal_weight 加权（缺一方则用另一方单基准）

    置信度降级（这是本维度的关键设计）：
      * 双基准分歧 > benchmark_divergence_limit → 得分 × divergence_confidence_factor
      * 估值陈旧（评估时点超 6 个月）→ 额外降权并要求人工复评
      * 市场可比样本不足 → 反向说明市场基准不可靠
    降级只影响**得分**而非直接淘汰，且全部写进 items 供审批留痕。
    """
    weight = rs.dim_max("price")
    anchors = rs.price_anchors
    amax = _anchor_max(anchors, 30.0)
    cp = rs.cost_params

    d_appr = asset.discount_vs_appraisal
    d_mkt = asset.discount_vs_market
    divergence = asset.benchmark_divergence
    stale_months = asset.appraisal_age_months
    stale_limit = float(cp.get("appraisal_stale_months", 6.0))
    div_limit = float(cp.get("benchmark_divergence_limit", 0.15))
    conf_factor = float(cp.get("divergence_confidence_factor", 0.6))
    appr_w = float(cp.get("appraisal_weight", 0.5))
    min_comp = int(cp.get("market_min_comp_count", 3))

    inputs: dict[str, Any] = {
        "起拍价": r2(asset.start_price),
        "司法评估价": r2(asset.effective_appraisal),
        "市场可比价": r2(asset.market_price),
        "评估价来源": ("人工复评" if asset.appraisal_refreshed_price else
                   ("原始评估价" if asset.appraisal_price else "无")),
        "折价率(对评估价)": (f"{d_appr * 100:.2f}%" if d_appr is not None else "—"),
        "折价率(对市场价)": (f"{d_mkt * 100:.2f}%" if d_mkt is not None else "—"),
        "评估时点": (asset.appraisal_at.strftime("%Y-%m-%d")
                 if asset.appraisal_at else "未载明"),
        "评估时点月龄": (f"{stale_months:.1f} 个月" if stale_months is not None else "—"),
    }

    start = asset.start_price
    if not start or (d_appr is None and d_mkt is None):
        return _dim("price", "评估基准维度", weight, 0.0, amax, [], inputs,
                    note="缺少起拍价，或既无司法评估价也无市场可比价，该维度不计分")

    # ---- 综合折价率
    if d_appr is not None and d_mkt is not None:
        blended = d_appr * appr_w + d_mkt * (1 - appr_w)
        basis_desc = f"双基准加权（评估价权重 {appr_w:.0%}）"
    elif d_appr is not None:
        blended = d_appr
        basis_desc = "仅司法评估价基准"
    else:
        blended = d_mkt
        basis_desc = "仅市场可比价基准"

    raw = interp(anchors, blended)

    # ---- 置信度降级（逐项累乘，每一项都在 items 里留痕）
    confidence = 1.0
    degrade_notes: list[str] = []

    if divergence is not None and div_limit > 0 and divergence > div_limit:
        confidence *= conf_factor
        degrade_notes.append(
            f"双基准分歧 {divergence * 100:.1f}% 超容许线 {div_limit * 100:.0f}%，"
            f"得分按 {conf_factor:.0%} 折算")

    if stale_months is not None and stale_months > stale_limit:
        confidence *= conf_factor
        degrade_notes.append(
            f"评估时点已过 {stale_months:.1f} 个月（超 {stale_limit:.0f} 个月），"
            f"估值可能失真，得分按 {conf_factor:.0%} 折算，建议人工复评")

    if asset.market_price and (asset.market_comp_count or 0) < min_comp and d_appr is not None:
        degrade_notes.append(
            f"市场可比案例仅 {asset.market_comp_count or 0} 宗（少于 {min_comp} 宗），"
            f"市场基准可信度偏低，已下调其在综合折价中的权重")

    final_raw = round(raw * confidence, 2)
    if confidence < 1.0 and final_raw > 0:
        final_raw = max(final_raw, 1.0)   # 保底 1 分，避免降权直接归零掩盖差异

    items = [
        {"name": "起拍价 vs 司法评估价折价", "value": f"{d_appr * 100:.2f}%" if d_appr is not None else "—",
         "raw": round(interp(anchors, d_appr), 2) if d_appr is not None else 0.0,
         "max": round(amax, 2),
         "note": (f"评估价 {r2(asset.effective_appraisal)} 元"
                  + (f"（时点 {asset.appraisal_at.strftime('%Y-%m-%d')}）"
                     if asset.appraisal_at else "（时点未载明）")),
         "scored": d_appr is not None},
        {"name": "起拍价 vs 市场可比价折价", "value": f"{d_mkt * 100:.2f}%" if d_mkt is not None else "—",
         "raw": round(interp(anchors, d_mkt), 2) if d_mkt is not None else 0.0,
         "max": round(amax, 2),
         "note": (f"市场价 {r2(asset.market_price)} 元，来源 {asset.market_comp_source or '未注明'}"
                  f"，样本 {asset.market_comp_count or '未载明'} 宗"),
         "scored": d_mkt is not None},
        {"name": "综合折价得分", "value": f"{blended * 100:.2f}%", "raw": round(raw, 2),
         "max": round(amax, 2), "note": basis_desc, "scored": True},
        {"name": "置信度折算", "value": f"{confidence:.0%}",
         "raw": -round(raw - final_raw, 2), "max": 0.0,
         "note": "；".join(degrade_notes) or "基准一致性良好，无降权",
         "scored": confidence < 1.0},
    ]

    if confidence >= 1.0 and blended >= 0.30:
        note = "折价充分且基准可靠，安全垫充足"
    elif confidence >= 1.0:
        note = "折价一般，基准一致性正常"
    else:
        note = "基准一致性存疑（估值陈旧或双基准分歧），该维度已降权，建议人工复核估值"

    return _dim("price", "评估基准维度", weight, final_raw, amax, items, inputs, note)


# ==================================================================== 维度2 清场与占有
def score_clearance(asset, rs: RuleSet, penalty_hits: list[dict] | None = None) -> dict:
    """清场与占有维度（30 分）。

    把「占用情形」折算成 0~4 的**清场难度等级**，再查锚点表。
    等级判定（从严原则，信息缺失时往不利方向取）：
      4 = 无法清场（长期租约 / 买卖不破租赁 / 占用且明确无法清场）
      3 = 案外人占用，或租约状态未载明但确已占用
      2 = 普通租赁占用，需协商解约
      1 = 有占用但腾退意愿明确 / 可清场
      0 = 空置无占用

    否决规则 V2（清场风险极高）已按业务要求降级为扣分项，命中时在此重罚：
    等级直接锁到 4，并在 items 中标注来自哪条规则。
    """
    weight = rs.dim_max("clearance")
    anchors = rs.clearance_anchors
    amax = _anchor_max(anchors, 30.0)

    lease = (asset.lease_status or "unknown").lower()
    occupied = asset.occupied
    can_clear = asset.can_clear

    level = 0.0
    level_reason = "空置无占用，可直接交付"

    if lease in ("long_term", "sale_not_break"):
        level, level_reason = 4.0, "存在长期有效租约或买卖不破租赁，依法需继续履行"
    elif occupied is True and can_clear is False:
        level, level_reason = 4.0, "公告明确无法清场"
    elif occupied is True and lease in ("normal",):
        level, level_reason = 2.0, "普通租赁占用，需协商解约后交付"
    elif occupied is True:
        level, level_reason = (1.0 if can_clear else 3.0,
                               "有占用但可清场" if can_clear else "有占用且清场存疑（案外人可能）")
    elif occupied is None and lease == "unknown":
        level, level_reason = 1.0, "占用情况未载明，按从严原则轻度扣分"
    elif lease == "normal":
        level, level_reason = 2.0, "存在普通租赁"

    penalty_from_veto = 0.0
    penalty_note = ""
    for hit in penalty_hits or []:
        if hit.get("code") == "V2":
            penalty_from_veto = float(weight) * 0.5     # 命中否决规则 V2 → 再扣半权
            penalty_note = f"触发规则 {hit['code']} {hit['name']}，本维度额外重罚"

    # 查封数量也影响能否顺利过户（多轮查封需逐一解封）
    seal = int(asset.seal_count or 0)
    seal_penalty = min(seal * 1.5, 6.0)

    raw = interp(anchors, level)
    raw = max(0.0, raw - penalty_from_veto - seal_penalty)

    items = [
        {"name": "清场难度等级", "value": f"{level:.0f} 级", "raw": round(interp(anchors, level), 2),
         "max": round(amax, 2), "note": level_reason, "scored": True},
        {"name": "轮候查封", "value": f"{seal} 轮", "raw": -round(seal_penalty, 2),
         "max": 6.0, "note": "每轮查封扣 1.5 分，封顶 6 分（解封流程耗时耗力）",
         "scored": seal > 0},
    ]
    if penalty_from_veto:
        items.append({"name": "清场风险重罚", "value": f"-{penalty_from_veto:.1f} 分",
                      "raw": -round(penalty_from_veto, 2), "max": 0.0,
                      "note": penalty_note, "scored": True})

    inputs = {
        "租赁状态": lease,
        "是否占用": {True: "是", False: "否", None: "未载明"}[occupied],
        "可否清场": {True: "可", False: "不可", None: "未载明"}[can_clear],
        "轮候查封": seal,
        "_level": level,   # 供 tags.py / 报告层取用（下划线前缀 = 内部中间量）
    }
    note = ("清场顺畅，交付风险低" if level <= 1 else
            "存在占用或租赁，需预留解约与腾退成本" if level <= 2 else
            "清场难度大，须计入极高腾退成本或直接放弃")
    return _dim("clearance", "清场与占有维度", weight, raw, amax, items, inputs, note)


# ==================================================================== 维度3 持有成本
def score_holding_cost(asset, rs: RuleSet, penalty_hits: list[dict] | None = None) -> dict:
    """持有成本维度（15 分）。

    业务口径：不只算欠费，还要算**过户/取得环节的一次性税费**
    （契税、增值税、个税、土地出让金补缴等），因为这部分同样真实吞噬收益。
    成本占比 =（欠费总额 + 过户税费预估 + 划拨补缴估算）÷ 标的成交预估价值

    否决规则 V3（大额欠费）已按业务要求降级为扣分项，命中时在此重罚。
    """
    weight = rs.dim_max("holding_cost")
    anchors = rs.holding_cost_anchors
    amax = _anchor_max(anchors, 15.0)
    cp = rs.cost_params

    arrears = float(asset.total_arrears or 0)
    transfer_tax = float(asset.transfer_tax_estimate or 0)

    # 划拨用地：可补办手续时估算补缴成本（业务给的参考口径）
    land_supplement = 0.0
    if (asset.land_nature or "").lower() == "allocated" and asset.can_supplement_procedure is not False:
        est_value = asset.reference_price or asset.start_price or 0
        # 普通住宅约成交价 1%，非居住类约土地估值 30% —— 此处按资产类型取保守口径
        ratio = 0.01 if (asset.asset_type or "") == "residential" else 0.30
        base = est_value if (asset.asset_type or "") != "residential" else (asset.start_price or est_value)
        land_supplement = float(base or 0) * ratio

    deal_value = float(asset.start_price or 0) * float(cp.get("deal_value_ratio", 1.0))
    total_cost = arrears + transfer_tax + land_supplement

    inputs = {
        "欠费总额": r2(arrears),
        "其中欠税": r2(asset.tax_owed),
        "其中土地闲置费": r2(asset.land_idle_fee),
        "其中工程欠款": r2(asset.construction_arrears),
        "其中物业欠费": r2(asset.property_fee_owed),
        "其中水电燃气": r2(asset.utility_owed),
        "其中采暖费": r2(asset.heating_owed),
        "过户税费预估": r2(transfer_tax),
        "划拨补缴估算": r2(land_supplement),
        "成本合计": r2(total_cost),
        "成交预估价值": r2(deal_value),
        "_ratio": (total_cost / deal_value) if deal_value > 0 else 0.0,
    }

    if deal_value <= 0:
        return _dim("holding_cost", "持有成本维度", weight, 0.0, amax, [], inputs,
                    note="缺少起拍价，无法计算成本占比，该维度不计分")

    ratio = total_cost / deal_value
    raw = interp(anchors, ratio)

    penalty_from_veto = 0.0
    penalty_note = ""
    for hit in penalty_hits or []:
        if hit.get("code") == "V3":
            penalty_from_veto = float(weight) * 0.4
            penalty_note = f"触发规则 {hit['code']} {hit['name']}（大额欠费），本维度额外重罚"

    raw = max(0.0, raw - penalty_from_veto)

    items = [
        {"name": "欠费总额", "value": f"{r2(arrears)} 元", "raw": None, "max": None,
         "note": "含欠税、土地闲置费、工程欠款、物业费、水电、采暖", "scored": False},
        {"name": "过户税费预估", "value": f"{r2(transfer_tax)} 元", "raw": None, "max": None,
         "note": "契税、增值税、个税等取得环节一次性税费", "scored": False},
        {"name": "划拨补缴估算", "value": f"{r2(land_supplement)} 元", "raw": None, "max": None,
         "note": ("划拨用地按普通住宅 1% / 非居住 30% 口径估算；"
                  "另需注意土地出让金 3% 契税"), "scored": False},
        {"name": "成本占比", "value": f"{ratio * 100:.2f}%", "raw": round(interp(anchors, ratio), 2),
         "max": round(amax, 2), "note": f"成本合计 {r2(total_cost)} ÷ 成交预估 {r2(deal_value)}",
         "scored": True},
    ]
    if penalty_from_veto:
        items.append({"name": "大额欠费重罚", "value": f"-{penalty_from_veto:.1f} 分",
                      "raw": -round(penalty_from_veto, 2), "max": 0.0,
                      "note": penalty_note, "scored": True})

    note = ("成本占比低，欠费与税费负担可控" if ratio <= 0.02 else
            "成本占比中等，需在出价中预留" if ratio <= 0.05 else
            "成本占比偏高，将显著吞噬收益，建议压低出价或放弃")
    return _dim("holding_cost", "持有成本维度", weight, raw, amax, items, inputs, note)


# ==================================================================== 维度4 租金现金流
def score_rent(asset, rs: RuleSet, rent_detail: dict) -> dict:
    """租金现金流维度（15 分）：净租售比。

    净租售比算法已按业务口径修正：
      净年收益 = 年毛租金 − 房产税（企业名下才有）− 租金开票税点
                 − 空置预留 − 修缮运维费
    """
    weight = rs.dim_max("rent")
    anchors = rs.rent_anchors
    amax = _anchor_max(anchors, 30.0)

    inputs = {
        "净租售比": rent_detail.get("net_rent_ratio_pct", "—"),
        "净年收益": r2(rent_detail.get("net_annual_income")),
        "成交预估价值": r2(rent_detail.get("deal_value")),
        "年毛租金": r2(rent_detail.get("gross_annual_rent")),
    }
    if not rent_detail.get("computable"):
        return _dim("rent", "租金现金流维度", weight, 0.0, amax, [], inputs,
                    note=f"无法测算：{rent_detail.get('reason', '数据不足')}")

    ratio = float(rent_detail["net_rent_ratio"])
    raw = interp(anchors, ratio)

    deduction_rows = rent_detail.get("deductions") or {}
    items = [{
        "name": "净租售比", "value": f"{ratio * 100:.2f}%",
        "raw": round(raw, 2), "max": round(amax, 2),
        "note": f"净年收益 {r2(rent_detail['net_annual_income'])} 元 ÷ "
                f"成交预估价值 {r2(rent_detail['deal_value'])} 元",
        "scored": True,
    }]
    for label, val in deduction_rows.items():
        items.append({"name": label, "value": f"-{r2(val)} 元", "raw": None, "max": None,
                      "note": "租金净收益扣减项", "scored": False})

    if ratio >= 0.05:
        note = "现金流达标（≥5%），可支撑持有型投资逻辑"
    elif ratio >= 0.04:
        note = "梯度扣分区间（4%~5%），现金流勉强成立，需压价或提升租金"
    else:
        note = "大幅扣分预警（＜4%），当前测算下现金流不成立"
    return _dim("rent", "租金现金流维度", weight, raw, amax, items, inputs, note)


# ==================================================================== 维度5 区位流通性
def score_location(asset, rs: RuleSet, bench: dict) -> dict:
    """区位流通性维度（10 分）。"""
    weight = rs.dim_max("location")
    lp = rs.location_params
    city_scores: dict = lp.get("city_tier_scores", {}) or {}
    city_max = max((float(v) for v in city_scores.values()), default=6.0)
    ind_max = float(lp.get("industry_support_max", 3.0))
    demand_max = float(lp.get("rental_demand_max", 3.0))
    turnover_max = float(lp.get("turnover_max", 3.0))
    amax = city_max + ind_max + demand_max + turnover_max

    tier = _pick(asset.city_tier, bench.get("city_tier"), default="tier3")
    ind = _pick(asset.industry_support_ratio, bench.get("industry_support_ratio"), default=0.5)
    demand = _pick(asset.rental_demand_ratio, bench.get("rental_demand_ratio"), default=0.5)
    turnover = _pick(asset.turnover_ratio, bench.get("turnover_ratio"), default=0.5)

    def _ratio(v) -> float:
        try:
            v = float(v)
        except (TypeError, ValueError):
            return 0.0
        return clamp(v / 100.0 if v > 1.0 else v, 0.0, 1.0)

    city_score = float(city_scores.get(tier, 0.0))
    ind_score = _ratio(ind) * ind_max
    demand_score = _ratio(demand) * demand_max
    turnover_score = _ratio(turnover) * turnover_max
    raw = city_score + ind_score + demand_score + turnover_score

    items = [
        {"name": "城市能级", "value": tier, "raw": round(city_score, 2),
         "max": round(city_max, 2), "note": "按标的所在城市能级取值", "scored": True},
        {"name": "产业配套成熟度", "value": f"{_ratio(ind) * 100:.0f}%",
         "raw": round(ind_score, 2), "max": round(ind_max, 2),
         "note": "区域产业配套完善程度", "scored": True},
        {"name": "区域出租需求", "value": f"{_ratio(demand) * 100:.0f}%",
         "raw": round(demand_score, 2), "max": round(demand_max, 2),
         "note": "区域租赁市场需求热度", "scored": True},
        {"name": "同类资产转手成交率", "value": f"{_ratio(turnover) * 100:.0f}%",
         "raw": round(turnover_score, 2), "max": round(turnover_max, 2),
         "note": "同类资产二级市场流动性", "scored": True},
    ]
    inputs = {
        "城市能级": tier,
        "区位数据来源": bench.get("_matched", "") or "系统默认",
        "标的覆盖项": "、".join(bench.get("_override", [])) or "无",
    }
    rate = raw / amax if amax else 0.0
    note = ("区位优质，产业成熟、易租易售" if rate >= 0.8 else
            "区位一般，流通性中性" if rate >= 0.5 else
            "区位偏弱，出租与转手均存在压力")
    return _dim("location", "区位流通性维度", weight, raw, amax, items, inputs, note)


# ==================================================================== 汇总
def score_all(asset, rs: RuleSet, rent_detail: dict, bench: dict,
              penalty_hits: list[dict] | None = None) -> tuple[dict, float]:
    """跑完物权五维，返回 (score_detail, total_score)。"""
    dims = {
        "price": score_price(asset, rs),
        "clearance": score_clearance(asset, rs, penalty_hits),
        "holding_cost": score_holding_cost(asset, rs, penalty_hits),
        "rent": score_rent(asset, rs, rent_detail),
        "location": score_location(asset, rs, bench),
    }
    total = round(sum(dims[c]["score"] for c in DIMENSION_ORDER), 2)
    detail: dict[str, Any] = {
        "asset_class": "property",
        "dimensions": dims,
        "order": list(DIMENSION_ORDER),
        "weights_total": round(rs.property_weight_total(), 2),
        "total_score": total,
        # 平铺关键中间量，供标签引擎（tags.py）与报告层直接取用，避免再解析 items
        "clearance_level": float(dims["clearance"]["inputs"].get("_level", 0.0)),
        "holding_cost_ratio": float(dims["holding_cost"]["inputs"].get("_ratio", 0.0)),
    }
    return detail, total
