"""字段归一化 + 公告文本结构化。

两块能力：
  normalize_record()   把任意来源的字段名/取值映射成标准字段
                       （容错：中文别名、万元/亿元量词、枚举口语表述）
  structurize_text()   把公告原文拆成结构化字段（正则 + 关键词规则，非大模型 —— PRD 六-1）

关键约定：**显式值优先**。
normalize_record 会记下 `_explicit_fields`（表格/适配器明确给出的字段），
enrich_from_text 只填这些字段之外的空白，绝不覆盖人工录入。
"""
from __future__ import annotations

import re
from typing import Any

from app.core import constants as C
from app.crawler.base import parse_dt, to_float

# ==================================================================== 字段别名
FIELD_ALIASES: dict[str, list[str]] = {
    "external_id": ["标的id", "标的编号", "外部id", "项目编号", "编号", "id", "external_id"],
    "title": ["标题", "标的名称", "标的物名称", "拍品名称", "名称", "title"],
    "source_url": ["链接", "详情链接", "标的链接", "url", "source_url"],
    "asset_type": ["资产类型", "标的类型", "资产类别", "类型", "asset_type"],
    "province": ["省份", "省", "province"],
    "city": ["城市", "所在城市", "市", "city"],
    "district": ["区县", "所在区县", "行政区", "区域", "district"],
    "address": ["标的地址", "地址", "坐落", "位置", "address"],
    "area_sqm": ["建筑面积", "面积(㎡)", "面积（㎡）", "面积", "建筑面积(平方米)", "size"],
    "land_area_sqm": ["土地面积", "宗地面积", "占地面积", "用地面积"],
    "start_price": ["起拍价", "起拍价格", "起拍价(元)", "起始价", "挂牌价"],
    "appraisal_price": ["评估价", "评估价格", "评估值", "评估总价"],
    "market_price": ["周边成交价", "市场价", "市场参考价", "参考价", "周边同类成交价"],
    "deposit": ["保证金", "竞买保证金"],
    "increment": ["加价幅度", "竞价幅度"],
    "listed_at": ["挂牌时间", "公告时间", "开始时间", "起拍时间"],
    "deadline_at": ["挂牌截止时间", "截止时间", "结束时间", "报名截止时间"],
    "auction_round": ["拍卖轮次", "轮次", "拍卖次数"],
    "court": ["处置法院", "执行法院", "法院"],
    "case_no": ["案号", "执行案号"],
    "land_nature": ["土地性质", "用地性质", "土地取得方式", "地类"],
    "land_remaining_years": ["土地剩余年限", "剩余使用年限", "剩余年限"],
    "compliance_level": ["合规等级", "手续情况"],
    "registration_ok": ["可否办证", "能否办理不动产登记", "办证情况"],
    "transfer_restricted": ["是否限制转让", "限制转让"],
    "can_supplement_procedure": ["可否补办手续", "能否补办手续"],
    "mortgage_count": ["抵押数量", "抵押笔数", "抵押情况", "抵押"],
    "seal_count": ["轮候查封数量", "查封数量", "查封情况", "查封"],
    "lawsuit_count": ["涉诉案件数", "涉诉数量", "案件数量", "诉讼数量"],
    "dispute_freq": ["司法纠纷频次", "纠纷频次"],
    "lease_status": ["租赁情况", "租赁状态", "租赁"],
    "occupied": ["占用情况", "是否占用", "占用状态", "占用"],
    "occupancy_note": ["占用说明", "占用备注"],
    "can_clear": ["可否清场", "能否清场"],
    "tax_owed": ["欠税", "欠税额", "欠缴税费", "欠缴税款"],
    "land_idle_fee": ["土地闲置费", "闲置费"],
    "construction_arrears": ["工程欠款", "拖欠工程款"],
    "property_fee_owed": ["物业欠费", "欠缴物业费"],
    "scrap_status": ["使用状态", "资产状态"],
    "city_tier": ["城市能级", "城市等级"],
    "industry_support_ratio": ["产业配套", "产业配套成熟度"],
    "rental_demand_ratio": ["出租需求", "租赁需求"],
    "turnover_ratio": ["转手成交率", "换手率", "流动性"],
    "rent_per_sqm_month": ["租金", "月租金单价", "租金单价"],
    "annual_gross_rent_override": ["年毛租金", "年租金"],
    "co_ownership_dispute": ["共有产权争议"],
    "irreversible_seal": ["不可解除查封"],
    "implicit_coownership": ["隐性共有产权提示"],
    "raw_text": ["公告原文", "公告内容", "描述", "详情", "备注", "raw_text"],
}

