"""AMC（国有资产管理公司）公开挂牌资产适配器。

对接对象：四大 AMC 及各地方 AMC 官网/官方公众号发布的对外挂牌资产公告。

这类站点普遍没有统一列表接口，公告以长文形式发布，因此解析策略以
「正文全文抓取 + 文本结构化」为主 —— 也就是把 raw_text 交给
`normalizer.structurize_text()` 去拆字段，而不是依赖精细的 CSS 选择器。

授权与选择器校正流程同 alipaimai.py。
"""
from __future__ import annotations

from app.crawler.adapters._html import HtmlListAdapter


class AmcAdapter(HtmlListAdapter):
    platform = "amc"
    display_name = "AMC公开挂牌资产"
    homepage = "https://www.chinamc.com"

    enabled = False
    authorization_confirmed = False
    use_sample_data = True

    request_interval = 5.0
    max_pages = 2

    list_url_template = "https://www.chinamc.com/notice/index_{page}.html"
    detail_url_template = "https://www.chinamc.com/notice/{raw_id}.html"

    selector_map = {
        "_row": "div.notice-list li",
        "external_id": "a::attr(data-id)",
        "title": "a",
        "detail_url": "a::attr(href)",
        "listed_at": "span.time",
    }

    # AMC 公告以正文为主，详情页解析后整段文本会进入 structurize_text
    detail_use_full_text = True
