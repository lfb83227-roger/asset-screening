"""评分引擎公共层：被物权 / 债权两条轨道共用的工具函数。

放在这里的原因：两条轨道的维度结构不同，但「维度得分 = 原始分 ÷ 锚点满分 × 权重」
这条折算规则、以及 `_dim` 输出结构必须完全一致 —— 否则前端渲染、报告导出、
参数快照都会因为两套结构而分裂。
"""
from __future__ import annotations

from app.core.ruleset import clamp, interp, r2  # noqa: F401  (re-export 供各轨道使用)

# 扣分型维度的分制基准（历史沿用 20 分制系数，后台调权重时按比例折算）
DEFAULT_LEGAL_BASE = 20.0


def _dim(code: str, label: str, weight: float, raw: float,
         anchor_max: float, items: list[dict],
         inputs: dict | None = None, note: str = "") -> dict:
    """构造一个维度的标准输出结构。

    统一约定：`score = raw / anchor_max × weight`。
    这样后台调整权重时，各子项的相对关系保持稳定，不改变策略含义。
    """
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


def _ratio01(v) -> float:
    """把 0~1 或 0~100 的输入统一归一到 0~1。"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return clamp(v / 100.0 if v > 1.0 else v, 0.0, 1.0)
