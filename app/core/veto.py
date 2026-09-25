"""模块2：一票否决风险拦截引擎（最高优先级）。

判定模型：每条规则内 `结构化字段条件 OR 关键词命中`。
* 字段条件是确定性的（土地性质、查封数量、欠费金额…），优先采信；
* 关键词是对公告原文的兜底扫描，一期不引入大模型语义解析（PRD 六-1）。

命中任意一条 → 直接 0 分、C 类淘汰，不进入五维打分。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.ruleset import RuleSet, r2

# 关键词扫描的文本字段
_TEXT_FIELDS = ("title", "raw_text", "occupancy_note", "address")

# ---------------------------------------------------------------- 否定词护栏
# 纯子串匹配最大的坑是否定表述："不属于无法清退情形" 里含 "无法清退"，
# 但语义完全相反。一期不引入大模型语义解析（PRD 六-1），改用一个确定性的
# 轻量护栏：命中关键词后回看前 N 个字，若出现否定词则判定为否定表述，不计命中。
#
# 这个规则是**可审计**的：命中的每一条都会在证据里留下原文片段，
# 复核人员一眼就能看出是被识别还是被否定。
NEGATION_WORDS = ("不属于", "并非", "不存在", "没有", "排除", "免于", "免除",
                  "不", "未", "非", "无")
NEGATION_WINDOW = 6


def _is_negated(text: str, kw: str, index: int) -> bool:
    """判断关键词在该位置是否被前置否定词修饰。"""
    window = text[max(0, index - NEGATION_WINDOW):index]
    return any(w in window for w in NEGATION_WORDS)


def scan_keywords(text: str, keywords: list[str]) -> tuple[list[str], list[dict]]:
    """扫描关键词。返回 (有效命中列表, 被否定护栏拦下的记录)。"""
    hits: list[str] = []
    negated: list[dict] = []
    for kw in keywords or []:
        if not kw:
            continue
        pos = text.find(kw)
        while pos >= 0:
            if kw not in hits:
                snippet = text[max(0, pos - 12):pos + len(kw) + 4]
                if _is_negated(text, kw, pos):
                    negated.append({"keyword": kw, "context": f"…{snippet}…",
                                    "reason": "前置否定词，判定为否定表述"})
                else:
                    hits.append(kw)
                    break
            pos = text.find(kw, pos + 1)
    return hits, negated



def _field_value(asset, field_name: str) -> Any:
    """取字段值；total_arrears 是计算属性，需特殊处理。"""
    if field_name == "total_arrears":
        return asset.total_arrears
    return getattr(asset, field_name, None)


def _match_op(actual: Any, op: str, expected: Any) -> bool:
    if actual is None:
        return False
    try:
        if op == "eq":
            return actual == expected
        if op == "ne":
            return actual != expected
        if op == "in":
            return actual in (expected or [])
        if op == "not_in":
            return actual not in (expected or [])
        if op == "gt":
            return float(actual) > float(expected)
        if op == "gte":
            return float(actual) >= float(expected)
        if op == "lt":
            return float(actual) < float(expected)
        if op == "lte":
            return float(actual) <= float(expected)
        if op == "is_true":
            return bool(actual) is True
        if op == "is_false":
            return actual is False
        if op == "truthy":
            return bool(actual)
        if op == "contains":
            return str(expected) in str(actual)
    except (TypeError, ValueError):
        return False
    return False


def _collect_text(asset) -> str:
    parts = []
    for f in _TEXT_FIELDS:
        v = getattr(asset, f, None)
        if isinstance(v, str) and v:
            parts.append(v)
    return "\n".join(parts)


def _eval_condition(asset, cond: dict) -> tuple[bool, str]:
    """单条字段条件。支持 `and` 子条件（全部成立才算命中）。"""
    field_name = cond.get("field")
    op = cond.get("op", "eq")
    expected = cond.get("value")
    label = cond.get("label") or f"{field_name} {op} {expected}"

    if not _match_op(_field_value(asset, field_name), op, expected):
        return False, ""

    for sub in cond.get("and", []) or []:
        if not _match_op(_field_value(asset, sub.get("field")), sub.get("op", "eq"),
                         sub.get("value")):
            return False, ""
    return True, label


def _fmt_evidence(label: str, cond: dict, asset) -> str:
    """把命中原因写成人能读的句子，便于复核与报告引用。"""
    field_name = cond.get("field")
    actual = _field_value(asset, field_name)
    if isinstance(actual, float):
        actual = r2(actual)
    return f"{label}（实际值：{actual}）"


def evaluate_veto(asset, ruleset: RuleSet) -> list[dict]:
    """返回命中的否决项列表；空列表表示无否决风险。"""
    hits: list[dict] = []
    text = _collect_text(asset)

    for rule in ruleset.veto_rules:
        if not rule.get("enabled", True):
            continue

        evidences: list[str] = []
        matched_keywords: list[str] = []
        suppressed: list[dict] = []

        for cond in rule.get("field_conditions") or []:
            ok, label = _eval_condition(asset, cond)
            if ok:
                evidences.append(_fmt_evidence(label, cond, asset))

        if rule.get("keyword_enabled", True):
            matched_keywords, suppressed = scan_keywords(text, rule.get("keywords") or [])

        if not evidences and not matched_keywords:
            continue

        if matched_keywords:
            evidences.append("公告原文命中关键词：" + "、".join(matched_keywords))

        item = {
            "code": rule["code"],
            "name": rule["name"],
            "description": rule.get("description", ""),
            "evidences": evidences,
            "matched_keywords": matched_keywords,
        }
        if suppressed:
            item["suppressed_keywords"] = suppressed
        hits.append(item)

    hits.sort(key=lambda h: h["code"])
    return hits
