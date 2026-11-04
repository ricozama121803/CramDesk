"""Render a week as a 2-part PDF: the log table, then the invoice. Mirrors the example layout."""
from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

from .report import Report
from .timeutil import (
    DAY_ABBR, fmt_hours_num, fmt_hrmin, fmt_long, fmt_md, fmt_mdy, fmt_money, fmt_range,
)

W, H = letter  # 612 x 792
F, FB = "Helvetica", "Helvetica-Bold"

# log table geometry (points, measured from the top of the page)
COL = [44, 103, 256, 568]   # table left, hours col, description col, table right
PAD = 7
HEAD_TOP, HEAD_H = 88, 28
ROW_MIN = 75
LINE = 17
SIZE = 10
BOTTOM_LIMIT = H - 56


def _y(top: float) -> float:
    return H - top


def _text(c, x, top, s, font=F, size=SIZE, align="left"):
    c.setFont(font, size)
    if align == "right":
        c.drawRightString(x, _y(top), s)
    elif align == "center":
        c.drawCentredString(x, _y(top), s)
    else:
        c.drawString(x, _y(top), s)


def _watermark(c):
    c.saveState()
    c.setFillColorRGB(0.55, 0.55, 0.6)
    c.setFillAlpha(0.13)
    c.translate(W / 2, H / 2)
    c.rotate(38)
    c.setFont(FB, 110)
    c.drawCentredString(0, -35, "PREVIEW")
    c.restoreState()
    c.saveState()
    c.setFillColorRGB(0.5, 0.5, 0.55)
    c.setFont(F, 7)
    c.drawRightString(W - 24, H - 18, "PREVIEW · not approved yet · crm approve")
    c.restoreState()


# ── page 1: log ──────────────────────────────────────────────────────────────

def _table_header(c, top):
    c.rect(COL[0], _y(top + HEAD_H), COL[3] - COL[0], HEAD_H)
    for x in COL[1:3]:
        c.line(x, _y(top), x, _y(top + HEAD_H))
    _text(c, COL[1] + PAD, top + 17, "Hours")
    _text(c, COL[2] + PAD, top + 17, "Description")
    return top + HEAD_H


def _layout_day(block) -> tuple[list[tuple[int, float, str]], float]:
    """-> ([(column, offset_from_row_top, text)], content height)"""
    desc_w = COL[3] - COL[2] - 2 * PAD
    items: list[tuple[int, float, str]] = []
    y = 18.0
    for i, s in enumerate(block.sessions):
        if i:
            y += LINE * 0.5  # breathing room between sessions
        items.append((1, y, fmt_range(s.start, s.end)))
        lines = [ln for note in s.notes for ln in (simpleSplit(note, F, SIZE, desc_w) or [""])]
        for j, ln in enumerate(lines or [""]):
            items.append((2, y + j * LINE, ln))
        y += LINE * max(1, len(lines))
    d = block.day
    items += [(0, 18.0, DAY_ABBR[d.weekday()]), (0, 18.0 + LINE, fmt_md(d))]
    return items, max(y, 18.0 + 2 * LINE)


def _log_pages(c, r: Report, cfg: dict, preview: bool):
    you = cfg["you"]
    if preview:
        _watermark(c)
    who = you["name"] + (f"-{you['title']}" if you.get("title") else "")
    _text(c, 77, 42, who)
    _text(c, 77, 60, f"{fmt_mdy(r.first_day)} - {fmt_mdy(r.last_day)}")
    _text(c, 264, 60, cfg["invoice"]["doc_title"])

    c.setLineWidth(1)
    top = _table_header(c, HEAD_TOP)
    for block in r.days:
        items, content_h = _layout_day(block)
        h = max(ROW_MIN, content_h + 22)
        if top + h > BOTTOM_LIMIT:
            c.showPage()
            if preview:
                _watermark(c)
            c.setLineWidth(1)
            top = _table_header(c, 50)
        c.rect(COL[0], _y(top + h), COL[3] - COL[0], h)
        for x in COL[1:3]:
            c.line(x, _y(top), x, _y(top + h))
        for col, dy, s in items:
            _text(c, COL[col] + PAD + (1 if col == 0 else 0), top + dy, s)
        top += h

    if top + 50 > BOTTOM_LIMIT:
        c.showPage()
        top = 40
    _text(c, 77, top + 23, f"Total Hours Worked: {fmt_hrmin(r.total_minutes)}")
    billed = f"Total Billed: {fmt_hrmin(r.billed_minutes)}"
    bw = max(117, c.stringWidth(billed, F, SIZE) + 40)
    c.saveState()
    c.setFillColorRGB(1, 1, 0)
    c.rect(75, _y(top + 47), bw, 16, stroke=0, fill=1)
    c.restoreState()
    _text(c, 77, top + 42, billed)