_ALIAS_INDEX: dict[str, str] = {}
for _std, _aliases in FIELD_ALIASES.items():
    for _a in _aliases:
        _ALIAS_INDEX[_a.strip().lower()] = _std
    _ALIAS_INDEX[_std.lower()] = _std

# ==================================================================== 字段类型分组
MONEY_FIELDS = {
    "start_price", "appraisal_price", "market_price", "deposit", "increment",
    "tax_owed", "land_idle_fee", "construction_arrears", "property_fee_owed",
    "annual_gross_rent_override",
}
RATIO_FIELDS = {"industry_support_ratio", "rental_demand_ratio", "turnover_ratio"}
FLOAT_FIELDS = {"area_sqm", "land_area_sqm", "land_remaining_years",
                "rent_per_sqm_month"} | RATIO_FIELDS
INT_FIELDS = {"mortgage_count", "seal_count", "lawsuit_count", "dispute_freq"}
DATE_FIELDS = {"listed_at", "deadline_at"}
BOOL_FIELDS = {"registration_ok", "transfer_restricted", "can_supplement_procedure",
               "occupied", "can_clear", "implicit_coownership",
               "co_ownership_dispute", "irreversible_seal"}

# 表格未填时字段的"空"表示（用于判断是否已显式提供）
_EMPTY = (None, "", "未载明", "—", "-", "/", "无数据", "N/A", "n/a")

# ==================================================================== 枚举映射
LAND_NATURE_MAP = {
    "出让": "granted", "国有出让": "granted", "招拍挂": "granted", "granted": "granted",
    "划拨": "allocated", "国有划拨": "allocated", "allocated": "allocated",
    "集体": "collective", "集体用地": "collective", "集体土地": "collective",
    "宅基地": "collective", "collective": "collective",
    "未载明": "unknown", "unknown": "unknown",
}

LEASE_MAP = {
    "无租赁": "none", "空置": "none", "无人承租": "none", "none": "none",
    "有租赁": "normal", "已出租": "normal", "带租约": "normal",
    "存在租赁": "normal", "normal": "normal",
    "长期租约": "long_term", "长期有效租约": "long_term", "长期租赁": "long_term",
    "long_term": "long_term",
    "买卖不破租赁": "sale_not_break", "sale_not_break": "sale_not_break",
    "未载明": "unknown", "unknown": "unknown",
}

ROUND_MAP = {
    "一拍": "first", "第一次拍卖": "first", "first": "first",
    "二拍": "second", "第二次拍卖": "second", "second": "second",
    "三拍": "third", "变卖": "third", "third": "third",
    "未载明": "unknown", "unknown": "unknown",
}

SCRAP_MAP = {
    "正常": "normal", "可用": "normal", "正常可用": "normal", "normal": "normal",
    "报废": "equipment_scrapped", "已报废": "equipment_scrapped",
    "设备老旧报废": "equipment_scrapped", "equipment_scrapped": "equipment_scrapped",
    "无开发价值": "land_no_value", "土地无开发价值": "land_no_value",
    "land_no_value": "land_no_value",
    "无法正常使用": "property_unusable", "无法使用": "property_unusable",
    "危房": "property_unusable", "property_unusable": "property_unusable",
}

COMPLIANCE_MAP = {
    "齐全": "full", "一致": "full", "证载用途与现状一致": "full", "full": "full",
    "部分": "partial", "可补正": "partial", "轻度不一致": "partial",
    "partial": "partial",
    "缺失": "none", "无法补正": "none", "手续缺失": "none", "none": "none",
}

CITY_TIER_MAP = {
    "一线": "tier1", "一线城市": "tier1",
    "新一线": "new_tier1", "新一线城市": "new_tier1",
    "二线": "tier2", "二线城市": "tier2",
    "三线": "tier3", "三线城市": "tier3",
    "四线": "tier4", "四线城市": "tier4",
    "五线": "tier5", "五线城市": "tier5", "五线及以下": "tier5",
}

