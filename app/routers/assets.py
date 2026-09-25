"""标的库：列表、详情、录入、编辑、重算、报告导出。

表单字段用一份声明式 spec（FORM_SECTIONS）同时驱动模板渲染与提交解析，
避免「模板加了一个字段但后端忘了读」这类最容易出的错。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core import constants as C
from app.core.pipeline import evaluate_and_persist
from app.core.ruleset import load_ruleset
from app.database import get_db
from app.models import Asset, ScoreHistory
from app.report.pdf import build_pdf
from app.security import ROLE_LEVEL, client_ip, log_operation, require_login
from app.webutils import render

router = APIRouter(prefix="/assets", tags=["assets"])

# ==================================================================== 表单声明
# type: text | number | money | int | datetime | select | tri | textarea | ratio
FORM_SECTIONS: list[dict] = [
    {"group": "基础信息", "fields": [
        {"name": "title", "label": "标的名称", "type": "text", "span": 2,
         "required": True},
        {"name": "asset_type", "label": "资产类型", "type": "select", "options": C.ASSET_TYPES},
        {"name": "source_platform", "label": "数据来源", "type": "select",
         "options": C.PLATFORMS},
        {"name": "province", "label": "省份", "type": "text"},
        {"name": "city", "label": "城市", "type": "text"},
        {"name": "district", "label": "区县", "type": "text"},
        {"name": "address", "label": "标的地址", "type": "text", "span": 2},
        {"name": "external_id", "label": "平台标的编号", "type": "text"},
        {"name": "source_url", "label": "详情链接", "type": "text"},
    ]},
    {"group": "面积与价格", "fields": [
        {"name": "area_sqm", "label": "建筑面积(㎡)", "type": "number"},
        {"name": "land_area_sqm", "label": "土地面积(㎡)", "type": "number"},
        {"name": "start_price", "label": "起拍价(元)", "type": "money"},
        {"name": "appraisal_price", "label": "评估价(元)", "type": "money"},
        {"name": "market_price", "label": "周边同类成交价(元)", "type": "money"},
        {"name": "deposit", "label": "保证金(元)", "type": "money"},
    ]},
    {"group": "挂牌信息", "fields": [
        {"name": "listed_at", "label": "挂牌时间", "type": "datetime"},
        {"name": "deadline_at", "label": "截止时间", "type": "datetime"},
        {"name": "auction_round", "label": "拍卖轮次", "type": "select",
         "options": C.AUCTION_ROUNDS},
        {"name": "court", "label": "处置法院", "type": "text"},
        {"name": "case_no", "label": "案号", "type": "text"},
    ]},
    {"group": "权属与合规", "fields": [
        {"name": "land_nature", "label": "土地性质", "type": "select",
         "options": C.LAND_NATURES},
        {"name": "land_remaining_years", "label": "土地剩余年限(年)", "type": "number"},
        {"name": "compliance_level", "label": "合规等级", "type": "select",
         "options": C.COMPLIANCE_LEVELS},
        {"name": "registration_ok", "label": "可否办理不动产登记", "type": "tri"},
        {"name": "transfer_restricted", "label": "是否限制转让", "type": "tri"},
        {"name": "can_supplement_procedure", "label": "划拨地可否补办手续", "type": "tri"},
        {"name": "implicit_coownership", "label": "隐性共有产权提示", "type": "tri"},
    ]},
    {"group": "司法风险", "fields": [
        {"name": "mortgage_count", "label": "抵押数量", "type": "int"},
        {"name": "seal_count", "label": "轮候查封数量", "type": "int"},
        {"name": "lawsuit_count", "label": "涉诉案件数", "type": "int"},
        {"name": "dispute_freq", "label": "司法纠纷频次", "type": "int"},
        {"name": "co_ownership_dispute", "label": "共有产权无法确权", "type": "tri"},
        {"name": "irreversible_seal", "label": "不可解除的限制性查封", "type": "tri"},
    ]},
    {"group": "占用与租赁", "fields": [
        {"name": "lease_status", "label": "租赁情况", "type": "select",
         "options": C.LEASE_STATUSES},
        {"name": "occupied", "label": "是否被占用", "type": "tri"},
        {"name": "can_clear", "label": "可否清场", "type": "tri"},
        {"name": "occupancy_note", "label": "占用说明", "type": "text", "span": 2},
        {"name": "scrap_status", "label": "使用状态", "type": "select",
         "options": C.SCRAP_STATUSES},
    ]},
    {"group": "欠费情况（元）", "fields": [
        {"name": "tax_owed", "label": "欠税", "type": "money"},
        {"name": "land_idle_fee", "label": "土地闲置费", "type": "money"},
        {"name": "construction_arrears", "label": "工程欠款", "type": "money"},
        {"name": "property_fee_owed", "label": "物业欠费", "type": "money"},
    ]},
    {"group": "租金与区位（留空则取区域大数据均值）", "fields": [
        {"name": "rent_per_sqm_month", "label": "租金(元/㎡/月)", "type": "number"},
        {"name": "annual_gross_rent_override", "label": "年毛租金(元)", "type": "money"},
        {"name": "city_tier", "label": "城市能级", "type": "select", "options": C.CITY_TIERS},
        {"name": "industry_support_ratio", "label": "产业配套(0~100)", "type": "ratio"},
        {"name": "rental_demand_ratio", "label": "出租需求(0~100)", "type": "ratio"},
        {"name": "turnover_ratio", "label": "转手成交率(0~100)", "type": "ratio"},
    ]},
    {"group": "公告原文（用于自动抽取字段，不覆盖上方已填值）", "fields": [
        {"name": "raw_text", "label": "公告原文", "type": "textarea", "span": 2},
    ]},
]

ALL_FORM_FIELDS = [f for sec in FORM_SECTIONS for f in sec["fields"]]
FIELD_BY_NAME = {f["name"]: f for f in ALL_FORM_FIELDS}

MONEY_FIELDS = {f["name"] for f in ALL_FORM_FIELDS if f["type"] == "money"}
NUMBER_FIELDS = {f["name"] for f in ALL_FORM_FIELDS if f["type"] in ("number", "ratio")}
INT_FIELDS = {f["name"] for f in ALL_FORM_FIELDS if f["type"] == "int"}
DT_FIELDS = {f["name"] for f in ALL_FORM_FIELDS if f["type"] == "datetime"}
TRI_FIELDS = {f["name"] for f in ALL_FORM_FIELDS if f["type"] == "tri"}
RATIO_FIELDS = {f["name"] for f in ALL_FORM_FIELDS if f["type"] == "ratio"}


# ==================================================================== 解析
def parse_form(form) -> tuple[dict, list[str]]:
    """从表单解析出字段字典；返回 (数据, 错误列表)。"""
    data: dict = {}
    errors: list[str] = []
    for f in ALL_FORM_FIELDS:
        name = f["name"]
        raw = form.get(name)
        raw = "" if raw is None else str(raw).strip()

        if not raw:
            data[name] = None if name not in INT_FIELDS else 0
            if name in INT_FIELDS:
                data[name] = 0
            continue

        try:
            if name in MONEY_FIELDS or name in NUMBER_FIELDS:
                value = float(raw.replace(",", "").replace("￥", "").replace("万元", ""))
                if name in RATIO_FIELDS and value > 1.0:
                    value = value / 100.0
                data[name] = value
            elif name in INT_FIELDS:
                data[name] = int(float(raw))
            elif name in DT_FIELDS:
                data[name] = datetime.strptime(raw, "%Y-%m-%dT%H:%M")
            elif name in TRI_FIELDS:
                data[name] = {"是": True, "否": False}.get(raw, None)
            else:
                data[name] = raw
        except (ValueError, TypeError):
            errors.append(f"「{f['label']}」格式不正确：{raw}")

    if not data.get("title"):
        errors.append("「标的名称」不能为空")

    # 数值字段的统一默认，避免 None 参与运算
    for name in INT_FIELDS:
        data.setdefault(name, 0)
        if data[name] is None:
            data[name] = 0
    for name in ("lease_status", "land_nature", "auction_round", "scrap_status",
                 "compliance_level"):
        data.setdefault(name, "unknown" if name != "scrap_status" else "normal")
    data.setdefault("compliance_level", "full")
    data.setdefault("asset_type", "other")
    data.setdefault("source_platform", "manual")
    return data, errors


def _form_values(asset: Asset | None) -> dict:
    """把资产对象转成表单初值。"""
    values: dict = {}
    for f in ALL_FORM_FIELDS:
        name = f["name"]
        v = getattr(asset, name, None) if asset else None
        if f["type"] == "datetime":
            values[name] = v.strftime("%Y-%m-%dT%H:%M") if isinstance(v, datetime) else ""
        elif f["type"] == "tri":
            values[name] = "" if v is None else ("是" if v else "否")
        elif f["type"] == "ratio":
            values[name] = "" if v in (None, "") else f"{float(v) * 100:g}"
        elif v in (None, ""):
            values[name] = ""
        elif f["type"] == "int":
            values[name] = str(int(v))
        else:
            values[name] = str(v)
    if not asset:
        values.setdefault("source_platform", "manual")
        values.setdefault("asset_type", "other")
        values.setdefault("land_nature", "unknown")
        values.setdefault("lease_status", "unknown")
        values.setdefault("auction_round", "unknown")
        values.setdefault("scrap_status", "normal")
        values.setdefault("compliance_level", "full")
    return values


# ==================================================================== 列表
SORT_OPTIONS = {
    "score_desc": ("综合得分 ↓", Asset.total_score.desc()),
    "score_asc": ("综合得分 ↑", Asset.total_score.asc()),
    "price_asc": ("起拍价 ↑", Asset.start_price.asc()),
    "deadline": ("截止时间 ↑", Asset.deadline_at.asc()),
    "newest": ("最新入库", Asset.id.desc()),
}


@router.get("")
def asset_list(request: Request, db: Session = Depends(get_db),
               user=Depends(require_login)):
    qp = request.query_params
    q = (qp.get("q") or "").strip()
    grade = qp.get("grade") or ""
    status = qp.get("status") or ""
    asset_type = qp.get("asset_type") or ""
    city = (qp.get("city") or "").strip()
    platform = qp.get("platform") or ""
    min_score = qp.get("min_score") or ""
    max_score = qp.get("max_score") or ""
    sort = qp.get("sort") or "score_desc"
    page = max(1, int(qp.get("page") or 1))
    size = 20

    stmt = select(Asset)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Asset.title.like(like), Asset.address.like(like),
                              Asset.external_id.like(like), Asset.case_no.like(like)))
    if grade:
        stmt = stmt.where(Asset.grade == grade)
    if status:
        stmt = stmt.where(Asset.status == status)
    if asset_type:
        stmt = stmt.where(Asset.asset_type == asset_type)
    if city:
        stmt = stmt.where(Asset.city.like(f"%{city}%"))
    if platform:
        stmt = stmt.where(Asset.source_platform == platform)
    try:
        if min_score:
            stmt = stmt.where(Asset.total_score >= float(min_score))
        if max_score:
            stmt = stmt.where(Asset.total_score <= float(max_score))
    except ValueError:
        pass

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.execute(count_stmt).scalar_one()

    order_by = SORT_OPTIONS.get(sort, SORT_OPTIONS["score_desc"])[1]
    stmt = stmt.order_by(order_by).offset((page - 1) * size).limit(size)
    assets = list(db.execute(stmt).scalars().all())

    cities = [c for (c,) in db.execute(
        select(Asset.city).where(Asset.city.is_not(None)).distinct().order_by(Asset.city)
    ).all()]

    # 汇总当前筛选结果的等级分布
    dist = dict(db.execute(
        select(Asset.grade, func.count(Asset.id)).group_by(Asset.grade)
    ).all())

    return render(
        request, "assets.html", user=user, active="assets",
        assets=assets, total=total, page=page, size=size,
        pages=max(1, (total + size - 1) // size),
        q=q, grade=grade, status=status, asset_type=asset_type, city=city,
        platform=platform, min_score=min_score, max_score=max_score, sort=sort,
        cities=cities, sort_options={k: v[0] for k, v in SORT_OPTIONS.items()},
        dist=dist,
        query_string=_build_query_string(qp, drop=("page",)),
    )


def _build_query_string(qp, drop: tuple[str, ...] = ()) -> str:
    parts = [f"{k}={v}" for k, v in qp.items() if k not in drop and v]
    return "&".join(parts)


# ==================================================================== 录入 / 编辑
# 注意：本路由必须声明在 `/{asset_id}` 之前，否则 "new" 会被当成 id 解析。
@router.get("/new")
def asset_new(request: Request, db: Session = Depends(get_db),
              user=Depends(require_login)):
    if ROLE_LEVEL.get(user.role, 0) < ROLE_LEVEL["operator"]:
        return RedirectResponse("/assets?err=当前角色无录入权限", status_code=303)
    return render(request, "asset_form.html", user=user, active="assets",
                  sections=FORM_SECTIONS, values=_form_values(None),
                  asset=None, errors=[])


# ==================================================================== 详情
@router.get("/{asset_id}")
def asset_detail(asset_id: int, request: Request, db: Session = Depends(get_db),
                 user=Depends(require_login)):
    asset = db.get(Asset, asset_id)
    if asset is None:
        return RedirectResponse("/assets?err=标的不存在", status_code=303)

    history = db.execute(
        select(ScoreHistory).where(ScoreHistory.asset_id == asset_id)
        .order_by(ScoreHistory.id.desc()).limit(20)
    ).scalars().all()

    dims = (asset.score_detail or {}).get("dimensions", {}) or {}
    order = (asset.score_detail or {}).get("order") or list(dims.keys())

    # 同区域同类标的横向对比（帮助判断这个分数算高还是低）
    peers = db.execute(
        select(Asset).where(
            Asset.city == asset.city, Asset.asset_type == asset.asset_type,
            Asset.id != asset.id, Asset.status == "scored",
        ).order_by(Asset.total_score.desc()).limit(5)
    ).scalars().all()

    return render(request, "asset_detail.html", user=user, active="assets",
                  a=asset, history=history, dims=dims, dim_order=order, peers=peers)


@router.get("/{asset_id}/edit")
def asset_edit(asset_id: int, request: Request, db: Session = Depends(get_db),
               user=Depends(require_login)):
    asset = db.get(Asset, asset_id)
    if asset is None:
        return RedirectResponse("/assets?err=标的不存在", status_code=303)
    if ROLE_LEVEL.get(user.role, 0) < ROLE_LEVEL["operator"]:
        return RedirectResponse("/assets?err=当前角色无编辑权限", status_code=303)
    return render(request, "asset_form.html", user=user, active="assets",
                  sections=FORM_SECTIONS, values=_form_values(asset),
                  asset=asset, errors=[])


@router.post("/save")
async def asset_save(request: Request, db: Session = Depends(get_db),
                     user=Depends(require_login)):
    if ROLE_LEVEL.get(user.role, 0) < ROLE_LEVEL["operator"]:
        return RedirectResponse("/assets?err=当前角色无写入权限", status_code=303)

    form = await request.form()
    asset_id = form.get("asset_id")
    data, errors = parse_form(form)

    if errors:
        return render(request, "asset_form.html", user=user, active="assets",
                      sections=FORM_SECTIONS, values={k: (v if v is not None else "")
                                                      for k, v in data.items()},
                      asset=(db.get(Asset, int(asset_id)) if asset_id else None),
                      errors=errors, err="表单校验未通过")

    asset = db.get(Asset, int(asset_id)) if asset_id else None
    if asset is None:
        asset = Asset(uid="")
        db.add(asset)
        asset.first_seen_at = datetime.now()

    for key, value in data.items():
        setattr(asset, key, value)

    # 手工录入的标的也需要去重键，方便与采集来的数据比对
    from app.crawler.dedup import make_dedup_key, make_uid
    record = {**data, "uid": ""}
    asset.dedup_key = make_dedup_key(record)
    if not asset.uid:
        asset.uid = make_uid(asset.source_platform or "manual", asset.external_id, record)

    db.flush()
    result = evaluate_and_persist(db, asset, trigger="manual")
    log_operation(db, user, "asset_save", target=f"asset:{asset.id}",
                  detail={"grade": result.grade, "score": result.total_score,
                          "status": result.status},
                  ip=client_ip(request))
    db.commit()

    msg = (f"已保存并完成评估：{result.grade} 类，"
           f"{'触发一票否决' if result.status == 'vetoed' else f'得分 {result.total_score}'}")
    return RedirectResponse(f"/assets/{asset.id}?ok={msg}", status_code=303)


@router.post("/{asset_id}/reevaluate")
def asset_reevaluate(asset_id: int, request: Request, db: Session = Depends(get_db),
                     user=Depends(require_login)):
    asset = db.get(Asset, asset_id)
    if asset is None:
        return RedirectResponse("/assets?err=标的不存在", status_code=303)
    result = evaluate_and_persist(db, asset, trigger="manual")
    log_operation(db, user, "asset_reevaluate", target=f"asset:{asset_id}",
                  detail={"grade": result.grade, "score": result.total_score},
                  ip=client_ip(request))
    db.commit()
    return RedirectResponse(
        f"/assets/{asset_id}?ok=已按当前参数重算：{result.grade} 类 / {result.total_score} 分",
        status_code=303)


@router.post("/{asset_id}/delete")
def asset_delete(asset_id: int, request: Request, db: Session = Depends(get_db),
                 user=Depends(require_login)):
    """归档而不是物理删除 —— 保留审计链路。"""
    asset = db.get(Asset, asset_id)
    if asset is None:
        return RedirectResponse("/assets?err=标的不存在", status_code=303)
    asset.status = "archived"
    log_operation(db, user, "asset_archive", target=f"asset:{asset_id}",
                  ip=client_ip(request))
    db.commit()
    return RedirectResponse(f"/assets?ok=标的 #{asset_id} 已归档", status_code=303)


# ==================================================================== 报告导出
@router.get("/{asset_id}/report.pdf")
def asset_report(asset_id: int, request: Request, db: Session = Depends(get_db),
                 user=Depends(require_login)):
    asset = db.get(Asset, asset_id)
    if asset is None:
        return RedirectResponse("/assets?err=标的不存在", status_code=303)

    ruleset = load_ruleset(db)
    pdf_bytes = build_pdf(asset, ruleset, operator=user.username)
    log_operation(db, user, "report_export", target=f"asset:{asset_id}",
                  ip=client_ip(request))
    db.commit()

    safe_name = f"asset-report-{asset.id}.pdf"
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.post("/{asset_id}/reevaluate-preview")
def asset_reevaluate_preview(asset_id: int, db: Session = Depends(get_db),
                             user=Depends(require_login)):
    """只算不落库，用于对比参数调整前后的差异。"""
    from app.core.benchmark import resolve_benchmark
    from app.core.pipeline import evaluate
    from fastapi.responses import JSONResponse

    asset = db.get(Asset, asset_id)
    if asset is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    rs = load_ruleset(db)
    bench = resolve_benchmark(db, asset, rs)
    preview = evaluate(asset, rs, bench)
    return JSONResponse({
        "current": {"score": asset.total_score, "grade": asset.grade},
        "preview": {"score": preview.total_score, "grade": preview.grade,
                    "status": preview.status},
        "delta": round(preview.total_score - (asset.total_score or 0), 2),
    })
