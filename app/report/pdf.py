"""模块6：智能标的报告 PDF 导出。

报告结构严格对齐 PRD 模块6的六项固定内容：
  1. 标的基础信息、挂牌信息
  2. AI 综合得分、标的等级
  3. 五大维度评分明细
  4. 净租售比测算明细
  5. 风险标签、优势标签、系统初筛结论
  6. 固定合规免责声明（强制自带，不可关闭）

中文字体：优先使用系统 TTF（Windows 微软雅黑 / 黑体），其次 reportlab 内置
CID 字体 STSong-Light，最后才退回 Helvetica。三级降级保证任何机器都能出报告。
"""
from __future__ import annotations

import io
import os
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app import ENGINE_VERSION
from app.config import CJK_FONT_CANDIDATES, CJK_FONT_NAME, REPORT_DIR
from app.core import constants as C

_FONT_CACHE: str | None = None

# 主题色（浅色专业风，与后台界面一致）
INK = colors.HexColor("#1f2937")
MUTED = colors.HexColor("#6b7280")
LINE = colors.HexColor("#e5e7eb")
BRAND = colors.HexColor("#1d4ed8")
SOFT = colors.HexColor("#f3f6fc")
RISK = colors.HexColor("#b42318")
BENEFIT = colors.HexColor("#067647")
WARN = colors.HexColor("#b54708")


def register_cjk_font() -> str:
    """注册中文字体，返回可用字体名。结果缓存，避免重复注册。"""
    global _FONT_CACHE
    if _FONT_CACHE:
        return _FONT_CACHE

    for path in CJK_FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            if path.lower().endswith(".ttc"):
                font = TTFont(CJK_FONT_NAME, path, subfontIndex=0)
            else:
                font = TTFont(CJK_FONT_NAME, path)
            pdfmetrics.registerFont(font)
            _FONT_CACHE = CJK_FONT_NAME
            return _FONT_CACHE
        except Exception:  # noqa: BLE001 - 换下一个候选字体
            continue

    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        _FONT_CACHE = "STSong-Light"
    except Exception:  # noqa: BLE001
        _FONT_CACHE = "Helvetica"
    return _FONT_CACHE


def _money(v, unit: str = "元") -> str:
    if v in (None, ""):
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{v:,.2f} {unit}"


def _pct(v, digits: int = 2) -> str:
    if v in (None, ""):
        return "—"
    try:
        return f"{float(v) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return str(v)


def _text(v, default: str = "—") -> str:
    if v in (None, "", [], {}):
        return default
    return str(v)


def _enum_name(mapping: dict[str, str], key) -> str:
    if key in (None, ""):
        return "—"
    return mapping.get(key, str(key))


def _styles(font: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontName=font, fontSize=20,
                                leading=26, textColor=INK, spaceAfter=2),
        "subtitle": ParagraphStyle("st", parent=base["Normal"], fontName=font, fontSize=9.5,
                                   leading=13, textColor=MUTED, alignment=TA_CENTER),
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontName=font, fontSize=13,
                             leading=18, textColor=BRAND, spaceBefore=10, spaceAfter=5),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName=font, fontSize=11,
                             leading=15, textColor=INK, spaceBefore=6, spaceAfter=4),
        "body": ParagraphStyle("b", parent=base["Normal"], fontName=font, fontSize=9.5,
                               leading=15, textColor=INK, alignment=TA_JUSTIFY),
        "small": ParagraphStyle("s", parent=base["Normal"], fontName=font, fontSize=8.5,
                                leading=12.5, textColor=MUTED),
        "cell": ParagraphStyle("c", parent=base["Normal"], fontName=font, fontSize=8.8,
                               leading=12.5, textColor=INK, alignment=TA_LEFT),
        "cellb": ParagraphStyle("cb", parent=base["Normal"], fontName=font, fontSize=8.8,
                                leading=12.5, textColor=INK),
        "score": ParagraphStyle("sc", parent=base["Normal"], fontName=font, fontSize=30,
                                leading=34, textColor=BRAND, alignment=TA_CENTER),
        "scorelabel": ParagraphStyle("sl", parent=base["Normal"], fontName=font, fontSize=9,
                                     leading=12, textColor=MUTED, alignment=TA_CENTER),
    }


# ==================================================================== 段落构造
def _kv_table(rows: list[tuple[str, str]], st: dict, cols: int = 2) -> Table:
    """键值表。cols=2 表示每行两组"键:值"。"""
    data = []
    for i in range(0, len(rows), cols):
        chunk = rows[i:i + cols]
        line = []
        for label, value in chunk:
            line.append(Paragraph(f"<b>{label}</b>", st["cell"]))
            line.append(Paragraph(value, st["cell"]))
        while len(line) < cols * 2:
            line.extend([Paragraph("", st["cell"]), Paragraph("", st["cell"])])
        data.append(line)

    widths = []
    for _ in range(cols):
        widths.extend([30 * mm, 55 * mm])
    table = Table(data, colWidths=widths[:cols * 2])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE),
    ]))
    return table