ASSET_TYPE_KEYWORDS = [
    ("industrial", ["厂房", "工业", "仓库", "车间", "厂区", "工业园区"]),
    ("land", ["土地", "地块", "宗地", "建设用地", "工业用地", "商住用地"]),
    ("commercial", ["商铺", "商业", "写字楼", "办公", "门面", "商场", "酒店"]),
    ("residential", ["住宅", "公寓", "别墅", "小区", "住房", "商品房"]),
    ("equipment", ["设备", "机器", "生产线", "车辆"]),
]

_TRUE_WORDS = ("是", "有", "true", "yes", "y", "1", "存在", "占用", "已查封", "受限", "不能", "无法")
_FALSE_WORDS = ("否", "无", "false", "no", "n", "0", "不存在", "空置", "未占用")


def _to_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    for w in _TRUE_WORDS:
        if text == w or text.startswith(w):
            return True
    for w in _FALSE_WORDS:
        if text == w or text.startswith(w):
            return False
    return None


def _enum(value: Any, mapping: dict[str, str], default: str) -> str:
    """先精确匹配，再子串匹配，最后回落默认值。"""
    if value is None:
        return default
    text = str(value).strip()
    if not text or text in _EMPTY:
        return default
    if text in mapping:
        return mapping[text]
    for k, v in mapping.items():
        if k and k in text:
            return v
    return default


def guess_asset_type(text: str) -> str:
    """按关键词判断资产类型。注意顺序：工业/土地优先于商业/住宅，
    因为"工业厂房"里也含"厂房"、"商住用地"里也含"住"。"""
    for code, kws in ASSET_TYPE_KEYWORDS:
        if any(k in text for k in kws):
            return code
    return "other"


# ==================================================================== 主归一
def normalize_record(raw: dict, platform: str = "manual") -> dict:
    """把平台/表格的原始字段映射为标准 Asset 字段。

    返回 dict 中所有标准字段都存在（缺失为 None/默认值），
    另含 `_explicit_fields`（显式提供过的字段名）与 `_unmapped`（未识别的列）。
    """
    mapped: dict[str, Any] = {}
    unmapped: dict[str, Any] = {}
    explicit: list[str] = []

    for key, value in (raw or {}).items():
        if key is None:
            continue
        std = _ALIAS_INDEX.get(str(key).strip().lower())
        if not std:
            unmapped[str(key)] = value
            continue
        text = "" if value is None else str(value).strip()
        if text in ("", "未载明", "—", "-", "/", "N/A", "n/a") and std not in DATE_FIELDS:
            continue           # 空单元格视为"未提供"，交给文本结构化去补
        mapped[std] = value
        explicit.append(std)

    def g(name: str) -> Any:
        return mapped.get(name)

    rec: dict[str, Any] = {
        "source_platform": platform,
        "external_id": (str(g("external_id")) if g("external_id") is not None else None),
        "title": str(g("title") or ""),
        "source_url": g("source_url") or None,
        "province": g("province") or None,
        "city": g("city") or None,
        "district": g("district") or None,
        "address": g("address") or None,
        "court": g("court") or None,
        "case_no": g("case_no") or None,
        "occupancy_note": g("occupancy_note") or None,
        "raw_text": g("raw_text") or None,
    }

    for f in MONEY_FIELDS:
        rec[f] = to_float(g(f))
    for f in FLOAT_FIELDS:
        v = to_float(g(f))
        if v is not None and f in RATIO_FIELDS and v > 1.0:
            v = v / 100.0       # 表格里写 85 表示 85%
        rec[f] = v
    for f in INT_FIELDS:
        v = to_float(g(f))
        rec[f] = int(v) if v is not None else 0
    for f in DATE_FIELDS:
        rec[f] = parse_dt(g(f))
    for f in BOOL_FIELDS:
        rec[f] = _to_bool(g(f))

    rec["land_nature"] = _enum(g("land_nature"), LAND_NATURE_MAP, "unknown")
    rec["lease_status"] = _enum(g("lease_status"), LEASE_MAP, "unknown")
    rec["auction_round"] = _enum(g("auction_round"), ROUND_MAP, "unknown")
    rec["scrap_status"] = _enum(g("scrap_status"), SCRAP_MAP, "normal")
    rec["compliance_level"] = _enum(g("compliance_level"), COMPLIANCE_MAP, "full")
    rec["city_tier"] = _enum(g("city_tier"), CITY_TIER_MAP, None) or None

    at = g("asset_type")
    if at:
        hit = [k for k, v in C.ASSET_TYPES.items()
               if str(at).strip() == k or v == str(at).strip()]
        rec["asset_type"] = hit[0] if hit else guess_asset_type(str(at))
    else:
        hint = " ".join(str(x) for x in
                        (rec.get("title"), rec.get("address"), rec.get("raw_text")) if x)
        rec["asset_type"] = guess_asset_type(hint)

    rec["_explicit_fields"] = sorted(set(explicit) | {"source_platform"})
    rec["_unmapped"] = unmapped
    return rec


