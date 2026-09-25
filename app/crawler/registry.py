"""适配器注册表。新增平台只需在这里加一行。"""
from __future__ import annotations

from app.core import constants as C
from app.crawler.adapters import (
    AlipaimaiAdapter,
    AmcAdapter,
    GgzyAdapter,
    JdAuctionAdapter,
    LawsuitAssetsAdapter,
)
from app.crawler.base import BaseAdapter

# 手工录入 / 批量导入不是网络适配器，因此不在此注册
ADAPTER_CLASSES: list[type[BaseAdapter]] = [
    AlipaimaiAdapter,
    JdAuctionAdapter,
    LawsuitAssetsAdapter,
    GgzyAdapter,
    AmcAdapter,
]

REGISTRY: dict[str, type[BaseAdapter]] = {c.platform: c for c in ADAPTER_CLASSES}


def get_adapter(platform: str, **overrides) -> BaseAdapter:
    """按平台名实例化适配器；overrides 可临时覆盖 enabled / use_sample_data 等。"""
    cls = REGISTRY.get(platform)
    if cls is None:
        raise KeyError(f"未注册的采集平台：{platform}")
    adapter = cls()
    for k, v in overrides.items():
        if hasattr(adapter, k):
            setattr(adapter, k, v)
    return adapter


def all_adapters() -> list[BaseAdapter]:
    return [c() for c in ADAPTER_CLASSES]


def adapter_status() -> list[dict]:
    """给后台『采集管理』页用的状态清单。"""
    rows = []
    for a in all_adapters():
        rows.append({
            "platform": a.platform,
            "display_name": a.display_name,
            "homepage": a.homepage,
            "enabled": a.enabled,
            "authorization_confirmed": a.authorization_confirmed,
            "mode": "样本数据" if a.use_sample_data else "真实采集",
            "requires_authorization": a.requires_authorization,
            "list_url_template": a.list_url_template,
            "selector_count": len(a.selector_map),
        })
    return rows


def platform_label(platform: str) -> str:
    return C.PLATFORMS.get(platform, platform)
