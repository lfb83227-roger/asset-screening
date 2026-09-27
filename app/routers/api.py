"""JSON API —— 供外部系统直接调用评分引擎。

典型用法：业务系统把标的 JSON POST 到 /api/evaluate，
拿到五维得分、标签、分级结论，而不需要登录后台。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import ENGINE_VERSION, __version__
from app.core import constants as C
from app.core.benchmark import benchmark_display, push_down_benchmark, resolve_benchmark
from app.core.pipeline import evaluate
from app.core.ruleset import load_ruleset
from app.database import get_db
from app.models import APPRAISAL_STALE_MONTHS, Asset

router = APIRouter(prefix="/api", tags=["api"])


# ==================================================================== 健康检查
@router.get("/health")
def health(db: Session = Depends(get_db)):
    total = db.execute(select(func.count(Asset.id))).scalar_one()
    return {"status": "ok", "version": __version__, "engine_version": ENGINE_VERSION,
            "assets": total, "time": datetime.now().isoformat(timespec="seconds")}


# ==================================================================== 评分引擎
class EvaluateRequest(BaseModel):
    """标的入参。字段名与后台录入一致，全部可选；缺失项会导致对应维度不计分。"""

    title: str = ""
    asset_type: str = "other"
    source_platform: str = "manual"
    province: str | None = None
    city: str | None = None
    district: str | None = None
    address: str | None = None

    area_sqm: float | None = None
    land_area_sqm: float | None = None
    start_price: float | None = None
    appraisal_price: float | None = None
    market_price: float | None = None

    land_nature: str = "unknown"
    land_remaining_years: float | None = None
    compliance_level: str = "full"
    registration_ok: bool | None = None
    transfer_restricted: bool | None = None
    can_supplement_procedure: bool | None = None
    implicit_coownership: bool | None = None

    mortgage_count: int = 0
    seal_count: int = 0
    lawsuit_count: int = 0
    dispute_freq: int = 0
    co_ownership_dispute: bool = False
    irreversible_seal: bool = False

    lease_status: str = "unknown"
    occupied: bool | None = None
    can_clear: bool | None = None
    occupancy_note: str | None = None
    scrap_status: str = "normal"

    tax_owed: float = 0.0
    land_idle_fee: float = 0.0
    construction_arrears: float = 0.0
    property_fee_owed: float = 0.0
    utility_owed: float = 0.0
    heating_owed: float = 0.0
    transfer_tax_estimate: float = 0.0
    owner_is_company: bool | None = None

    city_tier: str | None = None
    industry_support_ratio: float | None = None
    rental_demand_ratio: float | None = None
    turnover_ratio: float | None = None
    rent_per_sqm_month: float | None = None
    annual_gross_rent_override: float | None = None

    # ---- 双轨制：物权 / 债权分流 + 评估基准 + 债权专属
    asset_class: str = "property"
    appraisal_at: datetime | None = None
    appraisal_refreshed_price: float | None = None
    market_comp_source: str | None = None
    market_comp_count: int | None = None
    debt_principal: float | None = None
    debt_interest: float | None = None
    collateral_value: float | None = None
    debt_start_price: float | None = None
    guarantee_rank: str = "unknown"
    execution_stage: str = "unknown"
    debtor_solvency: str = "unknown"
    debt_doc_level: str = "unknown"
    debt_transferable: bool | None = None
    debt_limitation_ok: bool | None = None
    competing_claims: int = 0

    raw_text: str | None = None


class _AssetStub:
    """把请求对象伪装成 Asset，复用同一个纯函数引擎 —— 保证 API 与后台结果一致。"""

    def __init__(self, payload: dict):
        self._data = payload

    def __getattr__(self, item):
        return self._data.get(item)

    @property
    def is_debt(self) -> bool:
        return (self._data.get("asset_class") or "property") == "debt"

    @property
    def total_arrears(self) -> float:
        return float(self._data.get("tax_owed") or 0) + \
            float(self._data.get("land_idle_fee") or 0) + \
            float(self._data.get("construction_arrears") or 0) + \
            float(self._data.get("property_fee_owed") or 0) + \
            float(self._data.get("utility_owed") or 0) + \
            float(self._data.get("heating_owed") or 0)

    @property
    def effective_appraisal(self):
        return (self._data.get("appraisal_refreshed_price")
                or self._data.get("appraisal_price"))

    @property
    def appraisal_age_months(self):
        at = self._data.get("appraisal_at")
        if not at:
            return None
        from datetime import datetime as _dt
        return (_dt.now() - at).total_seconds() / 86400.0 / 30.44

    @property
    def appraisal_stale(self) -> bool:
        if self.is_debt:
            return False
        months = self.appraisal_age_months
        return months is not None and months > APPRAISAL_STALE_MONTHS

    @property
    def reference_price(self):
        return self._data.get("market_price") or self.effective_appraisal

    @property
    def discount_rate(self):
        ref = self.reference_price
        start = self._data.get("start_price")
        if not ref or not start or ref <= 0:
            return None
        return (ref - start) / ref

    @property
    def discount_vs_appraisal(self):
        p = self.effective_appraisal
        start = self._data.get("start_price")
        if not p or not start or p <= 0:
            return None
        return (p - start) / p

    @property
    def discount_vs_market(self):
        m = self._data.get("market_price")
        start = self._data.get("start_price")
        if not m or not start or m <= 0:
            return None
        return (m - start) / m

    @property
    def benchmark_divergence(self):
        a, m = self.discount_vs_appraisal, self.discount_vs_market
        if a is None or m is None:
            return None
        return abs(a - m)

    @property
    def debt_total_claim(self) -> float:
        return float(self._data.get("debt_principal") or 0) + \
            float(self._data.get("debt_interest") or 0)

    @property
    def guarantee_coverage(self):
        claim = self.debt_total_claim
        collateral = self._data.get("collateral_value")
        if not collateral or claim <= 0:
            return None
        return float(collateral) / claim

    @property
    def deadline_days_left(self):
        return None


@router.post("/evaluate")
def api_evaluate(payload: EvaluateRequest, db: Session = Depends(get_db),
                 with_benchmark: bool = Query(True, description="是否返回区域基准匹配详情")):
    """对单条标的执行一次完整评估（一票否决 → 五维评分 → 标签 → 分级），不落库。"""
    rs = load_ruleset(db)
    data: dict[str, Any] = payload.model_dump()
    stub = _AssetStub(data)
    bench = resolve_benchmark(db, stub, rs)
    result = evaluate(stub, rs, bench)

    resp = {
        "status": result.status,
        "vetoed": result.status == "vetoed",
        "veto_hits": result.veto_hits,
        "penalty_hits": result.penalty_hits,
        "asset_class": result.score_detail.get("asset_class", "property"),
        "total_score": result.total_score,
        "grade": result.grade,
        "grade_label": C.GRADES.get(result.grade),
        "dimensions": [
            {
                "code": code,
                "label": dim["label"],
                "score": dim["score"],
                "weight": dim["weight"],
                "rate": dim["rate"],
                "items": dim["items"],
                "note": dim["note"],
            }
            for code in result.score_detail.get("order", [])
            if (dim := result.score_detail["dimensions"].get(code))
        ],
        "rent_detail": result.rent_detail,
        "advantage_tags": result.advantage_tags,
        "risk_tags": result.risk_tags,
        "conclusion": result.conclusion,
        "disclaimer": rs.disclaimer,
        "engine_version": result.engine_version,
    }
    if with_benchmark:
        resp["benchmark"] = {
            "raw": push_down_benchmark(bench, stub),
            "display": benchmark_display(bench),
        }
    return resp


# ==================================================================== 标的查询
@router.get("/assets")
def api_assets(db: Session = Depends(get_db),
               grade: str | None = None, status: str | None = None,
               city: str | None = None, asset_type: str | None = None,
               limit: int = Query(50, le=500), offset: int = 0):
    stmt = select(Asset)
    if grade:
        stmt = stmt.where(Asset.grade == grade)
    if status:
        stmt = stmt.where(Asset.status == status)
    if city:
        stmt = stmt.where(Asset.city == city)
    if asset_type:
        stmt = stmt.where(Asset.asset_type == asset_type)

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(Asset.total_score.desc())
                      .offset(offset).limit(limit)).scalars().all()
    return {
        "total": total,
        "items": [
            {
                "id": a.id, "title": a.title, "grade": a.grade,
                "total_score": a.total_score, "status": a.status,
                "asset_class": a.asset_class or "property",
                "asset_class_label": C.ASSET_CLASSES.get(a.asset_class or "property"),
                "asset_type": a.asset_type,
                "asset_type_label": C.ASSET_TYPES.get(a.asset_type),
                "city": a.city, "district": a.district,
                "source_platform": a.source_platform,
                "start_price": a.start_price,
                "discount_rate": a.discount_rate,
                "net_rent_ratio": (a.rent_detail or {}).get("net_rent_ratio"),
                "advantage_tags": [t["name"] for t in (a.advantage_tags or [])],
                "risk_tags": [t["name"] for t in (a.risk_tags or [])],
                "scored_at": a.scored_at.isoformat() if a.scored_at else None,
            }
            for a in rows
        ],
    }


@router.get("/assets/{asset_id}")
def api_asset(asset_id: int, db: Session = Depends(get_db)):
    a = db.get(Asset, asset_id)
    if a is None:
        raise HTTPException(status_code=404, detail="标的不存在")
    return {
        "id": a.id, "uid": a.uid, "title": a.title, "status": a.status,
        "grade": a.grade, "total_score": a.total_score,
        "veto_hits": a.veto_hits,
        "score_detail": a.score_detail,
        "rent_detail": a.rent_detail,
        "advantage_tags": a.advantage_tags,
        "risk_tags": a.risk_tags,
        "conclusion": a.conclusion,
        "engine_version": a.engine_version,
        "scored_at": a.scored_at.isoformat() if a.scored_at else None,
    }


# ==================================================================== 配置
@router.get("/config")
def api_config(db: Session = Depends(get_db)):
    rs = load_ruleset(db)
    return {
        "weights_property": rs.weights,
        "weights_debt": rs.weights_debt,
        "grade_thresholds": rs.grade_thresholds,
        "cost_params": rs.cost_params,
        "tag_thresholds": rs.tag_thresholds,
        "dimension_labels": {
            "property": C.DIMENSION_LABELS_PROPERTY,
            "debt": C.DIMENSION_LABELS_DEBT,
        },
        "asset_classes": C.ASSET_CLASSES,
        "asset_types": C.ASSET_TYPES,
        "platforms": C.PLATFORMS,
        "disclaimer": rs.disclaimer,
    }


@router.get("/veto-rules")
def api_veto_rules(db: Session = Depends(get_db)):
    rs = load_ruleset(db)
    return {"rules": [
        {k: r.get(k) for k in ("code", "name", "description", "enabled",
                               "keyword_enabled", "keywords")}
        for r in rs.veto_rules
    ]}