def _grid_table(header: list[str], rows: list[list], st: dict) -> Table:
    data = [[Paragraph(f"<b>{h}</b>", st["cellb"]) for h in header]]
    for r in rows:
        data.append([Paragraph(str(c), st["cell"]) for c in r])
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), SOFT),
        ("TEXTCOLOR", (0, 0), (-1, 0), BRAND),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _score_panel(asset, st: dict) -> Table:
    grade_label = C.GRADES.get(asset.grade or "C", "—")
    color = {"A": BENEFIT, "B": WARN, "C": RISK}.get(asset.grade or "C", INK)
    score_txt = "0.00" if asset.status == "vetoed" else f"{(asset.total_score or 0):.2f}"

    left = Paragraph(
        f"<font color='#6b7280' size=9>AI 综合得分</font><br/>"
        f"<font size=30 color='{color.hexval()}'><b>{score_txt}</b></font>"
        f"<font size=9 color='#6b7280'> / 100</font><br/>"
        f"<font size=9 color='#6b7280'>满分 = 五维权重合计"
        f" {(asset.score_detail or {}).get('weights_total', 100):.0f} 分</font>",
        st["cell"])
    right = Paragraph(
        f"<font size=9 color='#6b7280'>标的等级</font><br/>"
        f"<font size=18 color='{color.hexval()}'><b>{asset.grade or '—'}</b></font>"
        f"<font size=11 color='{color.hexval()}'>  {grade_label}</font><br/>"
        f"<font size=9 color='#6b7280'>状态："
        f"{C.ASSET_STATUSES.get(asset.status, asset.status or '—')}</font>",
        st["cell"])

    table = Table([[left, right]], colWidths=[95 * mm, 70 * mm])
    table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#c7d7f5")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fbff")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LINEAFTER", (0, 0), (0, -1), 0.6, colors.HexColor("#c7d7f5")),
    ]))
    return table


def _tag_paragraph(tags: list[dict], kind: str, st: dict) -> Paragraph:
    if not tags:
        return Paragraph("（无）", st["small"])
    color = "#067647" if kind == "advantage" else "#b42318"
    pieces = []
    for t in tags:
        detail = f"：{t['detail']}" if t.get("detail") else ""
        pieces.append(
            f"<font color='{color}'><b>【{t['name']}】</b></font>"
            f"<font size=8 color='#6b7280'>{detail}</font>")
    return Paragraph("　".join(pieces), st["body"])


