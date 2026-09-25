"""批量导入（CSV / Excel）—— 采集之外的补数通道。

支持的输入：
* `.csv`  —— 自动尝试 UTF-8-BOM / UTF-8 / GBK 三种编码（运营导出的表格常见 GBK）
* `.xlsx` —— openpyxl 读取首个工作表

表头容错：列名走 normalizer 的别名表，所以「起拍价 / 起拍价格 / 起拍价(元)」
都能识别；无法识别的列会记录在 raw_payload._unmapped 里回显给用户，
而不是静默丢弃 —— 导入工具最忌讳的就是"悄悄少了一列"。
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.crawler.normalizer import FIELD_ALIASES
from app.crawler.service import ingest_records
from app.models import ImportBatch

# 导入模板列（按业务填写顺序排列，覆盖核心必填与常用选填）
TEMPLATE_COLUMNS = [
    "标的编号", "标的名称", "资产类型", "省份", "城市", "区县", "标的地址",
    "建筑面积", "土地面积", "起拍价", "评估价", "周边成交价",
    "挂牌时间", "截止时间", "拍卖轮次", "处置法院", "案号",
    "土地性质", "土地剩余年限", "合规等级",
    "租赁情况", "占用情况", "可否清场",
    "抵押数量", "查封数量", "涉诉案件数", "司法纠纷频次",
    "欠税", "土地闲置费", "工程欠款", "物业欠费",
    "租金", "年毛租金", "产业配套", "出租需求", "转手成交率",
    "公告原文",
]

TEMPLATE_HINT_ROW = {
    "标的编号": "MANUAL-2026-0001",
    "标的名称": "XX市XX区XX路厂房",
    "资产类型": "住宅/商业物业/工业厂房/土地/设备/其他",
    "省份": "广东省", "城市": "深圳市", "区县": "南山区",
    "标的地址": "深圳市南山区XX路XX号",
    "建筑面积": "5000（㎡）", "土地面积": "8000（㎡）",
    "起拍价": "600万 或 6000000", "评估价": "1000万",
    "周边成交价": "留空则以评估价为参考价",
    "挂牌时间": "2026-09-01 10:00", "截止时间": "2026-10-01 10:00",
    "拍卖轮次": "一拍/二拍/变卖", "处置法院": "XX市XX区人民法院",
    "案号": "(2026)粤0305执123号",
    "土地性质": "出让/划拨/集体",
    "土地剩余年限": "40",
    "合规等级": "齐全/部分/缺失",
    "租赁情况": "无租赁/有租赁/长期租约/买卖不破租赁",
    "占用情况": "是/否", "可否清场": "是/否",
    "抵押数量": "0", "查封数量": "0", "涉诉案件数": "0", "司法纠纷频次": "0",
    "欠税": "0", "土地闲置费": "0", "工程欠款": "0", "物业欠费": "0",
    "租金": "元/㎡/月，留空取区域大数据均值",
    "年毛租金": "元/年，填写后优先级最高",
    "产业配套": "0~100 或 0~1", "出租需求": "0~100 或 0~1", "转手成交率": "0~100 或 0~1",
    "公告原文": "整段粘贴公告，系统会自动抽取面积/起拍价/欠费/租赁等字段",
}


# ==================================================================== 读文件
def _decode_csv(content: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def read_table(filename: str, content: bytes) -> tuple[list[dict], list[str]]:
    """返回 (行字典列表, 原始表头列表)。"""
    name = (filename or "").lower()

    if name.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
        ws = wb[wb.sheetnames[0]]
        rows = list(ws.iter_rows(values_only=True))
        wb.close()
        if not rows:
            return [], []
        headers = [str(h).strip() if h is not None else "" for h in rows[0]]
        out = []
        for row in rows[1:]:
            if all(c is None or str(c).strip() == "" for c in row):
                continue
            out.append({headers[i]: row[i] for i in range(min(len(headers), len(row)))
                        if headers[i]})
        return out, headers

    # CSV
    text = _decode_csv(content)
    reader = csv.reader(io.StringIO(text))
    all_rows = [r for r in reader if any(str(c).strip() for c in r)]
    if not all_rows:
        return [], []
    headers = [str(h).strip() for h in all_rows[0]]
    out = []
    for row in all_rows[1:]:
        out.append({headers[i]: row[i] for i in range(min(len(headers), len(row)))
                    if headers[i]})
    return out, headers


# ==================================================================== 导入
def import_table(db: Session, filename: str, content: bytes,
                 operator: str = "system") -> ImportBatch:
    batch_no = f"IMP{datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:4].upper()}"
    batch = ImportBatch(batch_no=batch_no, filename=filename, operator=operator)
    db.add(batch)
    db.flush()

    try:
        rows, headers = read_table(filename, content)
    except Exception as exc:  # noqa: BLE001
        batch.total_rows = 0
        batch.failed = 1
        batch.error_report = [{"row": 0, "error": f"文件解析失败：{exc}"}]
        db.flush()
        return batch

    if not rows:
        batch.failed = 1
        batch.error_report = [{"row": 0, "error": "未读取到任何数据行"}]
        db.flush()
        return batch

    known_headers = set()
    for aliases in FIELD_ALIASES.values():
        known_headers.update(a.lower() for a in aliases)
    unknown_headers = [h for h in headers if h and h.strip().lower() not in known_headers]

    from app.crawler.service import build_record
    from app.crawler.base import RawListing

    records, errors = [], []
    for idx, row in enumerate(rows, start=2):   # 第 1 行是表头
        try:
            raw = RawListing(
                external_id=str(row.get("标的编号") or "").strip() or None,
                title=str(row.get("标的名称") or row.get("标题") or "").strip(),
                detail_url=str(row.get("链接") or row.get("详情链接") or "").strip() or None,
                raw_text=str(row.get("公告原文") or row.get("公告内容") or "").strip(),
                payload=row,
            )
            records.append(build_record("manual", raw, batch=batch_no))
        except Exception as exc:  # noqa: BLE001
            errors.append({"row": idx, "error": f"{type(exc).__name__}: {exc}"})

    stats = ingest_records(db, records, trigger="import", batch=batch_no) if records else \
        {"created": 0, "updated": 0}

    batch.total_rows = len(rows)
    batch.imported = stats.get("created", 0) + stats.get("updated", 0)
    batch.failed = len(errors)
    report = list(errors)
    if unknown_headers:
        report.insert(0, {"row": 0, "error": "以下列名未被识别，已跳过："
                                          + "、".join(unknown_headers)})
    batch.error_report = report or None
    db.flush()
    return batch


def template_csv() -> bytes:
    """生成带示例与说明的导入模板。"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(TEMPLATE_COLUMNS)
    writer.writerow([TEMPLATE_HINT_ROW.get(c, "") for c in TEMPLATE_COLUMNS])
    return buf.getvalue().encode("utf-8-sig")   # BOM 让 Excel 正确识别中文
