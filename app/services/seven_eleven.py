"""7-Eleven (Philippine Seven Corporation) paperwork: the daily Inventory Transaction Log (what the store received) and the Bad Merchandise slips.

The PDFs are read as text, every line is parsed exactly, and each document is checked against the totals printed on it before it is saved: a document whose
numbers do not add up is refused (never half-saved). A document that is uploaded twice is saved once (the log by its date and store, a slip by its slip id)."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import SevenBadItem, SevenBadSlip, SevenLogLine, SevenSaleLine
from ..timeutil import now_utc

MONEY = r"\(?-?[\d,]+\.\d{2}\)?"
ROW_START = re.compile(r"^\d\d/\d\d/\d{4} \d\d/\d\d/\d{4} \d\d:\d\d [AP]M ")


class SevenError(Exception):
    """The document could not be read exactly; nothing from it is saved."""


def money(text: str) -> float:
    t = text.strip()
    neg = t.startswith("(") or t.startswith("-")
    v = round(float(t.strip("()-").replace(",", "")), 2)
    return -v if neg else v


def pdf_text(raw: bytes) -> str:
    from pypdf import PdfReader
    try:
        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(raw)).pages)
    except Exception as e:  # noqa: BLE001
        raise SevenError("This is not a readable PDF") from e


@dataclass
class LogDoc:
    store_code: str
    store_name: str
    day_from: date
    day_to: date
    lines: list[dict] = field(default_factory=list)
    totals: dict = field(default_factory=dict)       # section -> (cost, retail) as printed
    grand: tuple[float, float] | None = None


@dataclass
class SlipDoc:
    slip_id: str
    document_id: str
    store_code: str
    store_name: str
    slip_date: date
    status: str
    items: list[dict] = field(default_factory=list)
    total_cost: float = 0.0
    total_retail: float = 0.0


def _d(text: str) -> date:
    return datetime.strptime(text.strip(), "%m/%d/%Y").date()


def _store(text: str) -> tuple[str, str]:
    m = re.search(r"(?m)^(?:Store\s+)?(\d{3,6})-(.+?)(?:\s+\d\d/\d\d/\d{4}.*)?$", text)
    if not m:
        raise SevenError("The store could not be found in this document")
    return m.group(1), m.group(2).strip()


def parse_log(text: str) -> LogDoc:
    code, name = _store(text)
    dates = re.findall(r"(?m)^((?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4})$", text)
    if len(dates) < 2:
        raise SevenError("The log dates could not be found")
    day_from, day_to = (datetime.strptime(x, "%B %d, %Y").date() for x in dates[:2])
    doc = LogDoc(code, name, day_from, day_to)
    section = None
    row = re.compile(rf"^(\d\d/\d\d/\d{{4}}) (\d\d/\d\d/\d{{4}}) (\d\d:\d\d [AP]M) (\S+) (.*?)\s?({MONEY})({MONEY})$")
    all_lines = [x.strip() for x in text.splitlines()]
    for n, line in enumerate(all_lines):
        nxt = next((x for x in all_lines[n + 1:] if x), "")
        if re.fullmatch(r"[A-Z][A-Z &/\-]+", line) and ROW_START.match(nxt):     # a section heading is followed by its first dated line (e.g. WAREHOUSE TRANSFER, PRICE CHANGE)
            section = line
            continue
        m = row.match(line)
        if m and section:
            tx, done, at, doc_id, who, retail, cost = m.groups()
            doc.lines.append({"section": section, "tx_date": _d(tx), "completed_at": datetime.strptime(f"{done} {at}", "%m/%d/%Y %I:%M %p"), "document_id": doc_id,
                              "supplier": who.strip(), "retail": money(retail), "cost": money(cost)})
            continue
        t = re.match(rf"^Total: ({MONEY}) ({MONEY})$", line)
        if t and section:
            doc.totals[section] = (money(t.group(1)), money(t.group(2)))
            continue
        g = re.match(rf"^GRAND TOTAL: ({MONEY}) ({MONEY})$", line)
        if g:
            doc.grand = (money(g.group(1)), money(g.group(2)))
    check_log(doc)
    return doc


def check_log(doc: LogDoc) -> None:
    """Every section and the grand total must equal what is printed on the document."""
    cost_all = retail_all = 0.0
    for section in dict.fromkeys([r["section"] for r in doc.lines] + list(doc.totals)):
        rows = [r for r in doc.lines if r["section"] == section]
        cost, retail = round(sum(r["cost"] for r in rows), 2), round(sum(r["retail"] for r in rows), 2)
        printed = doc.totals.get(section)
        if rows and printed is None:
            raise SevenError(f"{section}: the printed total is missing")
        slack = 0.011 + 0.005 * len(rows)       # each printed line is rounded to the centavo, the printed total is not: they may differ by half a centavo per line
        if printed is not None and (abs(printed[0] - cost) > slack or abs(printed[1] - retail) > slack):
            raise SevenError(f"{section}: lines add up to {cost:,.2f} / {retail:,.2f} but the document says {printed[0]:,.2f} / {printed[1]:,.2f}")
        cost_all += cost
        retail_all += retail
    if doc.grand is not None and (abs(doc.grand[0] - cost_all) > 0.011 + 0.005 * len(doc.lines) or abs(doc.grand[1] - retail_all) > 0.011 + 0.005 * len(doc.lines)):
        raise SevenError(f"The grand total is {doc.grand[0]:,.2f} / {doc.grand[1]:,.2f} but the sections add up to {cost_all:,.2f} / {retail_all:,.2f}")


def parse_slip(text: str) -> SlipDoc:
    code, name = _store(text)
    slip_date = re.search(r"(?m)^(\d\d/\d\d/\d{4})$", text)
    ids = re.findall(r"(?m)^([A-Z0-9]{8})$", text)
    docs = re.findall(r"(?m)^(\d{15,})$", text)
    if not slip_date or not ids or not docs:
        raise SevenError("The slip date or number could not be found")
    status = "COMPLETED" if "COMPLETED" in text else ("CANCELLED" if "CANCEL" in text else "")
    doc = SlipDoc(ids[0], docs[0], code, name, _d(slip_date.group(1)), status)
    item = re.compile(rf"^(.+?) (-?\d+(?:\.\d+)?) ([\d,]+\.\d{{2}}) ([\d,]+\.\d{{2}}) ({MONEY}) ({MONEY})(\d{{8}})(\d{{1,3}})$")
    for raw in text.splitlines():
        m = item.match(raw.strip())
        if m:
            nm, qty, uc, ur, tc, tr, code8, no = m.groups()
            doc.items.append({"line_no": int(no), "item_code": code8, "name": nm.strip(), "qty": float(qty), "unit_cost": money(uc), "unit_retail": money(ur),
                              "total_cost": money(tc), "total_retail": money(tr)})
            continue
        t = re.match(rf"^TOTAL: ({MONEY}) ({MONEY})$", raw.strip())
        if t:
            doc.total_cost, doc.total_retail = money(t.group(1)), money(t.group(2))
    check_slip(doc)
    return doc


def check_slip(doc: SlipDoc) -> None:
    if not doc.items:
        raise SevenError("The slip has no items")
    if sorted(i["line_no"] for i in doc.items) != list(range(1, len(doc.items) + 1)):
        raise SevenError("Some item lines of the slip could not be read")
    cost, retail = round(sum(i["total_cost"] for i in doc.items), 2), round(sum(i["total_retail"] for i in doc.items), 2)
    if abs(cost - doc.total_cost) > 0.011 or abs(retail - doc.total_retail) > 0.011:
        raise SevenError(f"The items add up to {cost:,.2f} / {retail:,.2f} but the slip says {doc.total_cost:,.2f} / {doc.total_retail:,.2f}")
    for i in doc.items:
        if abs(round(i["qty"] * i["unit_cost"], 2) - i["total_cost"]) > 0.02 or abs(round(i["qty"] * i["unit_retail"], 2) - i["total_retail"]) > 0.02:
            raise SevenError(f"Line {i['line_no']} ({i['name']}): quantity times price does not match its total")


@dataclass
class SalesDoc:
    store_code: str
    store_name: str
    day: date
    rows: list[dict] = field(default_factory=list)       # category, item_code, barcode, name, qty, amount
    totals: dict = field(default_factory=dict)           # category -> (qty, amount) as printed
    grand: tuple[float, float] | None = None


def parse_sales(text: str) -> SalesDoc:
    """The OR Sales Monitor by Quantity for ONE day: every product line is read, and each category and the grand total must equal the printed ones."""
    code, name = _store(text)
    dates = re.findall(r"(?m)^(\d\d/\d\d/\d{4})$", text)
    if len(dates) < 2:
        raise SevenError("The report dates could not be found")
    d1, d2 = _d(dates[0]), _d(dates[1])
    if d1 != d2:
        raise SevenError(f"This report covers {d1} to {d2}; export one day per file so every day is kept exactly")
    doc = SalesDoc(code, name, d1)
    row = re.compile(r"^([A-Z0-9]{8}) (\d{8,14}) (.+?) (\d+(?:\.\d+)?) ([\d,]+\.\d{2}) ([\d,]+\.\d{2})$")   # expense items have codes like EX000589
    category = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Category :"):
            category = line.split(":", 1)[1].strip()
            continue
        m = row.match(line)
        if m and category:
            item, barcode, nm, qty, amount, _upsd = m.groups()
            doc.rows.append({"category": category, "item_code": item, "barcode": barcode, "name": nm.strip(), "qty": float(qty), "amount": round(float(amount.replace(",", "")), 2)})
            continue
        t = re.match(r"^Total: ([\d,]+(?:\.\d+)?) ([\d,]+\.\d{2})$", line)
        if t and category:
            doc.totals[category] = (float(t.group(1).replace(",", "")), round(float(t.group(2).replace(",", "")), 2))
            continue
        g = re.match(r"^GRAND TOTAL: ([\d,]+(?:\.\d+)?) ([\d,]+\.\d{2})$", line)
        if g:
            doc.grand = (float(g.group(1).replace(",", "")), round(float(g.group(2).replace(",", "")), 2))
        elif re.match(r"^[A-Z0-9]{8} \d{8,14} ", line):
            raise SevenError(f"A product line could not be read: {line[:60]}")
    check_sales(doc)
    return doc


def check_sales(doc: SalesDoc) -> None:
    tq = ta = 0.0
    for cat in dict.fromkeys([r["category"] for r in doc.rows] + list(doc.totals)):
        rows = [r for r in doc.rows if r["category"] == cat]
        q, a = sum(r["qty"] for r in rows), round(sum(r["amount"] for r in rows), 2)
        pr = doc.totals.get(cat)
        if pr is None or abs(pr[0] - q) > 0.001 or abs(pr[1] - a) > 0.011:
            raise SevenError(f"{cat}: the products add up to {q:g} / {a:,.2f} but the report says {pr}")
        tq += q
        ta += a
    if doc.grand is None or abs(doc.grand[0] - tq) > 0.001 or abs(doc.grand[1] - round(ta, 2)) > 0.011:
        raise SevenError(f"The grand total is {doc.grand} but the categories add up to {tq:g} / {ta:,.2f}")


def kind_of(text: str) -> str:
    if "OR SALES MONITOR" in text:
        return "sales"
    if "INVENTORY TRANSACTION LOG" in text:
        return "log"
    if "BAD MERCHANDISE" in text and "BM Slip" in text.replace("\n", " ") or "Itemcode" in text:
        return "slip"
    raise SevenError("This is not a 7-Eleven Inventory Log, Bad Merchandise slip or OR Sales Monitor")


def save(db: Session, raw: bytes, filename: str = "") -> dict:
    """Read one PDF and save it. Returns {kind, status: new|duplicate, ...}. Raises SevenError when it cannot be read exactly."""
    text = pdf_text(raw)
    kind = kind_of(text)
    if kind == "log":
        doc = parse_log(text)
        have = {(r.tx_date, r.document_id, r.section, r.retail) for r in db.scalars(select(SevenLogLine).where(
            SevenLogLine.store_code == doc.store_code, SevenLogLine.tx_date >= doc.day_from, SevenLogLine.tx_date <= doc.day_to))}
        new = 0
        for r in doc.lines:
            if (r["tx_date"], r["document_id"], r["section"], r["retail"]) in have:
                continue
            have.add((r["tx_date"], r["document_id"], r["section"], r["retail"]))
            db.add(SevenLogLine(store_code=doc.store_code, store_name=doc.store_name, report_from=doc.day_from, report_to=doc.day_to, section=r["section"], tx_date=r["tx_date"],
                                completed_at=r["completed_at"], document_id=r["document_id"], supplier=r["supplier"][:160], retail=r["retail"], cost=r["cost"], read_at=now_utc()))
            new += 1
        db.flush()
        return {"kind": "log", "status": "new" if new else "duplicate", "day": doc.day_from, "lines": len(doc.lines), "new": new}
    if kind == "sales":
        sd = parse_sales(text)
        have = {r.item_code for r in db.scalars(select(SevenSaleLine).where(SevenSaleLine.store_code == sd.store_code, SevenSaleLine.sale_date == sd.day))}
        new = 0
        for r in sd.rows:
            if r["item_code"] in have:
                continue
            db.add(SevenSaleLine(store_code=sd.store_code, store_name=sd.store_name, sale_date=sd.day, category=r["category"], item_code=r["item_code"], barcode=r["barcode"],
                                 name=r["name"][:200], qty=r["qty"], amount=r["amount"], read_at=now_utc()))
            have.add(r["item_code"])
            new += 1
        db.flush()
        return {"kind": "sales", "status": "new" if new else "duplicate", "day": sd.day, "lines": len(sd.rows), "new": new}
    doc = parse_slip(text)
    if db.scalar(select(SevenBadSlip.id).where(SevenBadSlip.slip_id == doc.slip_id)):
        return {"kind": "slip", "status": "duplicate", "slip_id": doc.slip_id, "day": doc.slip_date, "lines": len(doc.items), "new": 0}
    slip = SevenBadSlip(slip_id=doc.slip_id, document_id=doc.document_id, store_code=doc.store_code, store_name=doc.store_name, slip_date=doc.slip_date, status=doc.status,
                        total_cost=doc.total_cost, total_retail=doc.total_retail, read_at=now_utc())
    db.add(slip)
    db.flush()
    for i in doc.items:
        db.add(SevenBadItem(slip_id=slip.id, **i))
    db.flush()
    return {"kind": "slip", "status": "new", "slip_id": doc.slip_id, "day": doc.slip_date, "lines": len(doc.items), "new": len(doc.items)}
