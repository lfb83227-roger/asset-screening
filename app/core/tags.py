"""模块5：风险与优势标签自动生成。

实现方式：**确定性谓词**（不是关键词碰运气），每个标签的触发条件都能说清楚、
能验收、能复现。关键词扫描放在一票否决模块里做，这里做的是量化判定。
阈值全部来自规则配置，后台可调；每个标签也能单独开关。
"""
from __future__ import annotations

from app.core import constants as C
from app.core.ruleset import RuleSet


def _num(v, default=0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def evaluate_tags(asset, rs: RuleSet, score_detail: dict,
                  rent_detail: dict) -> tuple[list[dict], list[dict]]:
    """返回 (优势标签, 风险标签)。每个标签是 TAG_REGISTRY 中的元数据副本。"""
    t = rs.tag_thresholds
    enabled = rs.tag_enabled or {}

    dims = (score_detail or {}).get("dimensions", {}) or {}
    location_rate = _num((dims.get("location") or {}).get("rate"), 0.0)
    legal_rate = _num((dims.get("legal") or {}).get("rate"), 0.0)

    discount = asset.discount_rate
    ratio = rent_detail.get("net_rent_ratio") if rent_detail else None
    ratio = _num(ratio) if ratio is not None else None
    days_left = asset.deadline_days_left
    turnover = asset.turnover_ratio

    advantages: list[dict] = []
    risks: list[dict] = []

    def add(code: str, hit: bool, detail: str = "") -> None:
        if not hit:
            return
        meta = C.TAG_BY_CODE.get(code)
        if not meta or not enabled.get(code, True):
            return
        item = dict(meta)
        if detail:
            item["detail"] = detail
        (advantages if meta["kind"] == "advantage" else risks).append(item)

    # ------------------------------------------------------------ 优势标签
    add("high_discount", discount is not None and discount >= _num(t.get("high_discount"), .30),
        f"折价率 {discount * 100:.1f}%" if discount is not None else "")
    add("rent_pass", ratio is not None and ratio >= _num(t.get("rent_pass"), .05),
        f"净租售比 {ratio * 100:.2f}%" if ratio is not None else "")
    add("clean_judicial",
        _num(asset.mortgage_count) == 0 and _num(asset.seal_count) == 0,
        "无抵押、无轮候查封")
    add("clean_title",
        asset.land_nature not in ("collective", "allocated")
        and asset.registration_ok is not False
        and not bool(asset.transfer_restricted),
        f"土地性质：{C.LAND_NATURES.get(asset.land_nature, asset.land_nature)}")
    add("good_location", location_rate >= 0.8,
        f"区位得分率 {location_rate * 100:.0f}%")
    add("granted_land", asset.land_nature == "granted", "出让用地")
    add("no_lease", asset.lease_status == "none", "公告载明无租赁")
    add("vacant", asset.occupied is False, "公告载明空置可交付")
    add("low_judicial_risk", legal_rate >= 0.8, f"司法风险得分率 {legal_rate * 100:.0f}%")

    # ------------------------------------------------------------ 风险标签
    add("has_lease", asset.lease_status in ("normal", "long_term", "sale_not_break"),
        C.LEASE_STATUSES.get(asset.lease_status, ""))
    add("occupied", asset.occupied is True,
        asset.occupancy_note or "公告载明被占用")
    add("arrears", (asset.tax_owed or 0) > 0 or (asset.land_idle_fee or 0) > 0
        or (asset.construction_arrears or 0) > 0 or (asset.property_fee_owed or 0) > 0,
        f"累计欠费 {asset.total_arrears:,.0f} 元")
    add("many_seals", _num(asset.seal_count) >= _num(t.get("seal_many"), 2),
        f"轮候查封 {int(_num(asset.seal_count))} 轮")
    add("land_years_short",
        asset.land_remaining_years is not None
        and _num(asset.land_remaining_years) < _num(t.get("land_years_short"), 20),
        f"剩余 {asset.land_remaining_years} 年" if asset.land_remaining_years is not None else "")
    add("low_discount", discount is not None and discount < _num(t.get("low_discount"), .10),
        f"折价率仅 {discount * 100:.1f}%" if discount is not None else "")
    add("rent_fail", ratio is not None and ratio < _num(t.get("rent_warn"), .04),
        f"净租售比仅 {ratio * 100:.2f}%" if ratio is not None else "")
    add("many_mortgages", _num(asset.mortgage_count) >= _num(t.get("mortgage_many"), 2),
        f"抵押 {int(_num(asset.mortgage_count))} 笔")
    add("many_lawsuits", _num(asset.lawsuit_count) >= _num(t.get("lawsuit_many"), 3),
        f"涉诉 {int(_num(asset.lawsuit_count))} 件")
    add("collective_land", asset.land_nature == "collective", "集体用地")
    add("allocated_land", asset.land_nature == "allocated", "划拨用地")
    add("transfer_restricted", bool(asset.transfer_restricted), "公告载明限制转让")
    add("no_registration", asset.registration_ok is False, "无法办理不动产登记")
    add("big_arrears", asset.total_arrears >= _num(t.get("big_arrears"), 500000),
        f"欠费合计 {asset.total_arrears:,.0f} 元")
    add("coownership_dispute", bool(asset.co_ownership_dispute), "共有产权无法统一确权")
    add("irreversible_seal", bool(asset.irreversible_seal), "存在不可解除的限制性查封")
    add("scrapped", asset.scrap_status not in (None, "", "normal"),
        C.SCRAP_STATUSES.get(asset.scrap_status, ""))
    add("deadline_soon",
        days_left is not None and 0 <= days_left < _num(t.get("deadline_days"), 7),
        f"仅剩 {days_left:.1f} 天" if days_left is not None else "")
    add("poor_liquidity",
        turnover is not None and _num(turnover) < _num(t.get("turnover_poor"), .30),
        f"转手成交率 {_num(turnover) * 100:.0f}%" if turnover is not None else "")
    add("implicit_coownership", bool(asset.implicit_coownership), "存在隐性共有产权提示")
    add("procedure_incomplete", asset.compliance_level == "none", "手续缺失/无法补正")

    return advantages, risks
