"""The store sales report workbook, in the layout of StoreHub's own sales report (Sales_Report_TDC-<STORE>.xlsx): one sheet per month with a TOTAL row, a Monthly Summary
whose formulas read the All Sales sheet, and All Sales with every day in date order.

Every column is worked out from the transactions StoreHub sent:
  Total Sales = what was rung up (tax included); Tax; Total Discount (all discounts are manual ones); Total Rounding; Service Charge; Total Sales Returned;
  Net Sales = Total Sales - Tax - Rounding - Returned; Discount % = discount / Net Sales; Manual Discount % = discount / Total Sales; Average Net Sales = Net Sales / Transactions.
Gross Profit is StoreHub's own figure for the day, read from its "Sales over time" report (its API has no item costs). A day StoreHub has not reported yet is worked out as
Net Sales - cost of what was sold, using Retail OS's cost history; days before that history began are left blank rather than guessed.
"""
from __future__ import annotations

import bisect
import io
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Location, Product, ProductCostHistory, SalesDay, SalesDayProduct, SalesTx
from ..timeutil import to_local

HEADERS = ["Date / Time", "Total Sales PHP", "Total Transactions", "Total Discount PHP", "Discount %", "Manual Discount PHP", "Manual Discount %", "Tax PHP",
           "Total Rounding PHP", "Shipping Fee PHP", "Service Charge PHP", "Gross Profit PHP", "Gross Profit %", "Total Sales Returned PHP", "Net Sales PHP",
           "Average Net Sales", "New Customers", "Pax"]
FORMATS = ["General", "#,##0.00", "#,##0", "#,##0.00", "0.00", "#,##0.00", "0.00", "#,##0.00", "#,##0.00", "#,##0.00", "#,##0.00", "#,##0.00", "0.00", "#,##0.00",
           "#,##0.00", "#,##0.00", "#,##0", "#,##0"]
SUMMED = "BCDFHIJKLNOQR"  # the columns the TOTAL row adds up (percentages and the average are not added)
NAVY, PALE = "1F3864", "D9E1F2"


def title_of(loc: Location) -> str:
    """'The Daily Centro - Tabunoc' -> 'TDC-TABUNOC'."""
    n = re.sub(r"^the daily centro\s*[-–]?\s*", "", loc.name or "", flags=re.I).strip()
    return "TDC-" + (re.sub(r"[^A-Za-z0-9]+", "-", n).strip("-").upper() or "STORE")


def file_name(loc: Location) -> str:
    return f"Sales_Report_{title_of(loc)}.xlsx"


def _r2(x: float) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.01"), ROUND_HALF_UP))


def _costs(db: Session):
    """product id -> [(date, cost)] sorted, and the first day Retail OS has any cost history."""
    hist: dict[int, list[tuple[date, float]]] = defaultdict(list)
    for pid, cost, at in db.execute(select(ProductCostHistory.product_id, ProductCostHistory.cost, ProductCostHistory.observed_at).order_by(ProductCostHistory.observed_at)):
        if cost is not None:
            hist[pid].append((to_local(at).date(), cost))
    first = min((h[0][0] for h in hist.values()), default=None)
    return hist, first