# ==================================================================== 文本结构化
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("area_sqm", re.compile(
        r"(?:建筑面积|面积)[^\d]{0,6}([\d,]+(?:\.\d+)?)\s*(?:万)?\s*(?:㎡|平方米|平米|m2|M2)")),
    ("land_area_sqm", re.compile(
        r"(?:土地面积|宗地面积|占地面积)[^\d]{0,6}([\d,]+(?:\.\d+)?)\s*(?:万)?\s*(?:㎡|平方米|平米|m2|M2)")),
    ("start_price", re.compile(
        r"(?:起拍价|起始价|挂牌价)[^\d]{0,6}([\d,]+(?:\.\d+)?)\s*(万元|万|亿元|亿|元)")),
    ("appraisal_price", re.compile(
        r"(?:评估价|评估值|评估总价)[^\d]{0,6}([\d,]+(?:\.\d+)?)\s*(万元|万|亿元|亿|元)")),
    ("market_price", re.compile(
        r"(?:市场价|市场参考价|周边成交价|参考价)[^\d]{0,6}([\d,]+(?:\.\d+)?)\s*(万元|万|亿元|亿|元)")),
    ("deposit", re.compile(
        r"(?:保证金)[^\d]{0,6}([\d,]+(?:\.\d+)?)\s*(万元|万|亿元|亿|元)")),
    ("tax_owed", re.compile(
        r"(?:欠税|欠缴税费|欠缴税款)[^\d]{0,6}([\d,]+(?:\.\d+)?)\s*(万元|万|元)")),
    ("land_idle_fee", re.compile(
        r"(?:土地闲置费|闲置费)[^\d]{0,6}([\d,]+(?:\.\d+)?)\s*(万元|万|元)")),
    ("construction_arrears", re.compile(
        r"(?:工程欠款|拖欠工程款)[^\d]{0,6}([\d,]+(?:\.\d+)?)\s*(万元|万|元)")),
    ("land_remaining_years", re.compile(
        r"(?:剩余使用年限|剩余年限|土地使用年限)[^\d]{0,6}(\d+(?:\.\d+)?)\s*年")),
    ("deadline_at", re.compile(
        r"(?:截止时间|结束时间|报名截止)[：:\s]*"
        r"(\d{4}[-年]\d{1,2}[-月]\d{1,2}日?(?:\s*\d{1,2}:\d{2})?)")),
    ("listed_at", re.compile(
        r"(?:挂牌时间|公告时间|开始时间|起拍时间)[：:\s]*"
        r"(\d{4}[-年]\d{1,2}[-月]\d{1,2}日?(?:\s*\d{1,2}:\d{2})?)")),
]

_LEASE_RULES = [
    ("sale_not_break", ["买卖不破租赁"]),
    ("long_term", ["长期有效租约", "长期租约", "长期租赁", "租期二十年", "租期20年"]),
    ("normal", ["有租赁", "已出租", "带租约", "存在租赁", "承租人"]),
    ("none", ["无租赁", "目前空置", "无人承租"]),
]

_OCCUPY_TRUE = ["被占用", "有人占用", "占用中", "有人居住", "现被使用", "存在占用"]
_OCCUPY_FALSE = ["空置", "无人占用", "已腾空", "房屋空置"]
_CANNOT_CLEAR = ["无法清退", "拒不腾退", "无法清场", "占用无法清场", "拒绝搬离"]

_SCRAP_RULES = [
    ("equipment_scrapped", ["已报废", "设备报废", "老旧报废"]),
    ("land_no_value", ["无开发价值", "不具备开发条件"]),
    ("property_unusable", ["无法正常使用", "危房", "已倒塌"]),
]

