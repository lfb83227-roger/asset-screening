"""京东司法拍卖平台适配器。

授权与选择器校正流程同 alipaimai.py。
"""
from __future__ import annotations

from app.crawler.adapters._html import HtmlListAdapter


class JdAuctionAdapter(HtmlListAdapter):
    platform = "jd_auction"
    display_name = "京东司法拍卖平台"
    homepage = "https://auction.jd.com"

    enabled = False
    authorization_confirmed = False
    use_sample_data = True

    request_interval = 4.0
    max_pages = 5

    list_url_template = "https://auction.jd.com/sifa_list.html?page={page}"
    detail_url_template = "https://auction.jd.com/sifa_detail/{raw_id}.html"

    selector_map = {
        "_row": "ul.pmbl_list li",
        "external_id": "a::attr(data-pid)",
        "title": "a.pm-name",
        "detail_url": "a.pm-name::attr(href)",
        "start_price": "span.pm-price",
        "area_sqm": "span.pm-area",
        "city": "span.pm-city",
        "deadline_at": "span.pm-endtime",
    }
