"""7-Eleven paperwork: Inventory Transaction Logs and Bad Merchandise slips, uploaded as PDFs and checked against their own totals."""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import func, select

from .. import audit
from ..models import SevenBadItem, SevenBadSlip, SevenLogLine, SevenSaleLine
from ..services import seven_eleven as sv
from ..services import seven_replenishment as srep
from .deps import Ctx, need, redirect, render

router = APIRouter()
PAGE = 100
MAX_FILES = 400
MAX_BYTES = 3 * 1024 * 1024


def _cell(v) -> str:
    v = str(v or "")
    return "'" + v if v.lstrip().startswith(("=", "+", "-", "@")) else v


@router.get("/seven-eleven")
def seven_page(request: Request, tab: str = "summary", section: str = "", q: str = "", frm: str = "", to: str = "", page: int = 1, export: str = "", c: Ctx = Depends(need("seven.view"))):
    db = c.db
    tab = tab if tab in ("summary", "order", "sales", "slips", "log") else "summary"
    today = date.today()
    try:
        start, end = date.fromisoformat(frm), date.fromisoformat(to)
        if start > end:
            raise ValueError
    except ValueError:
        first = db.scalar(select(func.min(SevenSaleLine.sale_date))) or db.scalar(select(func.min(SevenLogLine.tx_date)))
        last = db.scalar(select(func.max(SevenSaleLine.sale_date))) or db.scalar(select(func.max(SevenLogLine.tx_date)))
        if tab == "sales" and last and first:
            first = max(first, last - timedelta(days=27))        # the sales tab opens on the last 28 days
        start, end = (first or today - timedelta(days=30)), (last or today)
    page = max(page, 1)
    store = db.scalar(select(SevenLogLine.store_name).limit(1)) or db.scalar(select(SevenBadSlip.store_name).limit(1)) or ""
    ctx = dict(category=request.query_params.get("category", ""), tab=tab, frm=start.isoformat(), to=end.isoformat(), section=section, q=q, page=page, store=store, sections=[s for s in db.scalars(select(SevenLogLine.section).distinct().order_by(SevenLogLine.section))])
    if tab == "summary":
        months: dict[str, dict] = defaultdict(lambda: defaultdict(float))
        log_month = func.strftime("%Y-%m", SevenLogLine.tx_date)
        slip_month = func.strftime("%Y-%m", SevenBadSlip.slip_date)
        for ym, sec, retail, cost in db.execute(select(log_month, SevenLogLine.section, func.sum(SevenLogLine.retail), func.sum(SevenLogLine.cost)).group_by(log_month, SevenLogLine.section)):
            months[ym][sec + "|retail"] += retail or 0
            months[ym][sec + "|cost"] += cost or 0
        slip_months = {ym: (n, cost, retail) for ym, n, cost, retail in db.execute(select(slip_month, func.count(), func.sum(SevenBadSlip.total_cost), func.sum(SevenBadSlip.total_retail)).group_by(slip_month))}
        days = {ym: n for ym, n in db.execute(select(log_month, func.count(func.distinct(SevenLogLine.tx_date))).group_by(log_month))}
        sale_month = func.strftime("%Y-%m", SevenSaleLine.sale_date)
        sales_months = {ym: (n, u, a) for ym, n, u, a in db.execute(select(sale_month, func.count(func.distinct(SevenSaleLine.sale_date)), func.sum(SevenSaleLine.qty), func.sum(SevenSaleLine.amount)).group_by(sale_month))}
        for ym in sales_months:
            months.setdefault(ym, defaultdict(float))
        return render(c, "seven_eleven.html", months=sorted(months.items(), reverse=True), slip_months=slip_months, days=days, sales_months=sales_months, **ctx)
    if tab == "order":
        data = srep.suggest(db)
        rows = data["rows"]
        category = ctx["category"]
        if category:
            rows = [r for r in rows if r["category"] == category]
        if q.strip():
            needle = q.strip().lower()[:80]
            rows = [r for r in rows if needle in r["name"].lower() or needle in r["code"].lower() or needle in (r["barcode"] or "")]
        only = request.query_params.get("only", "order")
        if only == "order":
            rows = [r for r in rows if r["order"]]
        elif only == "review":
            rows = [r for r in rows if r["review"] and r["order"]]
        if export == "csv":
            out = io.StringIO()
            w = csv.writer(out)
            w.writerow(["Item code", "Barcode", "Product", "Category", "Order qty", "Units per day", "Units per day (last 7 days)", "Units sold in period", "Waste units", "Notes"])
            for r in rows:
                w.writerow([r["code"], r["barcode"], _cell(r["name"]), r["category"], r["order"], f"{r['per_day']:.3f}", f"{r['per_day7']:.3f}", f"{r['units']:g}", f"{r['waste']:g}", "; ".join(r["flags"])])
            audit.log(db, actor_type="user", actor_id=c.user.id, action="export_seven_order", entity_type="report", entity_id="seven_order", after={"rows": len(rows), "as_of": str(data["meta"]["as_of"])})
            db.commit()
            return Response("\ufeff" + out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="7-eleven-order-{data["meta"]["as_of"]}.csv"'})
        categories = sorted({r["category"] for r in data["rows"]})
        return render(c, "seven_eleven.html", order_rows=rows[(page - 1) * PAGE: page * PAGE], total=len(rows), pages=max(1, (len(rows) + PAGE - 1) // PAGE), meta=data["meta"], only=only, categories=categories,
                      cfg=srep.settings(db), **ctx)
    if tab == "sales":
        base = select(SevenSaleLine).where(SevenSaleLine.sale_date >= start, SevenSaleLine.sale_date <= end)
        if ctx["category"]:
            base = base.where(SevenSaleLine.category == ctx["category"])
        if q.strip():
            base = base.where(SevenSaleLine.name.ilike(f"%{q.strip()[:80]}%") | SevenSaleLine.item_code.ilike(f"%{q.strip()[:20]}%") | SevenSaleLine.barcode.like(f"%{q.strip()[:20]}%"))
        sub = base.subquery()
        trading = db.scalar(select(func.count(func.distinct(SevenSaleLine.sale_date))).where(SevenSaleLine.sale_date >= start, SevenSaleLine.sale_date <= end)) or 0
        agg = db.execute(select(sub.c.item_code, func.max(sub.c.barcode), func.max(sub.c.name), func.max(sub.c.category), func.sum(sub.c.qty), func.sum(sub.c.amount), func.count(func.distinct(sub.c.sale_date)))
                         .group_by(sub.c.item_code).order_by(func.sum(sub.c.qty).desc(), func.max(sub.c.name))).all()
        products = [{"code": r[0], "barcode": r[1], "name": r[2], "category": r[3], "qty": r[4] or 0, "amount": r[5] or 0, "days_sold": r[6], "upsd": (r[4] or 0) / trading if trading else 0} for r in agg]
        if export == "csv":
            out = io.StringIO()
            w = csv.writer(out)
            w.writerow(["Item code", "Barcode", "Product", "Category", "Units sold", "Amount", "Days sold", "Trading days in period", "Units per sales day"])
            for r in products:
                w.writerow([r["code"], r["barcode"], _cell(r["name"]), r["category"], f"{r['qty']:g}", f"{r['amount']:.2f}", r["days_sold"], trading, f"{r['upsd']:.3f}"])
            audit.log(db, actor_type="user", actor_id=c.user.id, action="export_seven_sales", entity_type="report", entity_id="seven_sales", after={"products": len(products), "from": str(start), "to": str(end)})
            db.commit()
            return Response("\ufeff" + out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="7-eleven-sales-{start}-to-{end}.csv"'})
        categories = list(db.scalars(select(SevenSaleLine.category).distinct().order_by(SevenSaleLine.category)))
        return render(c, "seven_eleven.html", products=products[(page - 1) * PAGE: page * PAGE], total=len(products), pages=max(1, (len(products) + PAGE - 1) // PAGE), trading=trading,
                      totals=(sum(r["qty"] for r in products), sum(r["amount"] for r in products)), categories=categories, **ctx)
    if tab == "slips":
        q_ = select(SevenBadSlip).where(SevenBadSlip.slip_date >= start, SevenBadSlip.slip_date <= end).order_by(SevenBadSlip.slip_date.desc(), SevenBadSlip.slip_id)
        if q.strip():
            q_ = q_.where(SevenBadSlip.id.in_(select(SevenBadItem.slip_id).where(SevenBadItem.name.ilike(f"%{q.strip()[:80]}%") | SevenBadItem.item_code.like(f"%{q.strip()[:20]}%"))) | SevenBadSlip.slip_id.ilike(f"%{q.strip()[:20]}%"))
        rows = list(db.scalars(q_))
        if export == "csv":
            out = io.StringIO(); w = csv.writer(out)
            w.writerow(["Slip date", "Slip ID", "Item code", "Product", "Qty", "Unit cost", "Unit retail", "Total cost", "Total retail"])
            for s in rows:
                for i in s.items:
                    w.writerow([s.slip_date, s.slip_id, i.item_code, _cell(i.name), f"{i.qty:g}", f"{i.unit_cost:.2f}", f"{i.unit_retail:.2f}", f"{i.total_cost:.2f}", f"{i.total_retail:.2f}"])
            audit.log(db, actor_type="user", actor_id=c.user.id, action="export_seven_bad_merchandise", entity_type="report", entity_id="seven_bm", after={"slips": len(rows), "from": str(start), "to": str(end)})
            db.commit()
            return Response("﻿" + out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="bad-merchandise-{start}-to-{end}.csv"'})
        return render(c, "seven_eleven.html", slips=rows[(page - 1) * PAGE: page * PAGE], total=len(rows), pages=max(1, (len(rows) + PAGE - 1) // PAGE), totals=(sum(s.total_cost for s in rows), sum(s.total_retail for s in rows)), **ctx)
    q_ = select(SevenLogLine).where(SevenLogLine.tx_date >= start, SevenLogLine.tx_date <= end)
    if section:
        q_ = q_.where(SevenLogLine.section == section)
    if q.strip():
        q_ = q_.where(SevenLogLine.supplier.ilike(f"%{q.strip()[:80]}%") | SevenLogLine.document_id.ilike(f"%{q.strip()[:40]}%"))
    if export == "csv":
        out = io.StringIO(); w = csv.writer(out)
        w.writerow(["Date", "Completed", "Section", "Document", "Delivered by", "Retail", "Cost"])
        for r in db.scalars(q_.order_by(SevenLogLine.tx_date, SevenLogLine.completed_at)):
            w.writerow([r.tx_date, r.completed_at.strftime("%Y-%m-%d %H:%M") if r.completed_at else "", r.section, _cell(r.document_id), _cell(r.supplier), f"{r.retail:.2f}", f"{r.cost:.2f}"])
        audit.log(db, actor_type="user", actor_id=c.user.id, action="export_seven_inventory_log", entity_type="report", entity_id="seven_log", after={"from": str(start), "to": str(end), "section": section})
        db.commit()
        return Response("﻿" + out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="inventory-log-{start}-to-{end}.csv"'})
    total = db.scalar(select(func.count()).select_from(q_.subquery())) or 0
    sums = db.execute(select(func.coalesce(func.sum(q_.subquery().c.retail), 0), func.coalesce(func.sum(q_.subquery().c.cost), 0))).one()
    rows = list(db.scalars(q_.order_by(SevenLogLine.tx_date.desc(), SevenLogLine.completed_at.desc()).limit(PAGE).offset((page - 1) * PAGE)))
    return render(c, "seven_eleven.html", log=rows, total=total, pages=max(1, (total + PAGE - 1) // PAGE), totals=(sums[1], sums[0]), **ctx)


@router.get("/seven-eleven/slip/{slip_id}")
def seven_slip(slip_id: int, c: Ctx = Depends(need("seven.view"))):
    slip = c.db.get(SevenBadSlip, slip_id)
    if slip is None:
        raise HTTPException(404)
    return render(c, "seven_slip.html", slip=slip)


@router.post("/seven-eleven/upload")
async def seven_upload(request: Request, c: Ctx = Depends(need("seven.import"))):
    """Upload one or many PDFs. Each is read exactly and saved only if its numbers add up; a PDF already saved is skipped."""
    form = await request.form()
    c.check_csrf(str(form.get("csrf", "")))
    files = [f for f in form.getlist("files") if getattr(f, "filename", "")]
    if not files:
        return redirect("/seven-eleven", err="Choose one or more PDF files")
    if len(files) > MAX_FILES:
        return redirect("/seven-eleven", err=f"Upload at most {MAX_FILES} files at a time")
    new_logs = new_slips = dup = 0
    problems: list[str] = []
    for f in files:
        raw = await f.read()
        if len(raw) > MAX_BYTES or not raw.startswith(b"%PDF"):
            problems.append(f"{f.filename[:40]}: not a PDF or too large")
            continue
        try:
            res = sv.save(c.db, raw, f.filename)
        except sv.SevenError as e:
            c.db.rollback()
            problems.append(f"{f.filename[:40]}: {e}")
            continue
        if res["status"] == "duplicate":
            dup += 1
        elif res["kind"] == "log":
            new_logs += 1
        else:
            new_slips += 1
    audit.log(c.db, actor_type="user", actor_id=c.user.id, action="import_seven_eleven", entity_type="import", entity_id="seven_eleven",
              after={"logs": new_logs, "slips": new_slips, "duplicates": dup, "refused": len(problems)})
    c.db.commit()
    msg = f"Saved {new_logs} inventory log(s) and {new_slips} bad merchandise slip(s); {dup} already saved"
    if problems:      # a refused file must never be hidden behind a success message
        return redirect("/seven-eleven", err=msg + ". Not saved - " + "; ".join(problems[:4]) + (f" (+{len(problems) - 4} more)" if len(problems) > 4 else ""))
    return redirect("/seven-eleven", ok=msg)


@router.post("/seven-eleven/settings")
async def seven_settings(request: Request, c: Ctx = Depends(need("seven.import"))):
    form = await request.form()
    c.check_csrf(str(form.get("csrf", "")))
    try:
        cover, look = int(str(form.get("cover_days", "")).strip()), int(str(form.get("lookback_days", "")).strip())
    except ValueError:
        return redirect("/seven-eleven?tab=order", err="Enter whole numbers of days")
    if not (1 <= cover <= 14 and 7 <= look <= 120):
        return redirect("/seven-eleven?tab=order", err="Cover days must be 1 to 14 and the look-back 7 to 120 days")
    srep.save_settings(c.db, cover, look)
    audit.log(c.db, actor_type="user", actor_id=c.user.id, action="seven_order_settings", entity_type="setting", entity_id="seven", after={"cover_days": cover, "lookback_days": look})
    c.db.commit()
    return redirect("/seven-eleven?tab=order", ok="Saved")
