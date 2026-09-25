"""平台适配器集合。

新增一个数据源 = 在本目录新增一个文件 + 在 registry.py 注册，公共管道无需改动。
"""
from app.crawler.adapters._html import HtmlListAdapter
from app.crawler.adapters.alipaimai import AlipaimaiAdapter
from app.crawler.adapters.amc import AmcAdapter
from app.crawler.adapters.ggzy import GgzyAdapter
from app.crawler.adapters.jd_auction import JdAuctionAdapter
from app.crawler.adapters.lawsuit_assets import LawsuitAssetsAdapter

__all__ = [
    "HtmlListAdapter",
    "AlipaimaiAdapter",
    "JdAuctionAdapter",
    "LawsuitAssetsAdapter",
    "GgzyAdapter",
    "AmcAdapter",
]
