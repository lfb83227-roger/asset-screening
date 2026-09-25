"""地方公共资源交易中心适配器。

注：地方交易中心多为多站点（各省市独立域名），此处以某典型站点结构为模板；
新增城市时复制本文件、改 `list_url_template` 与 `selector_map` 即可。

授权与选择器校正流程同 alipaimai.py。
"""
from __future__ import annotations

from app.crawler.adapters._html import HtmlListAdapter


class GgzyAdapter(HtmlListAdapter):
    platform = "ggzy"
    display_name = "地方公共资源交易中心"
    homepage = "https://www.ggzy.gov.cn"

    enabled = False
    authorization_confirmed = False
    use_sample_data = True

    request_interval = 5.0
    max_pages = 3

    list_url_template = "https://www.ggzy.gov.cn/queryContent.jspx?page={page}"
    detail_url_template = "https://www.ggzy.gov.cn/information/html/a/{raw_id}.shtml"

    selector_map = {
        "_row": "div.news-list li",
        "external_id": "a::attr(data-id)",
        "title": "a",
        "detail_url": "a::attr(href)",
        "listed_at": "span.time",
        "city": "span.area",
    }
