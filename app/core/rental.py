"""净租售比测算（PRD 四：核心公式，固定写入系统）。

净租售比 =（预估年毛租金 − 房产税 − 租金开票税点 − 土地使用税
            − 年度修缮运维费 − 空置损耗预留）
           ÷ 标的成交预估价值 × 100%

规则要点（业务确认口径）：
* 房产税（从租计征）**仅企业名下**才需缴纳 —— 个人名下住宅出租免征从租房产税，
  故按 `owner_is_company` 开关决定是否扣减，避免对个人标的误扣导致收益被低估；
* 租金开票税点（增值税及附加）按 `rent_vat_rate` 扣减；
* 修缮运维、空置损耗统一预留（后台可调）；
* 税费、租金调用区域大数据均值（见 benchmark.py）；
* 所有参数后台可调，适配不同城市、不同资产类型。
"""
from __future__ import annotations

from app.core.ruleset import RuleSet, r2


def compute_rent(asset, ruleset: RuleSet, bench: dict) -> dict:
    """返回测算明细 dict；任何关键输入缺失时 `computable=False` 并说明原因。"""
    cost = ruleset.cost_params
    detail: dict = {
        "formula": "净租售比 =（预估年毛租金 − 房产税 − 租金开票税点 − 土地使用税 "
                   "− 年度修缮运维费 − 空置损耗预留） ÷ 标的成交预估价值 × 100%",
        "computable": False,
        "reason": "",
        "lines": [],
        "benchmark_matched": bench.get("_matched", ""),
        "benchmark_source": bench.get("_source", ""),
    }

    # ---------------------------------------------------------- 年毛租金
    gross = asset.annual_gross_rent_override
    if gross:
        rent_source = "人工指定年毛租金"
        rent_unit = None
    else:
        area = asset.area_sqm or asset.land_area_sqm
        if not area:
            detail["reason"] = "缺少面积数据，无法测算租金"
            return detail
        unit_rent = asset.rent_per_sqm_month or bench.get("rent_per_sqm_month") or 0.0
        if not unit_rent:
            unit_rent = float(
                ruleset.default_rent_per_sqm_month.get(asset.asset_type, 0.0))
        if not unit_rent:
            detail["reason"] = "缺少租金数据且无区域基准，无法测算租金"
            return detail
        gross = float(unit_rent) * float(area) * 12.0
        rent_source = ("标的自填租金" if asset.rent_per_sqm_month
                       else f"区域大数据均值（{bench.get('_matched', '系统默认')}）")
        rent_unit = f"{r2(unit_rent)} 元/㎡/月 × {r2(area)} ㎡ × 12"

    # ---------------------------------------------------------- 成本项
    property_tax_rate = float(bench.get("property_tax_rate")
                              or cost.get("property_tax_rate", 0.12))
    rent_vat_rate = float(bench.get("rent_vat_rate")
                          or cost.get("rent_vat_rate", 0.05))
    maintenance_ratio = float(bench.get("maintenance_ratio")
                              or cost.get("maintenance_ratio", 0.05))
    vacancy_ratio = float(bench.get("vacancy_ratio")
                          or cost.get("vacancy_ratio", 0.10))
    land_tax_unit = float(bench.get("land_use_tax_per_sqm")
                          or cost.get("land_use_tax_per_sqm", 6.0))
    land_tax_area = asset.land_area_sqm or asset.area_sqm or 0.0

    # 房产税（从租计征）仅企业名下需缴；个人名下住宅出租免征从租房产税。
    # owner_is_company 为 None（未载明）时按企业名下的**从严**口径扣减，
    # 避免"没填就当免税"导致收益被系统性高估。
    owner_is_company = getattr(asset, "owner_is_company", None)
    property_tax_applies = owner_is_company is not False
    annual_property_tax = gross * property_tax_rate if property_tax_applies else 0.0
    annual_rent_vat = gross * rent_vat_rate

    annual_land_tax = land_tax_unit * float(land_tax_area)
    annual_tax = annual_property_tax + annual_rent_vat + annual_land_tax
    annual_maintenance = gross * maintenance_ratio
    vacancy_reserve = gross * vacancy_ratio
    net_income = gross - annual_tax - annual_maintenance - vacancy_reserve

    # ---------------------------------------------------------- 成交预估价值
    deal_ratio = float(cost.get("deal_value_ratio", 1.0))
    deal_value = None
    if asset.start_price:
        deal_value = float(asset.start_price) * deal_ratio
        deal_basis = f"起拍价 {r2(asset.start_price)} 元 × 系数 {deal_ratio}"
    elif asset.appraisal_price:
        deal_value = float(asset.appraisal_price) * deal_ratio
        deal_basis = f"评估价 {r2(asset.appraisal_price)} 元 × 系数 {deal_ratio}"

    if not deal_value or deal_value <= 0:
        detail["reason"] = "缺少起拍价/评估价，无法确定成交预估价值"
        detail.update({
            "gross_annual_rent": r2(gross),
            "annual_tax": r2(annual_tax),
            "annual_maintenance": r2(annual_maintenance),
            "vacancy_reserve": r2(vacancy_reserve),
            "net_annual_income": r2(net_income),
        })
        return detail

    ratio = net_income / deal_value

    # ---------------------------------------------------------- 明细
    detail.update({
        "computable": True,
        "rent_source": rent_source,
        "rent_unit_calc": rent_unit,
        "area_sqm": r2(asset.area_sqm or asset.land_area_sqm),
        "gross_annual_rent": r2(gross),
        "annual_property_tax": r2(annual_property_tax),
        "annual_rent_vat": r2(annual_rent_vat),
        "annual_land_tax": r2(annual_land_tax),
        "annual_tax": r2(annual_tax),
        "annual_maintenance": r2(annual_maintenance),
        "vacancy_reserve": r2(vacancy_reserve),
        "net_annual_income": r2(net_income),
        "deal_value": r2(deal_value),
        "deal_basis": deal_basis,
        "net_rent_ratio": round(ratio, 6),
        "net_rent_ratio_pct": f"{ratio * 100:.2f}%",
        "deductions": {
            "房产税": annual_property_tax,
            "租金开票税点": annual_rent_vat,
            "土地使用税": annual_land_tax,
            "修缮运维费": annual_maintenance,
            "空置损耗预留": vacancy_reserve,
        },
        "params_used": {
            "property_tax_rate": property_tax_rate,
            "rent_vat_rate": rent_vat_rate,
            "property_tax_applies": property_tax_applies,
            "land_use_tax_per_sqm": land_tax_unit,
            "land_tax_area": r2(land_tax_area),
            "maintenance_ratio": maintenance_ratio,
            "vacancy_ratio": vacancy_ratio,
            "deal_value_ratio": deal_ratio,
        },
    })

    detail["lines"] = [
        {"name": "预估年毛租金", "amount": r2(gross), "sign": "+",
         "note": rent_source + (f"（{rent_unit}）" if rent_unit else "")},
        {"name": "年度房产税", "amount": r2(annual_property_tax), "sign": "-",
         "note": (f"年毛租金 × {property_tax_rate * 100:.1f}%（从租计征，企业名下）"
                  if property_tax_applies else "个人名下，从租房产税免征，不计扣")},
        {"name": "租金开票税点", "amount": r2(annual_rent_vat), "sign": "-",
         "note": f"年毛租金 × {rent_vat_rate * 100:.1f}%（增值税及附加）"},
        {"name": "年度土地使用税", "amount": r2(annual_land_tax), "sign": "-",
         "note": f"{r2(land_tax_unit)} 元/㎡/年 × {r2(land_tax_area)} ㎡"},
        {"name": "年度修缮运维费", "amount": r2(annual_maintenance), "sign": "-",
         "note": f"年毛租金 × {maintenance_ratio * 100:.1f}%"},
        {"name": "空置损耗预留", "amount": r2(vacancy_reserve), "sign": "-",
         "note": f"年毛租金 × {vacancy_ratio * 100:.1f}%"},
        {"name": "净年收益", "amount": r2(net_income), "sign": "=", "note": ""},
        {"name": "成交预估价值", "amount": r2(deal_value), "sign": "÷", "note": deal_basis},
    ]
    return detail
