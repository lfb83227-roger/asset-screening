"""模块3：五维量化评分引擎（总分 100 分）。

五个维度（PRD 模块3）：
  price     价格折价维度        30 分
  rent      净租售比现金流维度  30 分（核心）
  legal     产权司法风险维度    20 分
  location  区位流通性维度      15 分
  ownership 权属合规补充维度     5 分

统一约定：
* 每个维度先算 `raw`（按锚点表/扣分系数得到的原始分），再按
  `score = raw / anchor_max × 当前权重` 折算 —— 这样后台调权重时，
  各子项的相对关系保持稳定，不会因为改权重而改变策略含义。
* 所有打分都走 `ruleset.interp` 或显式系数，无随机、无隐式状态。
"""
from __future__ import annotations

from typing import Any

from app.core.ruleset import RuleSet, clamp, interp, r2

# 扣分系数的分制基准（与出厂权重一致）。后台调整 legal 权重时按比例折算。
LEGAL_BASE_SCORE = 20.0


# ==================================================================== 工具
def _dim(code: str, label: str, weight: float, raw: float,
         anchor_max: float, items: list[dict],
         inputs: dict | None = None, note: str = "") -> dict:
    score = 0.0 if anchor_max <= 0 else round(raw / anchor_max * weight, 2)
    return {
        "code": code,
        "label": label,
        "weight": round(weight, 2),        # 该维度当前满分
        "raw": round(raw, 2),              # 原始分
        "anchor_max": round(anchor_max, 2),
        "score": score,
        "rate": round(score / weight, 4) if weight else 0.0,   # 得分率
        "items": items,
        "inputs": inputs or {},
        "note": note,
    }


def _pick(*values, default=None):
    """取第一个非空值。"""
    for v in values:
        if v not in (None, "", []):
            return v
    return default


def _anchor_max(anchors: list[list[float]], default: float) -> float:
    return max((float(p[1]) for p in anchors), default=default) or default


# ==================================================================== 维度1 价格折价
def score_price(asset, rs: RuleSet) -> dict:
    weight = rs.dim_max("price")
    anchors = rs.price_anchors
    amax = _anchor_max(anchors, 30.0)

    ref = asset.reference_price
    start = asset.start_price
    ref_src = ("周边同类型资产成交价" if asset.market_price
               else ("评估价" if asset.appraisal_price else "无"))

    inputs = {
        "起拍价": r2(start),
        "评估价": r2(asset.appraisal_price),
        "周边同类成交价": r2(asset.market_price),
        "参考价取值": ref_src,
    }
    if asset.market_price:
        inputs["折价率(对市场价)"] = (f"{asset.discount_rate * 100:.2f}%"
                                     if asset.discount_rate is not None else "—")
    if asset.appraisal_price and start:
        inputs["折价率(对评估价)"] = f"{(asset.appraisal_price - start) / asset.appraisal_price * 100:.2f}%"

    if not start or not ref or ref <= 0:
        return _dim("price", "价格折价维度", weight, 0.0, amax, [], inputs,
                    note="缺少起拍价或参考价，该维度不计分，需补充数据后重算")

    discount = (ref - start) / ref
    raw = interp(anchors, discount)
    items = [{
        "name": "起拍价折价率",
        "value": f"{discount * 100:.2f}%",
        "raw": round(raw, 2),
        "max": round(amax, 2),
        "note": f"（参考价 {r2(ref)} 元 − 起拍价 {r2(start)} 元）÷ 参考价；"
                f"参考价来源：{ref_src}",
        "scored": True,
    }]
    note = ("折价充分，安全垫充足" if discount >= 0.30 else
            "折价一般" if discount >= 0.10 else
            "折价不足，存在高价接盘风险")
    return _dim("price", "价格折价维度", weight, raw, amax, items, inputs, note)


# ==================================================================== 维度2 净租售比
def score_rent(asset, rs: RuleSet, rent_detail: dict) -> dict:
    weight = rs.dim_max("rent")
    anchors = rs.rent_anchors
    amax = _anchor_max(anchors, 30.0)

    inputs = {
        "净租售比": rent_detail.get("net_rent_ratio_pct", "—"),
        "净年收益": r2(rent_detail.get("net_annual_income")),
        "成交预估价值": r2(rent_detail.get("deal_value")),
    }
    if not rent_detail.get("computable"):
        return _dim("rent", "净租售比现金流维度", weight, 0.0, amax, [], inputs,
                    note=f"无法测算：{rent_detail.get('reason', '数据不足')}")

    ratio = float(rent_detail["net_rent_ratio"])
    raw = interp(anchors, ratio)
    items = [{
        "name": "净租售比",
        "value": f"{ratio * 100:.2f}%",
        "raw": round(raw, 2),
        "max": round(amax, 2),
        "note": f"净年收益 {r2(rent_detail['net_annual_income'])} 元 ÷ "
                f"成交预估价值 {r2(rent_detail['deal_value'])} 元",
        "scored": True,
    }]
    if ratio >= 0.05:
        note = "现金流达标（≥5%），可支撑持有型投资逻辑"
    elif ratio >= 0.04:
        note = "梯度扣分区间（4%~5%），现金流勉强成立，需压价或提升租金"
    else:
        note = "大幅扣分预警（＜4%），当前测算下现金流不成立，建议直接淘汰或大幅压价"
    return _dim("rent", "净租售比现金流维度", weight, raw, amax, items, inputs, note)


