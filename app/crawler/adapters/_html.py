"""列表页 → 详情页 的通用采集流程。

把"翻页 / 限速 / 逐条取详情 / 组装 RawListing"这段所有平台都一样的逻辑
收在一个基类里；子类只需要给出 URL 规则与页面选择器。
"""
from __future__ import annotations

from datetime import datetime

from app.crawler.base import BaseAdapter, RawListing


class HtmlListAdapter(BaseAdapter):
    """适用于「列表页 + 详情页」结构的平台。"""

    max_pages: int = 5
    # 详情页失败时是否仍保留列表页数据（保留，宁可有残缺也不要丢标的）
    keep_on_detail_error: bool = True

    def fetch_live(self, since: datetime | None = None, limit: int = 200) -> list[RawListing]:
        collected: list[RawListing] = []
        seen_ids: set[str] = set()

        for page in range(1, self.max_pages + 1):
            if len(collected) >= limit:
                break
            html = self._get(self.list_url(page))
            rows = self.parse_list(html)
            if not rows:
                break

            for row in rows:
                if len(collected) >= limit:
                    break
                if row.external_id:
                    if row.external_id in seen_ids:
                        continue
                    seen_ids.add(row.external_id)

                payload = dict(row.payload)
                raw_text = row.raw_text

                if row.detail_url:
                    try:
                        detail = self.parse_detail(self._get(row.detail_url))
                        raw_text = detail.pop("raw_text", "") or raw_text
                        # 详情页字段作为补充，不覆盖列表页已解析的字段
                        for k, v in detail.items():
                            payload.setdefault(k, v)
                    except Exception as exc:  # noqa: BLE001 - 单条失败不应中断整轮采集
                        if not self.keep_on_detail_error:
                            continue
                        payload["_detail_error"] = f"{type(exc).__name__}: {exc}"

                collected.append(RawListing(
                    external_id=row.external_id,
                    title=row.title,
                    detail_url=row.detail_url,
                    raw_text=raw_text,
                    payload=payload,
                ))

        return collected
