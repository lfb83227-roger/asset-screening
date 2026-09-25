"""后台管理：评分参数、否决规则、区域基准、用户权限、日志。

「后台可视化调整，无需改代码」这条需求（PRD 五-1/2/3）在这里落地。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import constants as C
from app.core.pipeline import reevaluate_all
from app.core.ruleset import load_ruleset
from app.database import get_db
from app.models import Asset, CrawlLog, OperationLog, RegionBenchmark, SysConfig, User, VetoRule
from app.security import (
    ROLE_LABELS,
    ROLE_LEVEL,
    client_ip,
    hash_password,
    log_operation,
    require_login,
)
from app.webutils import render

router = APIRouter(prefix="/admin", tags=["admin"])

OPERATOR = ROLE_LEVEL["operator"]
ADMIN = ROLE_LEVEL["admin"]


def _need(request: Request, user, level: str = "admin"):
    """返回 None 表示有权限，否则返回一个重定向响应。"""
    if ROLE_LEVEL.get(user.role, 0) < ROLE_LEVEL[level]:
        return RedirectResponse("/?err=权限不足，该操作需要系统管理员角色", status_code=303)
    return None


def _get_config(db: Session, key: str, default):
    row = db.get(SysConfig, key)
    return row.value if row and row.value is not None else default


def _set_config(db: Session, key: str, value) -> None:
    row = db.get(SysConfig, key)
    if row is None:
        db.add(SysConfig(key=key, value=value,
                         note=C.CONFIG_GROUP_LABELS.get(key, "")))
    else:
        row.value = value


# ==================================================================== 参数元数据
SCALAR_GROUPS: list[dict] = [
    {"key": "weights", "label": "五维评分权重", "unit": "分", "step": "1",
     "desc": "五个维度权重之和即总分满分，出厂合计 100 分。调整后建议执行一次全量重算。",
     "fields": [(k, C.DIMENSION_LABELS[k]) for k in C.DEFAULT_WEIGHTS]},
    {"key": "grade_thresholds", "label": "A / B / C 分级阈值", "unit": "分", "step": "1",
     "desc": "得分 ≥ A 阈值 → A 类；≥ B 阈值 → B 类；其余 → C 类。",
     "fields": [("A", "A 类下限"), ("B", "B 类下限")]},
    {"key": "cost_params", "label": "租金 / 税费 / 损耗系数", "unit": "", "step": "0.005",
     "desc": "净租售比测算的全部系数。费率类填小数（0.12 = 12%）。",
     "fields": [("property_tax_rate", "房产税率（从租计征）"),
                ("land_use_tax_per_sqm", "土地使用税（元/㎡/年）"),
                ("maintenance_ratio", "修缮运维费率"),
                ("vacancy_ratio", "空置损耗预留率"),
                ("deal_value_ratio", "成交预估价值系数（× 起拍价）")]},
    {"key": "legal_penalty", "label": "产权司法风险扣分系数", "unit": "分", "step": "0.5",
     "desc": "每项按数量扣分并封顶，累计扣分以 20 分制为基准，再按当前权重折算。",
     "fields": [("mortgage_per", "抵押 · 每笔扣分"), ("mortgage_cap", "抵押 · 封顶"),
                ("seal_per", "查封 · 每轮扣分"), ("seal_cap", "查封 · 封顶"),
                ("lawsuit_per", "涉诉 · 每件扣分"), ("lawsuit_cap", "涉诉 · 封顶"),
                ("dispute_per", "纠纷频次 · 每次扣分"), ("dispute_cap", "纠纷频次 · 封顶")]},
    {"key": "tag_thresholds", "label": "标签触发阈值", "unit": "", "step": "0.01",
     "desc": "折价率/租售比类填小数（0.30 = 30%），数量类填整数，金额填元。",
     "fields": [("high_discount", "高折价线"), ("low_discount", "低折价线"),
                ("rent_pass", "净租售比达标线"), ("rent_warn", "净租售比预警线"),
                ("seal_many", "多轮查封起点"), ("mortgage_many", "多抵押起点"),
                ("lawsuit_many", "涉诉较多起点"), ("land_years_short", "年限不足线"),
                ("deadline_days", "临近截止天数"), ("turnover_poor", "流动性差线"),
                ("big_arrears", "大额欠费线（元）")]},
    {"key": "default_rent_per_sqm_month", "label": "各资产类型租金兜底值", "unit": "元/㎡/月",
     "step": "0.5",
     "desc": "未匹配到区域基准时使用。",
     "fields": [(k, v) for k, v in C.ASSET_TYPES.items()]},
]

ANCHOR_GROUPS: list[dict] = [
    {"key": "price_anchors", "label": "价格折价打分锚点",
     "desc": "格式 [[输入值, 得分], ...]，按输入值升序，区间内线性插值。输入值为折价率小数。",
     "dimension": "price"},
    {"key": "rent_anchors", "label": "净租售比打分锚点",
     "desc": "输入值为净租售比小数。出厂设置：≥5%（0.05）满分 30，4%（0.04）为 18 分并触发大幅扣分。",
     "dimension": "rent"},
    {"key": "land_year_anchors", "label": "土地剩余年限打分锚点",
     "desc": "输入值为剩余年限（年），得分上限对应权属合规维度的「土地剩余年限」子项。",
     "dimension": "ownership"},
]

JSON_GROUPS: list[dict] = [
    {"key": "location_params", "label": "区位流通性子项分值",
     "desc": "城市能级分值表 + 产业配套/出租需求/转手成交率的子项满分。"},
    {"key": "ownership_params", "label": "权属合规补充子项分值",
     "desc": "土地剩余年限满分、无隐性共有产权得分、合规等级分值表。"},
]


# ==================================================================== 参数页
@router.get("/config")
def config_page(request: Request, db: Session = Depends(get_db),
                user=Depends(require_login)):
    ruleset = load_ruleset(db)
    values = {
        "weights": ruleset.weights,
        "grade_thresholds": ruleset.grade_thresholds,
        "cost_params": ruleset.cost_params,
        "legal_penalty": ruleset.legal_penalty,
        "tag_thresholds": ruleset.tag_thresholds,
        "default_rent_per_sqm_month": ruleset.default_rent_per_sqm_month,
    }
    anchors = {
        "price_anchors": ruleset.price_anchors,
        "rent_anchors": ruleset.rent_anchors,
        "land_year_anchors": ruleset.land_year_anchors,
    }
    json_values = {
        "location_params": ruleset.location_params,
        "ownership_params": ruleset.ownership_params,
    }
    tag_states = ruleset.tag_enabled

    can_edit = ROLE_LEVEL.get(user.role, 0) >= ADMIN
    return render(request, "config.html", user=user, active="config",
                  scalar_groups=SCALAR_GROUPS, anchor_groups=ANCHOR_GROUPS,
                  json_groups=JSON_GROUPS, values=values, anchors=anchors,
                  json_values=json_values, tag_registry=C.TAG_REGISTRY,
                  tag_states=tag_states, disclaimer=ruleset.disclaimer,
                  can_edit=can_edit,
                  weights_total=sum(ruleset.weights.values()))


@router.post("/config")
async def config_save(request: Request, db: Session = Depends(get_db),
                      user=Depends(require_login)):
    denied = _need(request, user)
    if denied:
        return denied

    form = await request.form()
    changed: list[str] = []
    errors: list[str] = []

    # ---- 标量字典组
    for group in SCALAR_GROUPS:
        key = group["key"]
        current = _get_config(db, key, C.CONFIG_KEYS.get(key, {})) or {}
        new_value = dict(current)
        for field_name, _label in group["fields"]:
            raw = str(form.get(f"{key}.{field_name}", "")).strip()
            if raw == "":
                continue
            try:
                new_value[field_name] = (int(float(raw)) if group["step"] == "1"
                                         else float(raw))
            except ValueError:
                errors.append(f"{group['label']} · {field_name} 不是有效数字：{raw}")
        if not errors:
            if new_value != current:
                changed.append(group["label"])
            _set_config(db, key, new_value)

    # ---- 锚点表（JSON）
    for group in ANCHOR_GROUPS:
        key = group["key"]
        raw = str(form.get(key, "")).strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
            assert isinstance(parsed, list) and all(
                isinstance(p, list) and len(p) == 2 for p in parsed), "结构必须是 [[x, y], ...]"
            parsed = sorted([[float(p[0]), float(p[1])] for p in parsed], key=lambda p: p[0])
            if parsed != _get_config(db, key, None):
                changed.append(group["label"])
            _set_config(db, key, parsed)
        except (ValueError, AssertionError, TypeError) as exc:
            errors.append(f"{group['label']} JSON 解析失败：{exc}")

    # ---- 复杂 JSON 组
    for group in JSON_GROUPS:
        key = group["key"]
        raw = str(form.get(key, "")).strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
            assert isinstance(parsed, dict), "结构必须是 JSON 对象"
            if parsed != _get_config(db, key, None):
                changed.append(group["label"])
            _set_config(db, key, parsed)
        except (ValueError, AssertionError, TypeError) as exc:
            errors.append(f"{group['label']} JSON 解析失败：{exc}")

    # ---- 标签开关
    tag_states = {t["code"]: (form.get(f"tag.{t['code']}") is not None)
                  for t in C.TAG_REGISTRY}
    if tag_states != _get_config(db, "tag_enabled", {}):
        changed.append("标签开关")
    _set_config(db, "tag_enabled", tag_states)

    # ---- 免责声明（不允许被清空）
    # 区分两种情况：表单里"没有这个字段"（部分提交，保持原值）
    # 与"用户把它删空了"（拒绝保存）。这样既不误伤 API/局部提交，也守住了合规底线。
    if "disclaimer" in form:
        disclaimer = str(form.get("disclaimer", "")).strip()
        if not disclaimer:
            errors.append("免责声明不能为空 —— 这是报告的强制合规条款")
        else:
            if disclaimer != _get_config(db, "disclaimer", None):
                changed.append("免责声明文案")
            _set_config(db, "disclaimer", disclaimer)

    if errors:
        db.rollback()
        return RedirectResponse("/admin/config?err=" + "；".join(errors), status_code=303)

    reevaluate = form.get("reevaluate") is not None
    stats = None
    if reevaluate:
        db.flush()
        stats = reevaluate_all(db, trigger="config_change")

    log_operation(db, user, "config_save",
                  detail={"changed": changed, "reevaluated": bool(reevaluate)},
                  ip=client_ip(request))
    db.commit()

    msg = f"已保存：{'、'.join(changed) if changed else '无变化'}"
    if stats:
        msg += (f"；已全量重算 {stats['total']} 条"
                f"（A {stats['A']} / B {stats['B']} / C {stats['C']}）")
    return RedirectResponse(f"/admin/config?ok={msg}", status_code=303)


# ==================================================================== 否决规则
@router.get("/veto")
def veto_page(request: Request, db: Session = Depends(get_db),
              user=Depends(require_login)):
    rules = db.execute(select(VetoRule).order_by(VetoRule.priority, VetoRule.id)
                       ).scalars().all()
    # 各规则命中次数（用于评估规则是否过严/过松）
    hit_counter: dict[str, int] = {}
    for (hits,) in db.execute(select(Asset.veto_hits).where(Asset.veto_hits.is_not(None))).all():
        for h in hits or []:
            hit_counter[h.get("code", "")] = hit_counter.get(h.get("code", ""), 0) + 1

    can_edit = ROLE_LEVEL.get(user.role, 0) >= ADMIN
    return render(request, "veto_rules.html", user=user, active="veto",
                  rules=rules, hit_counter=hit_counter, can_edit=can_edit,
                  field_options=_veto_field_options())


def _veto_field_options() -> list[tuple[str, str]]:
    """可供规则引用的结构化字段清单。"""
    return [
        ("land_nature", "土地性质"),
        ("transfer_restricted", "限制转让"),
        ("registration_ok", "可否办理不动产登记"),
        ("can_supplement_procedure", "划拨地可否补办手续"),
        ("lease_status", "租赁情况"),
        ("occupied", "是否被占用"),
        ("can_clear", "可否清场"),
        ("tax_owed", "欠税金额"),
        ("land_idle_fee", "土地闲置费"),
        ("construction_arrears", "工程欠款"),
        ("total_arrears", "欠费合计（计算值）"),
        ("co_ownership_dispute", "共有产权无法确权"),
        ("irreversible_seal", "不可解除的限制性查封"),
        ("scrap_status", "使用状态"),
        ("seal_count", "轮候查封数量"),
        ("mortgage_count", "抵押数量"),
    ]


@router.post("/veto/save")
async def veto_save(request: Request, db: Session = Depends(get_db),
                    user=Depends(require_login)):
    denied = _need(request, user)
    if denied:
        return denied

    form = await request.form()
    errors: list[str] = []

    for rule in db.execute(select(VetoRule)).scalars().all():
        # 复选框：HTML 语义下"未提交"就是"未勾选"，因此这里按 False 处理是正确的
        if f"enabled.{rule.code}" in form:
            rule.enabled = form.get(f"enabled.{rule.code}") is not None
        if f"kw.{rule.code}" in form:
            rule.keyword_enabled = form.get(f"kw.{rule.code}") is not None

        priority_key = f"priority.{rule.code}"
        if priority_key in form:
            try:
                rule.priority = int(str(form.get(priority_key)))
            except (TypeError, ValueError):
                errors.append(f"{rule.code} 优先级不是整数")

        # 文本框 / 文本域：字段缺失说明是局部提交，保持原值不动
        kw_key = f"keywords.{rule.code}"
        if kw_key in form:
            raw_kws = str(form.get(kw_key, "")).strip()
            rule.keywords = ([k.strip() for k in raw_kws.replace("，", ",").split(",")
                              if k.strip()] if raw_kws else [])

        cond_key = f"conditions.{rule.code}"
        if cond_key in form:
            raw_cond = str(form.get(cond_key, "")).strip()
            if raw_cond:
                try:
                    parsed = json.loads(raw_cond)
                    assert isinstance(parsed, list), "字段条件必须是 JSON 数组"
                    rule.field_conditions = parsed
                except (ValueError, AssertionError, TypeError) as exc:
                    errors.append(f"{rule.code} 字段条件 JSON 解析失败：{exc}")
            else:
                rule.field_conditions = []

    if errors:
        db.rollback()
        return RedirectResponse("/admin/veto?err=" + "；".join(errors), status_code=303)

    log_operation(db, user, "veto_rules_save", ip=client_ip(request))
    db.commit()
    return RedirectResponse("/admin/veto?ok=否决规则已保存（如改动较大建议全量重算）",
                            status_code=303)


@router.post("/veto/new")
def veto_new(request: Request, db: Session = Depends(get_db),
             code: str = Form(...), name: str = Form(...),
             description: str = Form(""), keywords: str = Form(""),
             user=Depends(require_login)):
    denied = _need(request, user)
    if denied:
        return denied

    code = code.strip().upper()
    if db.execute(select(VetoRule).where(VetoRule.code == code)).scalar_one_or_none():
        return RedirectResponse(f"/admin/veto?err=规则编号 {code} 已存在", status_code=303)

    max_priority = db.execute(select(func.max(VetoRule.priority))).scalar_one() or 0
    db.add(VetoRule(
        code=code, name=name.strip(), description=description.strip(),
        priority=max_priority + 10, enabled=True, keyword_enabled=True,
        field_conditions=[],
        keywords=[k.strip() for k in keywords.replace("，", ",").split(",") if k.strip()],
    ))
    log_operation(db, user, "veto_rule_create", target=code)
    db.commit()
    return RedirectResponse(f"/admin/veto?ok=已新增否决规则 {code}", status_code=303)


@router.post("/veto/{rule_id}/delete")
def veto_delete(rule_id: int, request: Request, db: Session = Depends(get_db),
                user=Depends(require_login)):
    denied = _need(request, user)
    if denied:
        return denied
    rule = db.get(VetoRule, rule_id)
    if rule is None:
        return RedirectResponse("/admin/veto?err=规则不存在", status_code=303)
    code = rule.code
    db.delete(rule)
    log_operation(db, user, "veto_rule_delete", target=code)
    db.commit()
    return RedirectResponse(f"/admin/veto?ok=已删除否决规则 {code}", status_code=303)


@router.post("/veto/reevaluate")
def veto_reevaluate(request: Request, db: Session = Depends(get_db),
                    user=Depends(require_login)):
    denied = _need(request, user)
    if denied:
        return denied
    stats = reevaluate_all(db, trigger="veto_change")
    log_operation(db, user, "veto_reevaluate", detail=stats)
    db.commit()
    return RedirectResponse(
        f"/admin/veto?ok=已全量重算 {stats['total']} 条（A {stats['A']} / "
        f"B {stats['B']} / C {stats['C']}，其中否决 {stats['vetoed']} 条）",
        status_code=303)


# ==================================================================== 区域基准
@router.get("/regions")
def regions_page(request: Request, db: Session = Depends(get_db),
                 user=Depends(require_login)):
    keyword = request.query_params.get("q") or ""
    stmt = select(RegionBenchmark).order_by(
        RegionBenchmark.city, RegionBenchmark.district, RegionBenchmark.asset_type)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(RegionBenchmark.city.like(like)
                          | RegionBenchmark.district.like(like))
    rows = db.execute(stmt).scalars().all()
    can_edit = ROLE_LEVEL.get(user.role, 0) >= ADMIN
    return render(request, "regions.html", user=user, active="regions",
                  rows=rows, keyword=keyword, can_edit=can_edit)


@router.post("/regions/save")
async def region_save(request: Request, db: Session = Depends(get_db),
                      user=Depends(require_login)):
    denied = _need(request, user)
    if denied:
        return denied

    form = await request.form()
    rid = form.get("id")

    def _f(name, default=None, cast=float):
        raw = str(form.get(name, "")).strip()
        if raw == "":
            return default
        try:
            return cast(raw)
        except ValueError:
            return default

    province = str(form.get("province", "")).strip()
    city = str(form.get("city", "")).strip()
    district = str(form.get("district", "")).strip() or None
    asset_type = str(form.get("asset_type", "residential")).strip()

    if not city:
        return RedirectResponse("/admin/regions?err=城市不能为空", status_code=303)

    row = db.get(RegionBenchmark, int(rid)) if rid else None
    if row is None:
        row = RegionBenchmark(province=province, city=city, district=district,
                              asset_type=asset_type)
        db.add(row)

    row.province = province or row.province
    row.city = city
    row.district = district
    row.asset_type = asset_type
    row.rent_per_sqm_month = _f("rent_per_sqm_month", 0.0)
    row.land_use_tax_per_sqm = _f("land_use_tax_per_sqm", 0.0)
    row.property_tax_rate = _f("property_tax_rate_pct", 12.0) / 100.0
    row.vacancy_ratio = _f("vacancy_ratio_pct", 10.0) / 100.0
    row.maintenance_ratio = _f("maintenance_ratio_pct", 5.0) / 100.0
    row.city_tier = str(form.get("city_tier", "tier3"))
    row.industry_support_ratio = _f("industry_support_ratio", 50.0) / 100.0
    row.rental_demand_ratio = _f("rental_demand_ratio", 50.0) / 100.0
    row.turnover_ratio = _f("turnover_ratio", 50.0) / 100.0
    row.data_source = str(form.get("data_source", "后台手工维护"))
    row.effective_date = str(form.get("effective_date", "")) or None

    db.flush()
    log_operation(db, user, "region_save", target=f"{city}/{district}/{asset_type}",
                  ip=client_ip(request))
    db.commit()
    return RedirectResponse("/admin/regions?ok=区域基准已保存", status_code=303)


@router.post("/regions/{rid}/delete")
def region_delete(rid: int, request: Request, db: Session = Depends(get_db),
                  user=Depends(require_login)):
    denied = _need(request, user)
    if denied:
        return denied
    row = db.get(RegionBenchmark, rid)
    if row is None:
        return RedirectResponse("/admin/regions?err=记录不存在", status_code=303)
    db.delete(row)
    log_operation(db, user, "region_delete", target=f"region:{rid}")
    db.commit()
    return RedirectResponse("/admin/regions?ok=已删除", status_code=303)


# ==================================================================== 用户与日志
@router.get("/users")
def users_page(request: Request, db: Session = Depends(get_db),
               user=Depends(require_login)):
    users = db.execute(select(User).order_by(User.id)).scalars().all()
    ops = db.execute(
        select(OperationLog).order_by(OperationLog.id.desc()).limit(60)
    ).scalars().all()
    crawl_logs = db.execute(
        select(CrawlLog).order_by(CrawlLog.id.desc()).limit(30)
    ).scalars().all()
    can_edit = ROLE_LEVEL.get(user.role, 0) >= ADMIN
    return render(request, "users.html", user=user, active="users",
                  users=users, ops=ops, crawl_logs=crawl_logs,
                  role_labels=ROLE_LABELS, can_edit=can_edit,
                  role_levels=ROLE_LEVEL)


@router.post("/users/new")
def user_new(request: Request, db: Session = Depends(get_db),
             username: str = Form(...), display_name: str = Form(""),
             role: str = Form("viewer"), password: str = Form(...),
             user=Depends(require_login)):
    denied = _need(request, user)
    if denied:
        return denied

    username = username.strip()
    if len(password) < 6:
        return RedirectResponse("/admin/users?err=密码至少 6 位", status_code=303)
    if db.execute(select(User).where(User.username == username)).scalar_one_or_none():
        return RedirectResponse(f"/admin/users?err=账号 {username} 已存在", status_code=303)
    if role not in ROLE_LEVEL:
        role = "viewer"

    db.add(User(username=username, display_name=display_name.strip() or username,
                role=role, password_hash=hash_password(password), is_active=True))
    log_operation(db, user, "user_create", target=username)
    db.commit()
    return RedirectResponse(f"/admin/users?ok=已创建账号 {username}", status_code=303)


@router.post("/users/{uid}/update")
def user_update(uid: int, request: Request, db: Session = Depends(get_db),
                role: str = Form("viewer"), is_active: str = Form(""),
                password: str = Form(""), user=Depends(require_login)):
    denied = _need(request, user)
    if denied:
        return denied

    target = db.get(User, uid)
    if target is None:
        return RedirectResponse("/admin/users?err=账号不存在", status_code=303)

    if target.username == user.username and is_active == "":
        return RedirectResponse("/admin/users?err=不能停用当前登录账号", status_code=303)

    if role in ROLE_LEVEL:
        target.role = role
    target.is_active = is_active != ""
    pwd_msg = ""
    if password.strip():
        if len(password.strip()) < 6:
            return RedirectResponse("/admin/users?err=密码至少 6 位", status_code=303)
        target.password_hash = hash_password(password.strip())
        pwd_msg = "（密码已重置）"

    log_operation(db, user, "user_update", target=target.username,
                  detail={"role": target.role, "active": target.is_active,
                          "password_reset": bool(pwd_msg)})
    db.commit()
    return RedirectResponse(f"/admin/users?ok=已更新账号 {target.username}{pwd_msg}",
                            status_code=303)
