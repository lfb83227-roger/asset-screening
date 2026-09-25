"""阿里拍卖 · 司法拍卖平台适配器。

⚠️ 上线前必做两件事：
  1. 与法务确认平台 robots.txt 与用户协议中关于自动化访问的条款，取得书面授权后
     把 `authorization_confirmed` 与 `enabled` 置 True；
  2. 按平台**当前实际 DOM** 校正 `selector_map`。选择器集中在此，无需改逻辑代码。

在完成上述两步之前，适配器处于样本模式（use_sample_data=True），不会发起任何网络请求。
"""
from __future__ import annotations

from app.crawler.adapters._html import HtmlListAdapter


class AlipaimaiAdapter(HtmlListAdapter):
    platform = "alipaimai"
    display_name = "阿里拍卖司法平台"
    homepage = "https://sf.taobao.com"

    enabled = False
    authorization_confirmed = False
    use_sample_data = True

    request_interval = 4.0
    max_pages = 5

    list_url_template = "https://sf.taobao.com/list/50025969.htm?page={page}"
    detail_url_template = "https://sf.taobao.com/item.htm?id={raw_id}"

    # 字段 → CSS 选择器。每行注释说明该字段在此平台大致落位，便于校正。
    selector_map = {
        "_row": "div.items div.item",                       # 列表容器
        "external_id": "a[href*='item.htm']::attr(data-id)",  # 标的编号
        "title": "a.item-title",                            # 标的名称
        "detail_url": "a.item-title::attr(href)",           # 详情链接
        "start_price": "span.item-price",                   # 起拍价（形如 "起拍价 180.0万"）
        "area_sqm": "span.item-area",                       # 建筑面积
        "city": "span.item-location",                       # 所在城市
        "deadline_at": "span.item-endtime",                 # 截止时间
    }
