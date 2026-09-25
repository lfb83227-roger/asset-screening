"""全国诉讼资产网适配器。

来源：人民法院诉讼资产网公示的司法变卖/拍卖标的。
授权与选择器校正流程同 alipaimai.py。
"""
from __future__ import annotations

from app.crawler.adapters._html import HtmlListAdapter


class LawsuitAssetsAdapter(HtmlListAdapter):
    platform = "lawsuit_assets"
    display_name = "全国诉讼资产网"
    homepage = "https://www.rmfysszc.gov.cn"

    enabled = False
    authorization_confirmed = False
    use_sample_data = True

    request_interval = 5.0
    max_pages = 3

    list_url_template = "https://www.rmfysszc.gov.cn/statichtml/pmgg/index_{page}.html"
    detail_url_template = "https://www.rmfysszc.gov.cn/statichtml/pmgg/{raw_id}.shtml"

    selector_map = {
        "_row": "div.list_box ul li",
        "external_id": "a::attr(data-id)",
        "title": "a",
        "detail_url": "a::attr(href)",
        "start_price": "span.price",
        "city": "span.area",
        "listed_at": "span.date",
    }
