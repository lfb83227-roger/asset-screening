"""去重指纹。

两级去重（PRD 模块1-2「自动去重，重复标的仅保留最新版本数据」）：

* `uid`       同源身份：同一平台 + 同一标的编号 → 必然同一 uid。
              没有编号时用 地址+面积+起拍价+类型 兜底。
* `dedup_key` 跨平台身份：同一城市+地址+面积+类型 在不同平台挂牌时，
              视为同一标的的多次曝光，用于合并展示与避免重复尽调。
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

# 地址里的噪声：括号备注、多余空白、全角符号
_NOISE_PATTERNS = [
    re.compile(r"[（(][^）)]{0,40}(?:详见|备注|评估报告|清单|附图)[^）)]{0,40}[）)]"),
    re.compile(r"[（(]\s*[）)]"),
    re.compile(r"\s+"),
    re.compile(r"[，,。;；]+$"),
]


def normalize_address(addr: str | None) -> str:
    """地址归一：全角转半角 → 去括号备注 → 去空白与尾部标点。"""
    if not addr:
        return ""
    text = unicodedata.normalize("NFKC", str(addr))
    for pat in _NOISE_PATTERNS:
        text = pat.sub("", text)
    return text.strip().lower()


def _hash(basis: str, length: int = 16) -> str:
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:length]


def make_uid(platform: str, external_id: str | None, record: dict) -> str:
    """同源唯一键。有平台标的编号就信编号，否则用组合特征。"""
    if external_id:
        return f"{platform[:10]}-{_hash(f'{platform}|id:{external_id}')}"
    basis = "|".join([
        platform,
        normalize_address(record.get("address") or record.get("title")),
        f"{record.get('area_sqm') or ''}",
        f"{record.get('start_price') or ''}",
        str(record.get("asset_type") or ""),
    ])
    return f"{platform[:10]}-{_hash(basis)}"


def make_dedup_key(record: dict) -> str | None:
    """跨平台去重键。地址缺失时返回 None（不做跨平台归并，避免误合并）。"""
    addr = normalize_address(record.get("address"))
    if not addr:
        return None
    area = record.get("area_sqm")
    area_bucket = f"{round(float(area) / 10) * 10:.0f}" if area else ""
    basis = "|".join([
        str(record.get("city") or ""),
        str(record.get("district") or ""),
        addr,
        area_bucket,
        str(record.get("asset_type") or ""),
    ])
    return _hash(basis, 20)


# ==================================================================== 字段合并
# 入库时用于「保留最新版本数据」：新值非空则覆盖，旧值非空而新值为空则保留
_IMMUTABLE = {"id", "uid", "created_at", "first_seen_at"}


def merge_record(existing, incoming: dict, overwrite_non_null: bool = True) -> list[str]:
    """把新采集到的数据合并进已有标的，返回被更新的字段名列表。"""
    updated: list[str] = []
    for field_name, value in incoming.items():
        if field_name.startswith("_") or field_name in _IMMUTABLE:
            continue
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        if not hasattr(existing, field_name):
            continue
        old = getattr(existing, field_name)
        if old == value:
            continue
        if old in (None, "", 0) or overwrite_non_null:
            setattr(existing, field_name, value)
            updated.append(field_name)
    return updated
