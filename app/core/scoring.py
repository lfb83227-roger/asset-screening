"""模块3：五维量化评分引擎 —— **双轨统一入口**。

物权（property）与债权（debt）是两套完全不同的评分体系，
本模块只做「按资产类别分流」+ 共享工具，具体维度实现在：
  * app/core/scoring_property.py  物权五维（评估基准/清场占有/持有成本/租金/区位）
  * app/core/scoring_debt.py      债权五维（覆盖倍数/顺位/执行进展/偿付能力/凭证）

统一约定（两条轨道必须一致，否则前端渲染与报告导出会分裂）：
* 每个维度先算 `raw`（按锚点表/分值表得到的原始分），再按
  `score = raw / anchor_max × 当前权重` 折算 —— 后台调权重不改变策略含义。
* 所有打分走锚点插值或显式系数，无随机、无隐式状态 → 结果完全可复现。
"""
from __future__ import annotations

from typing import Any

from app.core import constants as C
from app.core.ruleset import RuleSet, clamp, interp, r2  # noqa: F401 (re-export)
from app.core.scoring_common import _anchor_max, _dim, _pick  # noqa: F401
from app.core import scoring_debt, scoring_property

#: 旧版五维名称，仅为兼容历史报告/测试中的引用
LEGACY_DIMENSION_ORDER = ("price", "rent", "legal", "location", "ownership")
LEGAL_BASE_SCORE = 20.0


def dimension_order(asset_class: str) -> tuple[str, ...]:
    """取该资产类别的维度顺序（前端渲染、报告导出共用）。"""
    return scoring_debt.DIMENSION_ORDER if asset_class == "debt" \
        else scoring_property.DIMENSION_ORDER


def dimension_labels(asset_class: str) -> dict[str, str]:
    return dict(C.DIMENSION_LABELS_DEBT if asset_class == "debt"
                else C.DIMENSION_LABELS_PROPERTY)


def weights_of(asset_class: str) -> dict[str, float]:
    return dict(C.DEFAULT_WEIGHTS_DEBT if asset_class == "debt"
                else C.DEFAULT_WEIGHTS_PROPERTY)


# ==================================================================== 统一入口
def score_all(asset, rs: RuleSet, rent_detail: dict | None = None,
              bench: dict | None = None,
              penalty_hits: list[dict] | None = None) -> tuple[dict, float]:
    """按资产类别分流到对应引擎，返回 (score_detail, total_score)。

    物权需要 rent_detail（租金测算）与 bench（区域基准）；
    债权不需要这两者，传 None 即可。
    """
    if getattr(asset, "is_debt", False):
        return scoring_debt.score_all(asset, rs, penalty_hits)
    return scoring_property.score_all(
        asset, rs, rent_detail or {}, bench or {}, penalty_hits)


def grade_of(total: float, rs: RuleSet) -> str:
    """模块4：A ≥80 / B 60-79 / C <60（阈值可后台配置）。"""
    a, b = rs.grade_thresholds["A"], rs.grade_thresholds["B"]
    if total >= a:
        return "A"
    if total >= b:
        return "B"
    return "C"


# ==================================================================== 便捷导出
# 供旧调用点（测试、脚本）直接引用；新代码请走 score_all 分流。
score_price = scoring_property.score_price
score_clearance = scoring_property.score_clearance
score_holding_cost = scoring_property.score_holding_cost
score_rent = scoring_property.score_rent
score_location = scoring_property.score_location
score_coverage = scoring_debt.score_coverage
score_rank = scoring_debt.score_rank
score_execution = scoring_debt.score_execution
score_solvency = scoring_debt.score_solvency
score_documentation = scoring_debt.score_documentation

#: 旧名兼容：legal / ownership 两个维度已按业务要求重构掉，
#: 旧调用若仍引用会拿到 NotImplementedError，避免静默算出错误分数。
def score_legal(asset, rs: RuleSet) -> dict:  # pragma: no cover - 兼容占位
    raise NotImplementedError(
        "产权司法维度已在双轨制重构中拆分为「清场与占有」+「持有成本」，"
        "请改用 score_clearance / score_holding_cost")


def score_ownership(asset, rs: RuleSet) -> dict:  # pragma: no cover - 兼容占位
    raise NotImplementedError(
        "权属合规维度已并入「清场与占有」维度（V4 权属争议规则），"
        "请改用 score_clearance")