def rows_for(db: Session, location_id: int) -> list[list]:
    """One row per day, in the 18 report columns (a blank Gross Profit is None)."""
    days = list(db.scalars(select(SalesDay).where(SalesDay.location_id == location_id).order_by(SalesDay.business_date)))
    agg = {d: (disc, rnd, svc) for d, disc, rnd, svc in db.execute(
        select(SalesTx.business_date, func.coalesce(func.sum(SalesTx.discount), 0), func.coalesce(func.sum(SalesTx.rounded), 0), func.coalesce(func.sum(SalesTx.service_charge), 0))
        .where(SalesTx.location_id == location_id, SalesTx.cancelled.is_(False), SalesTx.tx_type == "Sale").group_by(SalesTx.business_date))}
    units: dict[date, list[tuple[int, float]]] = defaultdict(list)
    for d, pid, u in db.execute(select(SalesDayProduct.business_date, SalesDayProduct.product_id, SalesDayProduct.units).where(SalesDayProduct.location_id == location_id)):
        units[d].append((pid, u))
    hist, first_cost_day = _costs(db)
    current = dict(db.execute(select(Product.id, Product.cost)).all())

    def cost_at(pid: int, d: date) -> float:
        h = hist.get(pid)
        if h:
            i = bisect.bisect_right([x[0] for x in h], d)
            return h[i - 1][1] if i else h[0][1]
        return current.get(pid) or 0.0

    out = []
    for s in days:
        disc, rnd, svc = agg.get(s.business_date, (0.0, 0.0, 0.0))
        total, tax, ret = s.gross_sales, s.tax, s.returns_total
        net = total - ret - tax - rnd
        gp = gpp = None
        if s.gross_profit is not None:  # StoreHub's own figure, read from its report
            gp = _r2(s.gross_profit)
            gpp = _r2(gp / net * 100) if net else 0.0
        elif first_cost_day is not None and s.business_date > first_cost_day:  # a day StoreHub has not reported yet: worked out from our cost history, never for days before it began
            cogs = sum(u * cost_at(pid, s.business_date) for pid, u in units.get(s.business_date, []))
            gp = _r2(net - cogs)
            gpp = _r2(gp / net * 100) if net else 0.0
        out.append([s.business_date, _r2(total), s.tx_count, _r2(disc), _r2(disc / net * 100) if net else 0.0, _r2(disc), _r2(disc / total * 100) if total else 0.0, _r2(tax),
                    _r2(rnd), 0, _r2(svc), gp, gpp, _r2(ret), _r2(net), _r2(net / s.tx_count) if s.tx_count else 0.0, 0, 0])
    return out


def label(d: date) -> str:
    return f"{d.day} {d:%b %Y} ({d:%a})"


