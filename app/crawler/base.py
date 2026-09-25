"""采集适配器基类。

每个平台实现两件事：
  1. `list_url(page)` / `detail_url(raw_id)` —— 该平台的 URL 构造规则；
  2. `parse_list(html)` / `parse_detail(html)` —— 页面 → 标准字段。

页面选择器写在类属性 `selector_map` 里而不是硬编码在逻辑中，这样
平台改版时运维改一行配置即可，不必动代码。
"""
from __future__ import annotations

import abc
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.config import CRAWLER_TIMEOUT, CRAWLER_USER_AGENT


@dataclass
class RawListing:
    """适配器输出的统一中间结构（字段名 + 平台原始载荷）。"""

    external_id: str | None = None
    title: str = ""
    detail_url: str | None = None
    raw_text: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


class AdapterNotConfigured(RuntimeError):
    """适配器尚未补齐真实页面解析或未确认授权。"""


class BaseAdapter(abc.ABC):
    # ------------------------------------------------------------ 元信息
    platform: str = ""
    display_name: str = ""
    homepage: str = ""
    requires_authorization: bool = True

    # ------------------------------------------------------------ 开关
    # 只有把 enabled 置 True 且（如平台要求）authorization_confirmed 置 True，
    # 才会真正发起网络请求。默认关闭是刻意的：避免误触发对第三方站点的批量访问。
    enabled: bool = False
    authorization_confirmed: bool = False

    # ------------------------------------------------------------ 请求参数
    request_interval: float = 3.0   # 同平台两次请求最小间隔（秒）
    timeout: float = CRAWLER_TIMEOUT
    user_agent: str = CRAWLER_USER_AGENT

    # ------------------------------------------------------------ 页面映射
    # key = 标准字段名，value = CSS 选择器（列表页取文本）
    selector_map: dict[str, str] = {}
    list_url_template: str = ""
    detail_url_template: str = ""

    _last_request_at: float = 0.0

    # ============================================================ 对外
    def fetch(self, since: datetime | None = None, limit: int = 200) -> list[RawListing]:
        """采集入口。

        默认走真实采集；把子类的 `use_sample_data` 置 True 则返回内置样本，
        用于在未获授权 / 未补选择器时跑通全链路与演示。
        """
        if self.use_sample_data:
            from app.crawler.samples import sample_listings

            return [RawListing(**r) for r in sample_listings(self.platform, limit)]

        if not self.enabled:
            raise AdapterNotConfigured(
                f"[{self.display_name}] 采集开关未打开。"
                f"请在后台『采集管理』中确认已获授权并启用。")
        if self.requires_authorization and not self.authorization_confirmed:
            raise AdapterNotConfigured(
                f"[{self.display_name}] 尚未确认采集授权，已拒绝出网采集。")
        return self.fetch_live(since=since, limit=limit)

    use_sample_data: bool = True

    @abc.abstractmethod
    def fetch_live(self, since: datetime | None = None, limit: int = 200) -> list[RawListing]:
        """真实采集实现。子类在这里做分页、限速、解析。"""
        raise NotImplementedError

    # ============================================================ 工具
    def list_url(self, page: int = 1) -> str:
        return self.list_url_template.format(page=page)

    def detail_url(self, raw_id: str) -> str:
        return self.detail_url_template.format(raw_id=raw_id)

    def _throttle(self) -> None:
        gap = time.time() - self._last_request_at
        if gap < self.request_interval:
            time.sleep(self.request_interval - gap)
        self._last_request_at = time.time()

    def _get(self, url: str) -> str:
        """限速 + 超时的 GET。集中在一处，方便统一加代理 / 重试 / 落盘留证。"""
        import httpx

        self._throttle()
        resp = httpx.get(url, timeout=self.timeout,
                         headers={"User-Agent": self.user_agent}, follow_redirects=True)
        resp.raise_for_status()
        resp.encoding = resp.encoding or "utf-8"
        return resp.text

    @staticmethod
    def _soup(html: str):
        from bs4 import BeautifulSoup

        return BeautifulSoup(html, "lxml")

    def parse_list(self, html: str) -> list[RawListing]:
        """列表页解析。子类按需覆盖；selector_map 已给出字段映射骨架。"""
        soup = self._soup(html)
        row_sel = self.selector_map.get("_row")
        if not row_sel:
            raise AdapterNotConfigured(
                f"[{self.display_name}] 尚未配置列表页行选择器（selector_map['_row']）")
        out: list[RawListing] = []
        for node in soup.select(row_sel):
            rec: dict[str, str] = {}
            for field_name, sel in self.selector_map.items():
                if field_name.startswith("_"):
                    continue
                el = node.select_one(sel)
                if el is not None:
                    rec[field_name] = el.get_text(strip=True)
            out.append(RawListing(
                external_id=rec.get("external_id"),
                title=rec.get("title", ""),
                detail_url=rec.get("detail_url"),
                payload=rec,
            ))
        return out

    def parse_detail(self, html: str) -> dict[str, Any]:
        """详情页解析。默认把整页可视文本作为 raw_text 交给文本结构化器。"""
        soup = self._soup(html)
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = re.sub(r"\n{2,}", "\n", soup.get_text("\n", strip=True))
        return {"raw_text": text}


# ==================================================================== 工具函数
_NUM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)")


def to_float(value: Any, unit_scale: float = 1.0) -> float | None:
    """从任意文本里抽出第一个数字，并识别「万 / 亿」量词。

    例：'1,234.5万' → 12_345_000.0；'1.2万平方米' → 12000.0
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) * unit_scale
    text = str(value)
    m = _NUM_RE.search(text)
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    if "亿" in text:
        num *= 100_000_000
    elif "万" in text:
        num *= 10_000
    return num * unit_scale


def parse_dt(value: Any) -> datetime | None:
    """尽量把各种日期写法解析成 datetime；失败返回 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    text = text.replace("年", "-").replace("月", "-").replace("日", " ")
    text = text.replace("/", "-").replace(".", "-").strip()
    text = re.sub(r"\s+", " ", text)
    for fmt in (
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S", "%m-%d %H:%M",
    ):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.year == 1900:      # '%m-%d' 形式补齐当前年
                now = datetime.now()
                dt = dt.replace(year=now.year)
            return dt
        except ValueError:
            continue
    return None
