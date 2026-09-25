"""区域大数据基准解析（PRD 四-2：租金、税费调用对应区域大数据均值）。

查找链（逐级降级，任何一级命中即停）：
  1. 城市 + 区县 + 资产类型   （精确）
  2. 城市 + 资产类型          （同城其他区县均值）
  3. 出厂默认系数             （系统兜底，结果中会明确标注来源）
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ruleset import RuleSet, r2
from app.models import RegionBenchmark

# 区域基准中可直接透传的数值型字段
_NUM_FIELDS = (
    "rent_per_sqm_month", "land_use_tax_per_sqm", "property_tax_rate",
    "vacancy_ratio", "maintenance_ratio", "city_tier",
    "industry_support_ratio", "rental_demand_ratio", "turnover_ratio",
)


def _row_to_dict(row: RegionBenchmark) -> dict:
    return {f: getattr(row, f) for f in _NUM_FIELDS}


def resolve_benchmark(db: Session, asset, ruleset: RuleSet) -> dict:
    """解析适用于该标的的区域基准，返回字段完备的 dict。"""
    cost = ruleset.cost_params

    # 兜底：系统默认系数
    bench: dict = {
        "rent_per_sqm_month": 0.0,
        "land_use_tax_per_sqm": float(cost.get("land_use_tax_per_sqm", 6.0)),
        "property_tax_rate": float(cost.get("property_tax_rate", 0.12)),
        "vacancy_ratio": float(cost.get("vacancy_ratio", 0.10)),
        "maintenance_ratio": float(cost.get("maintenance_ratio", 0.05)),
        "city_tier": asset.city_tier or "tier3",
        "industry_support_ratio": None,
        "rental_demand_ratio": None,
        "turnover_ratio": None,
        "_source": "fallback",
        "_matched": "系统默认系数（未匹配到区域基准）",
    }

    if not asset.city:
        return bench

    rows = db.execute(
        select(RegionBenchmark).where(
            RegionBenchmark.city == asset.city,
            RegionBenchmark.asset_type == asset.asset_type,
        )
    ).scalars().all()

    if not rows:
        # 退一步：同城任意资产类型中，取住宅/商业作为城市能级的来源
        rows = db.execute(
            select(RegionBenchmark).where(RegionBenchmark.city == asset.city)
        ).scalars().all()

    if not rows:
        return bench

    picked = None
    source = "city"
    if asset.district:
        for row in rows:
            if row.district and row.district == asset.district and \
                    row.asset_type == asset.asset_type:
                picked, source = row, "exact"
                break
    if picked is None and rows:
        # 优先同资产类型，其次任意（用于获取城市能级与流动性）
        same_type = [r for r in rows if r.asset_type == asset.asset_type]
        picked = (same_type or rows)[0]

    data = _row_to_dict(picked)
    for k, v in data.items():
        if v is not None:
            bench[k] = v
    bench["_source"] = source
    bench["_matched"] = " / ".join(
        x for x in (picked.province, picked.city, picked.district,
                    picked.asset_type) if x
    )
    return bench


def push_down_benchmark(bench: dict, asset) -> dict:
    """标的自身录入的区位指标优先于区域均值（人工录入更准）。"""
    out = dict(bench)
    for field in ("city_tier", "industry_support_ratio",
                  "rental_demand_ratio", "turnover_ratio"):
        manual = getattr(asset, field, None)
        if manual not in (None, "", 0):
            out[field] = manual
            out.setdefault("_override", []).append(field)
    return out


def list_benchmarks(db: Session, keyword: str | None = None) -> list[RegionBenchmark]:
    stmt = select(RegionBenchmark).order_by(
        RegionBenchmark.city, RegionBenchmark.district, RegionBenchmark.asset_type)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            RegionBenchmark.city.like(like) | RegionBenchmark.district.like(like))
    return list(db.execute(stmt).scalars().all())


def benchmark_display(bench: dict) -> dict:
    """给模板/PDF 用的可读版本。"""
    src_map = {"exact": "区县级精确匹配", "city": "同城均值", "fallback": "系统默认系数"}
    return {
        "匹配来源": src_map.get(bench.get("_source"), bench.get("_source", "")),
        "匹配对象": bench.get("_matched", ""),
        "区域租金": f"{r2(bench.get('rent_per_sqm_month'), 2)} 元/㎡/月",
        "土地使用税": f"{r2(bench.get('land_use_tax_per_sqm'), 2)} 元/㎡/年",
        "房产税率": f"{(bench.get('property_tax_rate') or 0) * 100:.2f}%",
        "空置损耗预留": f"{(bench.get('vacancy_ratio') or 0) * 100:.1f}%",
        "修缮运维费率": f"{(bench.get('maintenance_ratio') or 0) * 100:.1f}%",
        "城市能级": bench.get("city_tier", ""),
    }