# ── page 2: invoice ──────────────────────────────────────────────────────────

INV_LEFT, INV_VAL, INV_RIGHT = 60, 180, 535
INV_COLS = {"desc": 60, "rate": 326, "hours": 418, "amount": 492}
INV_SIZE = 10
INV_BLOCK = 10.75
INV_LINE = 19


def _invoice_page(c, r: Report, cfg: dict, preview: bool):
    you, client, inv = cfg["you"], cfg["client"], cfg["invoice"]
    if preview:
        _watermark(c)

    _text(c, INV_RIGHT, 49, "INVOICE", FB, 18, "right")
    _text(c, INV_RIGHT, 70, f"#{r.invoice_number}", F, INV_SIZE, "right")
    _text(c, INV_RIGHT, 95, f"Issued: {fmt_long(r.issued)}", F, INV_SIZE, "right")
    _text(c, INV_RIGHT, 110, f"Due: {fmt_long(r.due)}", F, INV_SIZE, "right")

    def block(label, top, lines):
        _text(c, INV_LEFT, top, label, FB, INV_BLOCK)
        for i, ln in enumerate(lines):
            _text(c, INV_VAL, top + i * INV_LINE, ln, F, INV_BLOCK)
        return top + len(lines) * INV_LINE

    billed_to = [client["name"], *client.get("address", []), client.get("email", "")]
    end = block("BILLED TO:", 128, [ln for ln in billed_to if ln])
    pay_to = [you["name"], *you.get("address", []), you.get("email", "")]
    end = block("PAY TO:", max(end + 33, 280), [ln for ln in pay_to if ln])
    top = end + 20
    if you.get("paypal"):
        block("PayPal:", top + 2, [you["paypal"]])
        top += 38

    # line-item table
    head = top - 1
    _text(c, INV_COLS["desc"], head, "DESCRIPTION", FB, INV_SIZE)
    _text(c, INV_COLS["rate"], head, "RATE", FB, INV_SIZE)
    _text(c, INV_COLS["hours"], head, "HOURS", FB, INV_SIZE, "center")
    _text(c, INV_COLS["amount"], head, "AMOUNT", FB, INV_SIZE)
    c.setLineWidth(1)
    c.line(57, _y(head + 12), 557, _y(head + 12))

    row = head + 34
    first = f"{inv['role']} -" if r.summary else inv["role"]
    desc_lines = [first, *simpleSplit(r.summary, F, INV_SIZE, INV_COLS["rate"] - INV_COLS["desc"] - 16)]
    for i, ln in enumerate(desc_lines):
        _text(c, INV_COLS["desc"], row + i * 16, ln, F, INV_SIZE)
    _text(c, INV_COLS["rate"], row, f"{fmt_money(r.rate, cents=False)}/hr", F, INV_SIZE)
    _text(c, INV_COLS["hours"], row, fmt_hours_num(r.billed_minutes), F, INV_SIZE, "center")
    _text(c, INV_COLS["amount"], row, fmt_money(r.amount, cents=False), F, INV_SIZE)

    rule = row + 16 * (len(desc_lines) - 1) + 32
    c.line(57, _y(rule), 557, _y(rule))
    _text(c, INV_COLS["desc"], rule + 22, "TOTAL", FB, 12)
    _text(c, 551, rule + 22, fmt_money(r.amount), FB, 12, "right")

    foot = rule + 106
    for i, ln in enumerate(inv.get("footer", [])):
        _text(c, INV_COLS["desc"], foot + i * 27, ln.format(days=inv["due_business_days"]), F, 10)


# ── entry point ──────────────────────────────────────────────────────────────

def render(r: Report, cfg: dict, path: Path, preview: bool = False) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setTitle(f"{cfg['invoice']['doc_title']} {fmt_mdy(r.monday)} - {fmt_mdy(r.sunday)}")
    c.setAuthor(cfg["you"]["name"])
    c.setCreator("crm")
    _log_pages(c, r, cfg, preview)
    c.showPage()
    _invoice_page(c, r, cfg, preview)
    c.showPage()
    c.save()
    return path