# ==================================================================== 主流程
def build_pdf(asset, ruleset, operator: str = "", generated_at: datetime | None = None) -> bytes:
    font = register_cjk_font()
    st = _styles(font)
    now = generated_at or datetime.now()
    report_no = f"RPT-{asset.id or 0}-{now:%Y%m%d%H%M%S}"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=18 * mm,
        title=f"标的筛选报告 {report_no}", author="AI资产智能筛选与评分系统",
    )

    story: list = []

    # ---------------------------------------------------------- 封面区
    story.append(Paragraph("不良资产 AI 智能筛选报告", st["title"]))
    story.append(Paragraph(
        f"报告编号 {report_no}　|　生成时间 {now:%Y-%m-%d %H:%M}　|　"
        f"评估引擎 {ENGINE_VERSION}"
        + (f"　|　导出人 {operator}" if operator else ""), st["subtitle"]))
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=1, color=LINE))
    story.append(Spacer(1, 8))

    # ---------------------------------------------------------- 1. 综合得分
    story.append(Paragraph("一、标的名称与综合得分", st["h1"]))
    story.append(Paragraph(_text(asset.title, "（未命名标的）"), st["body"]))
    story.append(Spacer(1, 6))
    story.append(_score_panel(asset, st))
    story.append(Spacer(1, 8))

    # ---------------------------------------------------------- 2. 基础信息
    story.append(Paragraph("二、标的基础信息与挂牌信息", st["h1"]))
    base_rows = [
        ("数据来源", C.PLATFORMS.get(asset.source_platform, asset.source_platform or "—")),
        ("平台标的编号", _text(asset.external_id)),
        ("资产类型", _enum_name(C.ASSET_TYPES, asset.asset_type)),
        ("所在地区", " / ".join(x for x in (asset.province, asset.city, asset.district) if x)
         or "—"),
        ("标的地址", _text(asset.address)),
        ("建筑面积", f"{_money(asset.area_sqm, '㎡')}" if asset.area_sqm else "—"),
        ("土地面积", f"{_money(asset.land_area_sqm, '㎡')}" if asset.land_area_sqm else "—"),
        ("起拍价", _money(asset.start_price)),
        ("评估价", _money(asset.appraisal_price)),
        ("周边同类成交价", _money(asset.market_price)),
        ("保证金", _money(asset.deposit)),
        ("挂牌时间", f"{asset.listed_at:%Y-%m-%d %H:%M}" if asset.listed_at else "—"),
        ("截止时间", f"{asset.deadline_at:%Y-%m-%d %H:%M}" if asset.deadline_at else "—"),
        ("拍卖轮次", _enum_name(C.AUCTION_ROUNDS, asset.auction_round)),
        ("处置法院", _text(asset.court)),
        ("案号", _text(asset.case_no)),
        ("详情链接", _text(asset.source_url, "—")),
    ]
    story.append(_kv_table(base_rows, st, cols=2))

    story.append(Paragraph("三、权属、司法与占用情况", st["h1"]))
    own_rows = [
        ("土地性质", _enum_name(C.LAND_NATURES, asset.land_nature)),
        ("土地剩余年限", f"{asset.land_remaining_years:g} 年"
         if asset.land_remaining_years is not None else "未载明"),
        ("可否办理不动产登记",
         "是" if asset.registration_ok else ("否" if asset.registration_ok is False else "未载明")),
        ("是否限制转让",
         "是" if asset.transfer_restricted else ("否" if asset.transfer_restricted is False else "未载明")),
        ("合规等级", _enum_name(C.COMPLIANCE_LEVELS, asset.compliance_level)),
        ("抵押数量", f"{int(asset.mortgage_count or 0)} 笔"),
        ("轮候查封数量", f"{int(asset.seal_count or 0)} 轮"),
        ("涉诉案件数量", f"{int(asset.lawsuit_count or 0)} 件"),
        ("司法纠纷频次", f"{int(asset.dispute_freq or 0)} 次"),
        ("租赁情况", _enum_name(C.LEASE_STATUSES, asset.lease_status)),
        ("占用情况", "被占用" if asset.occupied else
         ("未占用" if asset.occupied is False else "未载明")),
        ("可否清场", "可" if asset.can_clear else
         ("不可" if asset.can_clear is False else "未载明")),
        ("欠税", _money(asset.tax_owed)),
        ("土地闲置费", _money(asset.land_idle_fee)),
        ("工程欠款", _money(asset.construction_arrears)),
        ("物业欠费", _money(asset.property_fee_owed)),
        ("欠费合计", _money(asset.total_arrears)),
    ]
    story.append(_kv_table(own_rows, st, cols=2))

    # ---------------------------------------------------------- 3. 一票否决
    veto_hits = asset.veto_hits or []
    if veto_hits:
        story.append(Paragraph("四、一票否决命中情况（最高优先级）", st["h1"]))
        rows = []
        for h in veto_hits:
            rows.append([f"{h.get('code', '')} {h.get('name', '')}",
                         "<br/>".join(h.get("evidences") or []) or "—"])
        story.append(_grid_table(["否决规则", "命中依据"], rows, st))
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            "命中一票否决的标的直接判定为 0 分、C 类淘汰，不参与五维量化打分。",
            st["small"]))
    story.append(PageBreak())

    # ---------------------------------------------------------- 4. 五维明细
    story.append(Paragraph("五、五大维度评分明细", st["h1"]))
    dims = (asset.score_detail or {}).get("dimensions", {}) or {}
    order = (asset.score_detail or {}).get("order") or list(dims.keys())

    if not dims:
        story.append(Paragraph("该标的已触发一票否决，未进入量化打分。", st["body"]))
    else:
        summary_rows = []
        for code in order:
            d = dims.get(code)
            if not d:
                continue
            summary_rows.append([
                d["label"], f"{d['score']:.2f} / {d['weight']:.0f}",
                f"{d['rate'] * 100:.1f}%", d.get("note", ""),
            ])
        summary_rows.append(["合计", f"{(asset.total_score or 0):.2f} / "
                                      f"{(asset.score_detail or {}).get('weights_total', 100):.0f}",
                             f"{asset.grade or '—'} 类", ""])
        story.append(_grid_table(
            ["评分维度", "得分 / 满分", "得分率", "评语"], summary_rows, st))
        story.append(Spacer(1, 8))

        for code in order:
            d = dims.get(code)
            if not d:
                continue
            block = [Paragraph(f"{d['label']}　{d['score']:.2f} / {d['weight']:.0f} 分",
                               st["h2"])]
            rows = []
            for it in d.get("items", []):
                raw = it.get("raw")
                rows.append([
                    it.get("name", ""),
                    it.get("value", ""),
                    "—" if raw is None else f"{raw:g}",
                    f"{it.get('max', '')}",
                    it.get("note", ""),
                ])
            block.append(_grid_table(["子项", "实际值", "得分", "封顶", "计分说明"],
                                     rows, st))
            if d.get("inputs"):
                block.append(Spacer(1, 3))
                block.append(Paragraph(
                    "输入数据：" + "；".join(f"{k}={v}" for k, v in d["inputs"].items()
                                          if v not in (None, "")),
                    st["small"]))
            if d.get("note"):
                block.append(Paragraph(f"结论：{d['note']}", st["small"]))
            story.append(KeepTogether(block))
            story.append(Spacer(1, 6))

    story.append(PageBreak())

    # ---------------------------------------------------------- 5. 净租售比
    story.append(Paragraph("六、净租售比测算明细", st["h1"]))
    rd = asset.rent_detail or {}
    story.append(Paragraph(rd.get("formula", ""), st["small"]))
    story.append(Spacer(1, 4))

    if not rd.get("computable"):
        story.append(Paragraph(
            f"<font color='#b42318'><b>无法完成测算</b></font>："
            f"{_text(rd.get('reason'), '数据不足')}。"
            f"该维度按 0 分计，补充起拍价 / 面积 / 租金数据后可重新评估。",
            st["body"]))
    else:
        rows = []
        for line in rd.get("lines", []):
            rows.append([line.get("sign", ""), line.get("name", ""),
                         _money(line.get("amount")), line.get("note", "")])
        story.append(_grid_table(["", "项目", "金额", "说明"], rows, st))
        story.append(Spacer(1, 5))

        detail_rows = [
            ("净租售比", f"<b>{rd.get('net_rent_ratio_pct', '—')}</b>"),
            ("成交预估价值", _money(rd.get("deal_value"))),
            ("成交价值口径", _text(rd.get("deal_basis"))),
            ("租金数据来源", _text(rd.get("rent_source"))),
            ("区域基准匹配", _text(rd.get("benchmark_matched"))
             + ("（区县级精确匹配）" if rd.get("benchmark_source") == "exact"
                else "（同城均值）" if rd.get("benchmark_source") == "city"
                else "（系统默认系数）")),
        ]
        story.append(_kv_table(detail_rows, st, cols=2))
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            "口径说明：空置损耗统一按年毛租金的一定比例预留；税费与租金调用区域大数据均值；"
            "成交预估价值按起拍价乘以成交系数（法拍资产惯例按底价成交）测算。"
            "以上参数均可在系统后台调整。", st["small"]))

    story.append(PageBreak())

    # ---------------------------------------------------------- 6. 标签与结论
    story.append(Paragraph("七、风险标签与优势标签", st["h1"]))
    story.append(Paragraph("优势标签", st["h2"]))
    story.append(_tag_paragraph(asset.advantage_tags or [], "advantage", st))
    story.append(Spacer(1, 5))
    story.append(Paragraph("风险标签", st["h2"]))
    story.append(_tag_paragraph(asset.risk_tags or [], "risk", st))
    story.append(Spacer(1, 10))

    story.append(Paragraph("八、系统初筛结论", st["h1"]))
    story.append(Paragraph(_text(asset.conclusion, "—"), st["body"]))
    story.append(Spacer(1, 10))

    # ---------------------------------------------------------- 7. 免责声明
    disclaimer = ruleset.disclaimer
    story.append(Paragraph("九、合规免责声明（固定条款）", st["h1"]))
    box = Table([[Paragraph(disclaimer, st["body"])]], colWidths=[165 * mm])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fffaf5")),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#f0c9a4")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    story.append(box)

    # ---------------------------------------------------------- 页脚
    def _footer(canvas, doc_):
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.line(18 * mm, 13 * mm, A4[0] - 18 * mm, 13 * mm)
        canvas.setFont(font, 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 9 * mm,
                          f"{report_no}　AI资产智能筛选与评分系统　{ENGINE_VERSION}")
        canvas.drawRightString(A4[0] - 18 * mm, 9 * mm, f"第 {doc_.page} 页")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def save_pdf(asset, ruleset, operator: str = "", out_dir: Path | None = None) -> Path:
    """生成并落盘，返回文件路径。"""
    target_dir = Path(out_dir or REPORT_DIR)
    target_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    safe_type = C.ASSET_TYPES.get(asset.asset_type, "资产")
    filename = f"标的报告-{asset.id}-{safe_type}-{ts}.pdf"
    path = target_dir / filename
    path.write_bytes(build_pdf(asset, ruleset, operator=operator))
    return path
