"""参数与规则的统一装载层。

引擎函数**只接受 RuleSet 对象**，不直接碰数据库 —— 这样：
1. 同一份 RuleSet + 同一份标的数据 → 必然同一结果（PRD 验收标准 3：结果可复现）；
2. 每次评分把 RuleSet 快照写进 ScoreHistory，事后可以精确还原"当时用的什么参数"；
3. 单元测试可以手工构造 RuleSet，不需要数据库。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import constants as C


# ==================================================================== 插值工具
def interp(anchors: list[list[float]], x: float) -> float:
    """在锚点表上做分段线性插值，两端钳制。

    anchors: [[x0, y0], [x1, y1], ...]，按 x 升序。
    这是全部"档位打分"的唯一实现 —— 后台改锚点即改规则，无需改代码。
    """
    if not anchors:
        return 0.0
    pts = sorted(((float(a[0]), float(a[1])) for a in anchors), key=lambda p: p[0])
    if x <= pts[0][0]:
        return pts[0][1]
    if x >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return y1
            t = (x - x0) / (x1 - x0)
            return round(y0 + t * (y1 - y0), 4)
    return pts[-1][1]


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def r2(v: float | None, nd: int = 2) -> float | None:
    return None if v is None else round(float(v), nd)


# ==================================================================== RuleSet
@dataclass
class RuleSet:
    """一次评分所需的全部参数与规则（不可变快照）。

    双轨制说明：物权与债权是**两套完全独立**的权重、锚点、分值表。
    为兼容历史调用（以及旧配置键），`weights` / `DIMENSION_*` 等单轨字段
    保留为"物权轨"的别名，但所有引擎一律走 `weights_for(asset_class)`。
    """

    # ---- 物权轨
    weights: dict[str, float]
    price_anchors: list[list[float]]
    rent_anchors: list[list[float]]
    clearance_anchors: list[list[float]] = field(default_factory=list)
    holding_cost_anchors: list[list[float]] = field(default_factory=list)
    # ---- 债权轨
    weights_debt: dict[str, float] = field(default_factory=dict)
    coverage_anchors: list[list[float]] = field(default_factory=list)
    debt_rank_scores: dict[str, float] = field(default_factory=dict)
    debt_execution_scores: dict[str, float] = field(default_factory=dict)
    debt_solvency_scores: dict[str, float] = field(default_factory=dict)
    debt_doc_scores: dict[str, float] = field(default_factory=dict)
    # ---- 两轨共用
    grade_thresholds: dict[str, float] = field(default_factory=dict)
    land_year_anchors: list[list[float]] = field(default_factory=list)
    cost_params: dict[str, float] = field(default_factory=dict)
    legal_penalty: dict[str, float] = field(default_factory=dict)
    location_params: dict[str, Any] = field(default_factory=dict)
    ownership_params: dict[str, Any] = field(default_factory=dict)
    tag_thresholds: dict[str, float] = field(default_factory=dict)
    tag_enabled: dict[str, bool] = field(default_factory=dict)
    default_rent_per_sqm_month: dict[str, float] = field(default_factory=dict)
    veto_rules: list[dict] = field(default_factory=list)
    disclaimer: str = ""

    # ---------------------------------------------------------------- 工具
    #: 旧代码用 RuleSet(...) 按位置传参的兼容顺序
    _COMPAT_ORDER = (
        "weights", "grade_thresholds", "price_anchors", "rent_anchors",
        "land_year_anchors", "cost_params", "legal_penalty", "location_params",
        "ownership_params", "tag_thresholds", "tag_enabled",
        "default_rent_per_sqm_month", "veto_rules", "disclaimer",
    )

    def __post_init__(self) -> None:
        # 债权权重缺省则继承物权结构（同名维度）以保证不等长配置也能跑
        if not self.weights_debt:
            self.weights_debt = dict(C.DEFAULT_WEIGHTS_DEBT)
        if not self.clearance_anchors:
            self.clearance_anchors = copy.deepcopy(C.DEFAULT_CLEARANCE_ANCHORS)
        if not self.holding_cost_anchors:
            self.holding_cost_anchors = copy.deepcopy(C.DEFAULT_HOLDING_COST_ANCHORS)
        if not self.coverage_anchors:
            self.coverage_anchors = copy.deepcopy(C.DEFAULT_COVERAGE_ANCHORS)
        if not self.debt_rank_scores:
            self.debt_rank_scores = copy.deepcopy(C.DEBT_RANK_SCORES)
        if not self.debt_execution_scores:
            self.debt_execution_scores = copy.deepcopy(C.DEBT_EXECUTION_SCORES)
        if not self.debt_solvency_scores:
            self.debt_solvency_scores = copy.deepcopy(C.DEBT_SOLVENCY_SCORES)
        if not self.debt_doc_scores:
            self.debt_doc_scores = copy.deepcopy(C.DEBT_DOC_SCORES)

    @classmethod
    def from_legacy(cls, **kwargs) -> "RuleSet":
        """按旧版字段名构造（位置参数顺序见 _COMPAT_ORDER）。

        旧版 RuleSet 用位置参数构造，新增字段插入中间会错位；
        这里按名字映射，保证旧调用与新调用都能工作。
        """
        mapped: dict[str, Any] = {}
        for i, (name, value) in enumerate(zip(cls._COMPAT_ORDER, kwargs.get("args", ()))):
            mapped[name] = value
        mapped.update(kwargs.get("kwargs", {}))
        return cls(**mapped)

    def weights_for(self, asset_class: str) -> dict[str, float]:
        """按资产类别取权重表。未知类别回落物权（从严不适用，宁可给全权重）。"""
        return self.weights_debt if (asset_class or "").lower() == "debt" else self.weights

    def dim_max(self, code: str, asset_class: str = "property") -> float:
        return float(self.weights_for(asset_class).get(code, 0.0))

    def total_weight(self, asset_class: str = "property") -> float:
        return float(sum(self.weights_for(asset_class).values()))

    # 兼容旧调用名
    def property_weight_total(self) -> float:
        return float(sum(self.weights.values()))

    def debt_weight_total(self) -> float:
        return float(sum(self.weights_debt.values()))

    def to_snapshot(self, asset_class: str | None = None) -> dict:
        """写入 ScoreHistory.params_snapshot 的可序列化快照。

        asset_class 给定时只导出该轨道权重，避免快照里混入无关参数导致
        事后复核时误以为另一套规则也参与了本次评分。
        """
        snap: dict[str, Any] = {
            "grade_thresholds": self.grade_thresholds,
            "cost_params": self.cost_params,
            "legal_penalty": self.legal_penalty,
            "location_params": self.location_params,
            "ownership_params": self.ownership_params,
            "tag_thresholds": self.tag_thresholds,
            "veto_rules": [
                {k: r.get(k) for k in
                 ("code", "name", "enabled", "keyword_enabled",
                  "applies_to", "action", "field_conditions", "keywords")}
                for r in self.veto_rules
            ],
        }
        if asset_class == "debt":
            snap["weights"] = self.weights_debt
            snap["coverage_anchors"] = self.coverage_anchors
            snap["debt_rank_scores"] = self.debt_rank_scores
            snap["debt_execution_scores"] = self.debt_execution_scores
            snap["debt_solvency_scores"] = self.debt_solvency_scores
            snap["debt_doc_scores"] = self.debt_doc_scores
        else:
            snap["weights"] = self.weights
            snap["price_anchors"] = self.price_anchors
            snap["rent_anchors"] = self.rent_anchors
            snap["clearance_anchors"] = self.clearance_anchors
            snap["holding_cost_anchors"] = self.holding_cost_anchors
            snap["land_year_anchors"] = self.land_year_anchors
        return snap


def _cfg(db: Session, key: str) -> Any:
    """读配置。若新键未写入，回落到旧键名（LEGACY_CONFIG_ALIASES）。"""
    from app.models import SysConfig

    row = db.get(SysConfig, key)
    if row is None:
        legacy = C.LEGACY_CONFIG_ALIASES.get(key)
        if legacy:
            row = db.get(SysConfig, legacy)
    return row.value if row else None


def _as_dict(value: Any, default: dict) -> dict:
    """配置值统一成 dict。兼容后台把 value 存成 JSON 字符串的情况。"""
    import json

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return copy.deepcopy(default)
    if isinstance(value, dict):
        merged = copy.deepcopy(default)
        merged.update(value)
        return merged
    return copy.deepcopy(default)


def _as_anchors(value: Any, default: list) -> list[list[float]]:
    import json

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return copy.deepcopy(default)
    if isinstance(value, list) and all(isinstance(p, (list, tuple)) and len(p) == 2
                                       for p in value):
        return [[float(p[0]), float(p[1])] for p in value]
    return copy.deepcopy(default)


def load_ruleset(db: Session) -> RuleSet:
    """从数据库装载全量参数；任何缺失键回落出厂默认。双轨制版本。"""
    from app.config import DEFAULT_DISCLAIMER
    from app.models import VetoRule

    weights = _as_dict(_cfg(db, "weights_property"), C.DEFAULT_WEIGHTS_PROPERTY)
    weights = {k: max(0.0, float(v)) for k, v in weights.items()
               if k in C.DEFAULT_WEIGHTS_PROPERTY}
    for k, v in C.DEFAULT_WEIGHTS_PROPERTY.items():
        weights.setdefault(k, v)

    weights_debt = _as_dict(_cfg(db, "weights_debt"), C.DEFAULT_WEIGHTS_DEBT)
    weights_debt = {k: max(0.0, float(v)) for k, v in weights_debt.items()
                    if k in C.DEFAULT_WEIGHTS_DEBT}
    for k, v in C.DEFAULT_WEIGHTS_DEBT.items():
        weights_debt.setdefault(k, v)

    grade_thresholds = _as_dict(_cfg(db, "grade_thresholds"), C.DEFAULT_GRADE_THRESHOLDS)

    rules = db.execute(
        select(VetoRule).order_by(VetoRule.priority, VetoRule.id)
    ).scalars().all()
    veto_rules = [
        {
            "code": r.code,
            "name": r.name,
            "priority": r.priority,
            "enabled": bool(r.enabled),
            "keyword_enabled": bool(r.keyword_enabled),
            "applies_to": getattr(r, "applies_to", "property") or "property",
            "action": getattr(r, "action", "veto") or "veto",
            "description": r.description or "",
            "field_conditions": r.field_conditions or [],
            "keywords": r.keywords or [],
        }
        for r in rules
    ]

    disclaimer = _cfg(db, "disclaimer")
    if not isinstance(disclaimer, str) or not disclaimer.strip():
        disclaimer = DEFAULT_DISCLAIMER

    return RuleSet(
        weights=weights,
        weights_debt=weights_debt,
        grade_thresholds={
            "A": float(grade_thresholds.get("A", 80.0)),
            "B": float(grade_thresholds.get("B", 60.0)),
        },
        price_anchors=_as_anchors(_cfg(db, "price_anchors"), C.DEFAULT_PRICE_ANCHORS),
        rent_anchors=_as_anchors(_cfg(db, "rent_anchors"), C.DEFAULT_RENT_ANCHORS),
        clearance_anchors=_as_anchors(
            _cfg(db, "clearance_anchors"), C.DEFAULT_CLEARANCE_ANCHORS),
        holding_cost_anchors=_as_anchors(
            _cfg(db, "holding_cost_anchors"), C.DEFAULT_HOLDING_COST_ANCHORS),
        coverage_anchors=_as_anchors(
            _cfg(db, "coverage_anchors"), C.DEFAULT_COVERAGE_ANCHORS),
        debt_rank_scores=_as_dict(_cfg(db, "debt_rank_scores"), C.DEBT_RANK_SCORES),
        debt_execution_scores=_as_dict(
            _cfg(db, "debt_execution_scores"), C.DEBT_EXECUTION_SCORES),
        debt_solvency_scores=_as_dict(
            _cfg(db, "debt_solvency_scores"), C.DEBT_SOLVENCY_SCORES),
        debt_doc_scores=_as_dict(_cfg(db, "debt_doc_scores"), C.DEBT_DOC_SCORES),
        land_year_anchors=_as_anchors(
            _cfg(db, "land_year_anchors"), C.DEFAULT_LAND_YEAR_ANCHORS),
        cost_params=_as_dict(_cfg(db, "cost_params"), C.DEFAULT_COST_PARAMS),
        legal_penalty=_as_dict(_cfg(db, "legal_penalty"), C.DEFAULT_LEGAL_PENALTY),
        location_params=_as_dict(_cfg(db, "location_params"), C.DEFAULT_LOCATION_PARAMS),
        ownership_params=_as_dict(_cfg(db, "ownership_params"), C.DEFAULT_OWNERSHIP_PARAMS),
        tag_thresholds=_as_dict(_cfg(db, "tag_thresholds"), C.DEFAULT_TAG_THRESHOLDS),
        tag_enabled=_as_dict(_cfg(db, "tag_enabled"),
                             {t["code"]: True for t in C.TAG_REGISTRY}),
        default_rent_per_sqm_month=_as_dict(
            _cfg(db, "default_rent_per_sqm_month"), C.DEFAULT_RENT_PER_SQM_MONTH),
        veto_rules=veto_rules,
        disclaimer=disclaimer,
    )


# ==================================================================== 初始化
def ensure_defaults(db: Session) -> dict[str, int]:
    """首次运行灌入出厂参数、否决规则、区域基准。幂等，可重复执行。"""
    from app.models import RegionBenchmark, SysConfig, VetoRule

    created = {"config": 0, "veto_rules": 0, "regions": 0}

    for key, default in C.CONFIG_KEYS.items():
        if db.get(SysConfig, key) is None:
            value = default if default is not None else None
            db.add(SysConfig(key=key, value=copy.deepcopy(value),
                             note=C.CONFIG_GROUP_LABELS.get(key, "")))
            created["config"] += 1

    existing_codes = {c for (c,) in db.execute(select(VetoRule.code)).all()}
    for seed in C.VETO_RULE_SEED:
        if seed["code"] not in existing_codes:
            db.add(VetoRule(**copy.deepcopy(seed)))
            created["veto_rules"] += 1

    existing_regions = {
        (c, d, t) for c, d, t in
        db.execute(select(RegionBenchmark.city, RegionBenchmark.district,
                          RegionBenchmark.asset_type)).all()
    }
    for seed in C.REGION_BENCHMARK_SEED:
        key = (seed["city"], seed["district"], seed["asset_type"])
        if key not in existing_regions:
            db.add(RegionBenchmark(**copy.deepcopy(seed)))
            created["regions"] += 1

    db.flush()
    return created
