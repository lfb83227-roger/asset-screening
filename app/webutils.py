"""模板渲染与视图公共工具。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import TEMPLATE_DIR
from app.core import constants as C

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


# ==================================================================== 过滤器
def f_money(v: Any, unit: str = "元") -> str:
    if v in (None, ""):
        return "—"
    try:
        return f"{float(v):,.2f} {unit}"
    except (TypeError, ValueError):
        return str(v)


def f_money_short(v: Any) -> str:
    """金额简写：大于 1 万时用「万元」，列表页更清爽。"""
    if v in (None, ""):
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(n) >= 10000:
        return f"{n / 10000:,.1f} 万"
    return f"{n:,.0f} 元"


def f_pct(v: Any, digits: int = 2) -> str:
    if v in (None, ""):
        return "—"
    try:
        return f"{float(v) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return str(v)


def f_num(v: Any, digits: int = 2) -> str:
    if v in (None, ""):
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{n:.{digits}f}".rstrip("0").rstrip(".") if digits else f"{n:.0f}"


def f_dt(v: Any, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if not v:
        return "—"
    if isinstance(v, str):
        return v
    return v.strftime(fmt) if isinstance(v, datetime) else str(v)


def f_enum(mapping: dict, key: Any) -> str:
    if key in (None, ""):
        return "—"
    return mapping.get(key, str(key))


def f_percent_input(v: Any) -> str:
    """比率 → 便于在 input 里编辑的百分数值。"""
    if v in (None, ""):
        return ""
    try:
        return f"{float(v) * 100:g}"
    except (TypeError, ValueError):
        return str(v)


def f_bool3(v: Any) -> str:
    """三态布尔展示。"""
    if v is True:
        return "是"
    if v is False:
        return "否"
    return "未载明"


def f_days_left(v: Any) -> str:
    if v is None:
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    if n < 0:
        return "已截止"
    return f"{n:.1f} 天"


def f_grade_class(g: Any) -> str:
    return {"A": "g-a", "B": "g-b", "C": "g-c"}.get(g or "C", "g-c")


def f_score_class(score: Any, weight: Any) -> str:
    """得分率 → 颜色档（用于进度条）。"""
    try:
        rate = float(score) / float(weight) if float(weight) else 0
    except (TypeError, ValueError):
        return "bar-low"
    if rate >= 0.8:
        return "bar-high"
    if rate >= 0.6:
        return "bar-mid"
    return "bar-low"


def f_tag_class(kind: str) -> str:
    return "tag-adv" if kind == "advantage" else "tag-risk"


def f_platform(p: Any) -> str:
    return C.PLATFORMS.get(p, str(p) if p else "—")


for _name, _fn in (
    ("money", f_money), ("money_short", f_money_short), ("pct", f_pct),
    ("num", f_num), ("dt", f_dt), ("percent_input", f_percent_input),
    ("bool3", f_bool3), ("days_left", f_days_left), ("grade_class", f_grade_class),
    ("score_class", f_score_class), ("tag_class", f_tag_class),
    ("platform", f_platform),
):
    templates.env.filters[_name] = _fn

templates.env.globals.update({
    "C": C,
    "ASSET_TYPES": C.ASSET_TYPES,
    "PLATFORMS": C.PLATFORMS,
    "LAND_NATURES": C.LAND_NATURES,
    "LEASE_STATUSES": C.LEASE_STATUSES,
    "SCRAP_STATUSES": C.SCRAP_STATUSES,
    "COMPLIANCE_LEVELS": C.COMPLIANCE_LEVELS,
    "CITY_TIERS": C.CITY_TIERS,
    "AUCTION_ROUNDS": C.AUCTION_ROUNDS,
    "GRADES": C.GRADES,
    "ASSET_STATUSES": C.ASSET_STATUSES,
    "DIMENSION_LABELS": C.DIMENSION_LABELS,
    "ROLE_LABELS": {"viewer": "只读查看", "operator": "业务操作", "admin": "系统管理员"},
})


# ==================================================================== 渲染
def render(request: Request, name: str, user=None, **context):
    """统一渲染入口：自动带上用户、导航高亮与提示消息。"""
    context.setdefault("user", user)
    context.setdefault("active", "")
    context.setdefault("ok", request.query_params.get("ok"))
    context.setdefault("err", request.query_params.get("err"))
    context.setdefault("now", datetime.now())
    return templates.TemplateResponse(request, name, context)


# ==================================================================== 导航
NAV_ITEMS = [
    {"key": "dashboard", "url": "/", "label": "总览"},
    {"key": "assets", "url": "/assets", "label": "标的库"},
    {"key": "crawl", "url": "/crawl", "label": "采集管理"},
    {"key": "import", "url": "/import", "label": "批量导入"},
    {"key": "veto", "url": "/admin/veto", "label": "否决规则"},
    {"key": "config", "url": "/admin/config", "label": "评分参数"},
    {"key": "regions", "url": "/admin/regions", "label": "区域基准"},
    {"key": "users", "url": "/admin/users", "label": "权限与日志"},
]
templates.env.globals["NAV_ITEMS"] = NAV_ITEMS