def workbook(db: Session, loc: Location) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    rows = rows_for(db, loc.id)
    title = title_of(loc)
    base = Font(name="Arial", size=10)
    bold = Font(name="Arial", size=10, bold=True)
    head_font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor=NAVY)
    pale = PatternFill("solid", fgColor=PALE)
    thin = Border(bottom=Side(style="thin", color="D9D9D9"))

    def sheet_for(ws, data: list[list], with_total: bool = True) -> None:
        ws.merge_cells("A1:R1")
        ws["A1"] = title
        ws["A1"].font = Font(name="Arial", size=14, bold=True)
        ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 24
        for i, h in enumerate(HEADERS, 1):
            c = ws.cell(row=2, column=i, value=h)
            c.font, c.fill, c.alignment = head_font, head_fill, Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.row_dimensions[2].height = 39.75
        for r, row in enumerate(data, 3):
            ws.row_dimensions[r].height = 15
            for i, v in enumerate(row, 1):
                c = ws.cell(row=r, column=i, value=label(v) if i == 1 else v)
                c.font, c.border, c.number_format = base, thin, FORMATS[i - 1]
        last = 2 + len(data)
        if with_total:
            t = last + 1
            ws.row_dimensions[t].height = 15
            for i in range(1, 19):
                c = ws.cell(row=t, column=i)
                c.font, c.fill, c.number_format = bold, pale, FORMATS[i - 1]
            ws.cell(row=t, column=1, value="TOTAL")
            for col in SUMMED:
                ws[f"{col}{t}"] = f"=SUM({col}3:{col}{last})" if data else 0
        ws.freeze_panes = "B3"
        ws.column_dimensions["A"].width = 18
        ws.column_dimensions["B"].width = 11
        for i in range(3, 19):
            ws.column_dimensions[get_column_letter(i)].width = 13

    wb = Workbook()
    wb.remove(wb.active)
    months: dict[tuple[int, int], list[list]] = defaultdict(list)
    for r in rows:
        months[(r[0].year, r[0].month)].append(r)
    spans = []
    start_row = 3  # where each month starts inside All Sales
    for (y, m), data in sorted(months.items()):
        ws = wb.create_sheet(f"{date(y, m, 1):%b %Y}")
        sheet_for(ws, data)
        spans.append((y, m, data, start_row, start_row + len(data) - 1))
        start_row += len(data)

    # Monthly Summary: formulas that read the All Sales sheet
    ms = wb.create_sheet("Monthly Summary")
    ms.merge_cells("A1:J1")
    ms["A1"] = title
    ms["A1"].font, ms["A1"].alignment = Font(name="Arial", size=14, bold=True), Alignment(horizontal="center", vertical="center")
    ms.row_dimensions[1].height = 24
    ms["A2"] = "Monthly Summary (formulas reference the All Sales sheet)"
    ms["A2"].font = Font(name="Arial", size=12, bold=True)
    heads = ["Period", "Days", "Total Sales PHP", "Transactions", "Tax PHP", "Gross Profit PHP", "Net Sales PHP", "Avg Daily Net Sales", "Avg Net Sales / Txn", "Gross Profit % of Net"]
    for i, h in enumerate(heads, 1):
        c = ms.cell(row=4, column=i, value=h)
        c.font, c.fill, c.alignment = head_font, head_fill, Alignment(horizontal="center", vertical="center", wrap_text=True)
    ms.row_dimensions[4].height = 31.5
    fmt = ["General", "0", "#,##0.00", "#,##0", "#,##0.00", "#,##0.00", "#,##0.00", "#,##0.00", "#,##0.00", "0.0%"]
    r = 5
    for y, m, data, a, b in spans:
        first, lastd = data[0][0], data[-1][0]
        end_of_month = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
        part = ""
        if first.day != 1 or lastd != end_of_month:
            part = f" ({first.day}–{lastd.day} {lastd:%b})" if first.day != lastd.day else f" ({first.day} {lastd:%b})"
        ref = lambda col: f"'All Sales'!{col}{a}:{col}{b}"
        vals = [f"{date(y, m, 1):%b %Y}{part}", f"=COUNTA({ref('A')})", f"=SUM({ref('B')})", f"=SUM({ref('C')})", f"=SUM({ref('H')})", f"=SUM({ref('L')})", f"=SUM({ref('O')})",
                f"=IFERROR(G{r}/B{r},0)", f"=IFERROR(G{r}/D{r},0)", f"=IFERROR(F{r}/G{r},0)"]
        for i, v in enumerate(vals, 1):
            c = ms.cell(row=r, column=i, value=v)
            c.font, c.border, c.number_format = base, thin, fmt[i - 1]
        ms.row_dimensions[r].height = 15
        r += 1
    top = 5
    ms.cell(row=r, column=1, value="Total")
    for i in range(2, 8):
        col = get_column_letter(i)
        ms.cell(row=r, column=i, value=f"=SUM({col}{top}:{col}{r - 1})" if r > top else 0)
    ms.cell(row=r, column=8, value=f"=IFERROR(G{r}/B{r},0)")
    ms.cell(row=r, column=9, value=f"=IFERROR(G{r}/D{r},0)")
    ms.cell(row=r, column=10, value=f"=IFERROR(F{r}/G{r},0)")
    for i in range(1, 11):
        c = ms.cell(row=r, column=i)
        c.font, c.fill, c.border, c.number_format = bold, pale, thin, fmt[i - 1]
    ms.row_dimensions[r].height = 15
    notes = ["All Sales holds every day of this store, from the sales StoreHub sent to Retail OS, sorted by date.",
             "Gross Profit is StoreHub's own figure for each day, read from its Sales over time report. A day it has not reported yet is worked out from Retail OS's cost history "
             + (f"(which starts {_first_cost_label(db)}) or left blank." if _first_cost_label(db) else "or left blank."),
             "Gross Profit % here is Gross Profit / Net Sales, as in StoreHub's own report. Net Sales = Total Sales - Tax - Rounding - Sales Returned."]
    ms.cell(row=r + 2, column=1, value="Notes").font = bold
    for k, n in enumerate(notes, r + 3):
        ms.cell(row=k, column=1, value=n).font = base
        ms.row_dimensions[k].height = 15
    ms.column_dimensions["A"].width = 24
    for i in range(2, 11):
        ms.column_dimensions[get_column_letter(i)].width = 16

    allws = wb.create_sheet("All Sales")
    sheet_for(allws, rows, with_total=False)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _first_cost_label(db: Session) -> str:
    _, first = _costs(db)
    return f"{first.day} {first:%b %Y}" if first else ""