_TITLE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("seal_count", re.compile(r"查封[^\d]{0,6}(\d+)\s*(?:轮|次|道)")),
    ("mortgage_count", re.compile(r"抵押[^\d]{0,6}(\d+)\s*(?:笔|次|轮)")),
    ("lawsuit_count", re.compile(r"涉诉[^\d]{0,6}(\d+)\s*(?:件|起)")),
]


def structurize_text(text: str) -> dict[str, Any]:
    """从公告原文抽取结构化字段。只返回**确实抽到**的字段。"""
    if not text:
        return {}
    out: dict[str, Any] = {}
    inferred: list[str] = []

    def put(field_name: str, value: Any) -> None:
        out[field_name] = value
        inferred.append(field_name)

    # ---- 数值 / 日期
    for field_name, pattern in _PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        if field_name in DATE_FIELDS:
            dt = parse_dt(m.group(1))
            if dt:
                put(field_name, dt)
            continue
        # 把单位一起交给 to_float，让它处理"万元/亿元"
        unit = m.group(2) if m.lastindex and m.lastindex >= 2 else ""
        num = to_float(m.group(1) + unit)
        if num is not None:
            put(field_name, num)

    # ---- 枚举推断
    for status, kws in _LEASE_RULES:
        if "lease_status" not in out and any(k in text for k in kws):
            put("lease_status", status)

    for code, kws in _SCRAP_RULES:
        if any(k in text for k in kws):
            put("scrap_status", code)
            break

    if "land_nature" not in out:
        if "集体土地" in text or "集体用地" in text:
            put("land_nature", "collective")
        elif "划拨" in text:
            put("land_nature", "allocated")
        elif "出让" in text:
            put("land_nature", "granted")

    # ---- 布尔推断
    if "occupied" not in out:
        if any(k in text for k in _OCCUPY_TRUE):
            put("occupied", True)
        elif any(k in text for k in _OCCUPY_FALSE):
            put("occupied", False)

    if any(k in text for k in _CANNOT_CLEAR):
        put("can_clear", False)

    if "限制转让" in text or "不得转让" in text or "禁止转让" in text:
        put("transfer_restricted", True)
    if "无法办理不动产登记" in text or "不能办理产权登记" in text or "无法办证" in text:
        put("registration_ok", False)
    if "共有产权无法确权" in text or "权属争议" in text or "权属不明" in text:
        put("co_ownership_dispute", True)
    if "不可解除的查封" in text:
        put("irreversible_seal", True)
    if "隐性共有产权" in text:
        put("implicit_coownership", True)

    # ---- 计数
    for field_name, pattern in _TITLE_PATTERNS:
        if field_name in out:
            continue
        m = pattern.search(text)
        if m:
            put(field_name, int(m.group(1)))

    out["_inferred"] = sorted(set(inferred))
    return out


def enrich_from_text(record: dict) -> dict:
    """把文本抽取结果补齐到**未显式提供**的字段上。

    显式值（表格列 / 适配器字段）优先级最高；这条规则保证人工录入的结果
    不会被正则覆盖，也让整个流程可预期、可复现。
    """
    text = record.get("raw_text") or ""
    extracted = structurize_text(text)
    extracted.pop("_inferred", None)

    explicit = set(record.get("_explicit_fields") or [])
    # 归一化后带默认值的字段，只有在"没被显式提供"且"还是默认值"时才允许填充
    default_values = {"lease_status": "unknown", "land_nature": "unknown",
                      "auction_round": "unknown", "scrap_status": "normal",
                      "compliance_level": "full", "city_tier": None}

    applied: list[str] = []
    for field_name, value in extracted.items():
        if field_name in explicit:
            continue
        current = record.get(field_name)
        is_default = (current is None) or (current == default_values.get(field_name, None)) \
            or (field_name in INT_FIELDS and current == 0)
        if is_default:
            record[field_name] = value
            applied.append(field_name)

    payload = record.get("raw_payload") or {}
    payload.setdefault("_unmapped", record.get("_unmapped") or {})
    payload["_text_inferred"] = sorted(set(applied))
    payload["_text_length"] = len(text)
    record["raw_payload"] = payload
    return record