# ==================================================================== 维度3 产权司法风险
def score_legal(asset, rs: RuleSet) -> dict:
    weight = rs.dim_max("legal")
    p = rs.legal_penalty
    base = LEGAL_BASE_SCORE

    components = [
        ("mortgage", "抵押数量", int(asset.mortgage_count or 0),
         float(p.get("mortgage_per", 2.0)), float(p.get("mortgage_cap", 6.0))),
        ("seal", "轮候查封数量", int(asset.seal_count or 0),
         float(p.get("seal_per", 4.0)), float(p.get("seal_cap", 12.0))),
        ("lawsuit", "涉诉案件数量", int(asset.lawsuit_count or 0),
         float(p.get("lawsuit_per", 1.5)), float(p.get("lawsuit_cap", 6.0))),
        ("dispute", "司法纠纷频次", int(asset.dispute_freq or 0),
         float(p.get("dispute_per", 1.0)), float(p.get("dispute_cap", 3.0))),
    ]

    items, total_penalty = [], 0.0
    for _key, label, count, per, cap in components:
        pen = min(count * per, cap)
        total_penalty += pen
        items.append({
            "name": label,
            "value": f"{count} 项",
            "raw": -round(pen, 2),
            "max": round(cap, 2),
            "note": f"每项扣 {per} 分，本项封顶 {cap} 分",
            "scored": True,
        })

    raw = clamp(base - total_penalty, 0.0, base)
    inputs = {
        "抵押数量": int(asset.mortgage_count or 0),
        "轮候查封": int(asset.seal_count or 0),
        "涉诉案件": int(asset.lawsuit_count or 0),
        "纠纷频次": int(asset.dispute_freq or 0),
        "累计扣分": round(total_penalty, 2),
    }
    if total_penalty == 0:
        note = "无抵押、无查封、无涉诉，产权司法状态干净，满分"
    elif raw <= 0:
        note = "司法风险扣分已触及下限，产权瑕疵严重"
    else:
        note = f"累计扣 {round(total_penalty, 2)} 分，风险项越多分数越低"
    return _dim("legal", "产权司法风险维度", weight, raw, base, items, inputs, note)


# ==================================================================== 维度4 区位流通性
def score_location(asset, rs: RuleSet, bench: dict) -> dict:
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


# ==================================================================== 维度5 权属合规补充
def score_ownership(asset, rs: RuleSet) -> dict:
    weight = rs.dim_max("ownership")
    op = rs.ownership_params
    land_max = float(op.get("land_years_max", 2.0))
    implicit_score = float(op.get("no_implicit_coownership_score", 1.5))
    compliance_scores: dict = op.get("compliance_scores", {}) or {}
    comp_max = max((float(v) for v in compliance_scores.values()), default=1.5)
    amax = land_max + implicit_score + comp_max

    years = asset.land_remaining_years
    if years is None:
        land_score = 0.0
        land_note = "未载明土地剩余年限，从严按 0 分计（补充数据后可重算）"
        land_value = "未载明"
    else:
        land_score = clamp(interp(rs.land_year_anchors, float(years)), 0.0, land_max)
        land_note = "按剩余年限锚点表取值"
        land_value = f"{r2(years)} 年"

    if asset.implicit_coownership is True:
        own_score = 0.0
        own_note = "公告存在隐性共有产权提示，本子项不计分"
    else:
        own_score = implicit_score
        own_note = "未发现隐性共有产权提示"

    level = asset.compliance_level or "full"
    comp_score = float(compliance_scores.get(level, 0.0))
    comp_note = {
        "full": "证载用途与现状一致、手续齐全",
        "partial": "存在轻度不一致但可补正",
        "none": "手续缺失 / 无法补正",
    }.get(level, "")

    raw = land_score + own_score + comp_score
    items = [
        {"name": "土地剩余年限", "value": land_value, "raw": round(land_score, 2),
         "max": round(land_max, 2), "note": land_note, "scored": True},
        {"name": "隐性共有产权提示", "value": "有" if asset.implicit_coownership else "无",
         "raw": round(own_score, 2), "max": round(implicit_score, 2),
         "note": own_note, "scored": True},
        {"name": "资产合规性", "value": level, "raw": round(comp_score, 2),
         "max": round(comp_max, 2), "note": comp_note, "scored": True},
    ]
    inputs = {"土地性质": asset.land_nature, "合规等级": level}
    return _dim("ownership", "权属合规补充维度", weight, raw, amax, items, inputs,
                note="权属合规补充项，用于区分同分标的的合规成色")


# ==================================================================== 汇总
DIMENSION_ORDER = ("price", "rent", "legal", "location", "ownership")


def score_all(asset, rs: RuleSet, rent_detail: dict, bench: dict) -> tuple[dict, float]:
    """跑完五个维度，返回 (score_detail, total_score)。"""
    dims = {
        "price": score_price(asset, rs),
        "rent": score_rent(asset, rs, rent_detail),
        "legal": score_legal(asset, rs),
        "location": score_location(asset, rs, bench),
        "ownership": score_ownership(asset, rs),
    }
    total = round(sum(dims[c]["score"] for c in DIMENSION_ORDER), 2)
    detail: dict[str, Any] = {
        "dimensions": dims,
        "order": list(DIMENSION_ORDER),
        "weights_total": round(rs.total_weight(), 2),
        "total_score": total,
    }
    return detail, total


def grade_of(total: float, rs: RuleSet) -> str:
    """模块4：A ≥80 / B 60-79 / C <60（阈值可后台配置）。"""
    a, b = rs.grade_thresholds["A"], rs.grade_thresholds["B"]
    if total >= a:
        return "A"
    if total >= b:
        return "B"
    return "C"
