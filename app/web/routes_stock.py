"""Inventory, sales, replenishment and purchase orders."""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import case, func, select
from sqlalchemy.orm import selectinload

from .. import audit
from .. import settings_service as cfg
from ..models import (Employee, InventoryCurrent, Location, OrderBatch, Payment, Product, PurchaseOrder, Recommendation, ReplRun, SalesDay, SalesDayProduct,
                      SalesLine, SalesTx, Supplier)
from ..services import dq, freshness, purchasing, receiving, replenishment, transfers
from ..timeutil import fmt_local, now_utc, today_local
from .deps import Ctx, need, redirect, render, parse_float

router = APIRouter()
PAGE = 50


def _pick_loc(c: Ctx, loc: int, prefer: str | None = None) -> Location | None:
    locs = c.locations()
    if loc:
        c.check_loc(loc)
        return next((l for l in locs if l.id == loc), None)
    if prefer:  # default to the first location of that type (stores sell, the warehouse does not)
        return next((l for l in locs if l.type == prefer), locs[0] if locs else None)
    return locs[0] if locs else None


# ======================================================================================= inventory
@router.get("/inventory")
def inventory(loc: int = 0, q: str = "", status: str = "", category: str = "", inc: str = "", page: int = 1, export: str = "", c: Ctx = Depends(need("inventory.view"))):
    db = c.db
    location = _pick_loc(c, loc)
    if location is None:
        return render(c, "inventory.html", rows=[], location=None, locations=[], q=q, status=status, category=category, inc=inc, page=1, total=0, cats=[])
    today = today_local()
    start = today - timedelta(days=int(cfg.get(db, "repl.lookback_days")))
    sold = {pid: u for pid, u in db.execute(select(SalesDayProduct.product_id, func.sum(SalesDayProduct.units)).where(
        SalesDayProduct.location_id == location.id, SalesDayProduct.business_date >= start, SalesDayProduct.business_date < today).group_by(SalesDayProduct.product_id))}
    days = db.scalar(select(func.count()).select_from(SalesDay).where(SalesDay.location_id == location.id, SalesDay.business_date >= start,
                                                                     SalesDay.business_date < today, SalesDay.tx_count > 0)) or 0
    open_pos = purchasing.open_po_detail(db)  # every PO not yet completed or received, overdue ones flagged
    stmt = (select(InventoryCurrent, Product).join(Product, Product.id == InventoryCurrent.product_id)
            .where(InventoryCurrent.location_id == location.id).options(selectinload(Product.supplier)))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Product.name.like(like) | Product.barcode.like(like) | Product.sku.like(like))
    if category:
        stmt = stmt.where(Product.category == category)
    if status == "negative":
        stmt = stmt.where(InventoryCurrent.on_hand < 0)
    elif status == "zero":
        stmt = stmt.where(InventoryCurrent.on_hand == 0)
    elif status == "positive":
        stmt = stmt.where(InventoryCurrent.on_hand > 0)
    stmt = stmt.order_by(InventoryCurrent.on_hand.asc() if status == "negative" else Product.name)
    pairs = db.execute(stmt).all()
    rows = []
    for inv, p in pairs:
        ads = (sold.get(p.id, 0) / days) if days else 0
        d = open_pos.get((location.id, p.id))
        rows.append({"p": p, "on_hand": inv.on_hand, "incoming": d["qty"] if d else 0, "late": d["late_qty"] if d else 0, "pos": d["pos"] if d else [], "ads": ads,
                     "cover": (max(inv.on_hand, 0) / ads) if ads > 0 else None, "synced": inv.synced_at})
    if inc == "yes":
        rows = [r for r in rows if r["incoming"] > 0]
    elif inc == "late":
        rows = [r for r in rows if r["late"] > 0]
    elif inc == "ontime":
        rows = [r for r in rows if r["incoming"] - r["late"] > 0]
    elif inc == "none":
        rows = [r for r in rows if r["incoming"] <= 0]
    if status == "low":
        rows = [r for r in rows if r["cover"] is not None and r["cover"] < 3 and r["on_hand"] >= 0]
        rows.sort(key=lambda r: r["cover"])
    if export == "csv":
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Location", "Product", "SKU", "Barcode", "Category", "Supplier", "On hand", "Incoming (open POs)", "Of which overdue", "Open PO numbers", "Avg daily sales", "Days of cover", "Last synced (UTC)"])
        for r in rows:
            w.writerow([location.name, r["p"].name, r["p"].sku or "", r["p"].barcode or "", r["p"].category or "", r["p"].supplier.name if r["p"].supplier else "",
                        r["on_hand"], r["incoming"], r["late"], " ".join(f'#{x["no"]}' for x in r["pos"]), round(r["ads"], 3), "" if r["cover"] is None else round(r["cover"], 1), r["synced"]])
        return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="inventory-{location.id}-{today}.csv"'})
    total = len(rows)
    stats = {"count": total, "value": sum(max(r["on_hand"], 0) * (r["p"].cost or 0) for r in rows),
             "negative": sum(1 for r in rows if r["on_hand"] < 0), "zero": sum(1 for r in rows if r["on_hand"] == 0),
             "low": sum(1 for r in rows if r["cover"] is not None and r["cover"] < 3 and r["on_hand"] >= 0)}
    page = max(page, 1)
    cats = [x for x in db.scalars(select(Product.category).where(Product.category.is_not(None)).distinct().order_by(Product.category))]
    fr = next(f for f in freshness.freshness(db) if f["domain"] == "inventory")
    return render(c, "inventory.html", rows=rows[(page - 1) * PAGE: page * PAGE], location=location, locations=c.locations(), q=q, status=status,
                  category=category, inc=inc, page=page, total=total, cats=cats, fr=fr, days=days, stats=stats)


# ======================================================================================= sales
@router.get("/sales/products")
def sales_by_product(loc: int = 0, days: str = "14", q: str = "", category: str = "", sort: str = "sales", page: int = 1, export: str = "",
                     frm: str = "", to: str = "", c: Ctx = Depends(need("sales.view"))):
    """Every product sold in the period, from the line items of StoreHub transactions. loc=0 combines all locations the user may see.
    Period is a preset (days=N) or a custom range (days=custom&frm=YYYY-MM-DD&to=YYYY-MM-DD)."""
    db = c.db
    end = today_local()
    custom = days == "custom"
    if custom:
        try:
            f_, t_ = date.fromisoformat(frm), date.fromisoformat(to)
            if f_ > t_ or (t_ - f_).days > 365 or t_ > end:
                raise ValueError
            start, n_days = f_, (t_ - f_).days + 1
        except ValueError:
            custom, n_days, frm, to = False, 14, "", ""
            start = end - timedelta(days=n_days - 1)
    else:
        n_days = min(max(int(days) if days.isdigit() else 14, 1), 365)
        start = end - timedelta(days=n_days - 1)
    allowed = [l.id for l in c.locations()]
    if loc:
        c.check_loc(loc)
        ids = [loc]
    else:
        ids = allowed
    sold = select(SalesDayProduct.product_id, func.sum(SalesDayProduct.units).label("units"), func.sum(SalesDayProduct.net_sales).label("net"),
                  func.count(func.distinct(SalesDayProduct.business_date)).label("days_sold")).where(
        SalesDayProduct.location_id.in_(ids or [0]), SalesDayProduct.business_date >= start, SalesDayProduct.business_date <= end).group_by(SalesDayProduct.product_id).subquery()
    stmt = select(Product, sold.c.units, sold.c.net, sold.c.days_sold).join(sold, sold.c.product_id == Product.id)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(Product.name.ilike(like) | Product.barcode.ilike(like) | Product.sku.ilike(like))
    if category:
        stmt = stmt.where(Product.category == category)
    order = {"units": sold.c.units.desc(), "name": Product.name.asc(), "price": (sold.c.net / sold.c.units).desc()}.get(sort, sold.c.net.desc())
    rows = list(db.execute(stmt.order_by(order, Product.name)))
    total_net = sum(r[2] or 0 for r in rows) or 0.0
    total_units = sum(r[1] or 0 for r in rows) or 0.0
    if export == "csv":
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Product", "Barcode", "Category", "Units sold", "Net sales", "Share of sales %", "Average price", "Days with sales"])
        for prod, units, net, ds in rows:
            w.writerow([prod.name, prod.barcode or "", prod.category or "", f"{units:g}", f"{net:.2f}", f"{(net / total_net * 100) if total_net else 0:.2f}",
                        f"{(net / units) if units else 0:.2f}", ds])
        audit.log(db, actor_type="user", actor_id=c.user.id, action="export_sales_by_product", entity_type="report", entity_id="sales_by_product",
                  after={"rows": len(rows), "days": n_days, "custom": f"{frm}..{to}" if custom else "", "location": loc})
        db.commit()
        fname = f"sales-by-product-{frm}-to-{to}.csv" if custom else f"sales-by-product-{n_days}d.csv"
        return Response("﻿" + out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{fname}"'})
    page = max(page, 1)
    shown = rows[(page - 1) * PAGE: page * PAGE]
    day_list = [start + timedelta(days=i) for i in range(n_days)] if n_days <= 60 else []
    series: dict[int, list[float]] = {}
    if shown and day_list:
        by = defaultdict(dict)
        for pid, d, u in db.execute(select(SalesDayProduct.product_id, SalesDayProduct.business_date, func.sum(SalesDayProduct.units)).where(
                SalesDayProduct.location_id.in_(ids or [0]), SalesDayProduct.business_date >= start, SalesDayProduct.business_date <= end,
                SalesDayProduct.product_id.in_([r[0].id for r in shown]))
                .group_by(SalesDayProduct.product_id, SalesDayProduct.business_date)):
            by[pid][d] = u
        series = {pid: [by[pid].get(d, 0.0) for d in day_list] for pid in by}
    cats = [x for x in db.scalars(select(Product.category).where(Product.category.is_not(None)).distinct().order_by(Product.category))]
    fr = next(f for f in freshness.freshness(db) if f["domain"] == "sales")
    return render(c, "sales_products.html", rows=shown, series=series, labels=[d.strftime("%a %d %b") for d in day_list], total=len(rows), total_net=total_net,
                  total_units=total_units, page=page, per=PAGE, offset=(page - 1) * PAGE, locations=c.locations(), loc=loc, days=n_days, q=q, category=category,
                  sort=sort, cats=cats, fr=fr, custom=custom, frm=frm, to=to)


@router.get("/sales/product/{pid}")
def sales_of_product(pid: int, days: str = "30", frm: str = "", to: str = "", c: Ctx = Depends(need("sales.view"))):
    """One product: its sales per day and per store, with the stock position next to it.
    Period is a preset (days=N) or a custom range (days=custom&frm=YYYY-MM-DD&to=YYYY-MM-DD)."""
    db = c.db
    p = db.get(Product, pid)
    if p is None:
        raise HTTPException(404)
    today = today_local()
    end = today
    custom = days == "custom"
    if custom:
        try:
            f_, t_ = date.fromisoformat(frm), date.fromisoformat(to)
            if f_ > t_ or (t_ - f_).days > 365 or t_ > today:
                raise ValueError
            start, end, days = f_, t_, (t_ - f_).days + 1
        except ValueError:
            custom, days, frm, to = False, 30, "", ""
            start = today - timedelta(days=days - 1)
    else:
        days = min(max(int(days) if days.isdigit() else 30, 7), 180)
        start = end - timedelta(days=days - 1)
    locs = c.locations()
    ids = [l.id for l in locs]
    rows = list(db.execute(select(SalesDayProduct.business_date, SalesDayProduct.location_id, SalesDayProduct.units, SalesDayProduct.net_sales).where(
        SalesDayProduct.product_id == pid, SalesDayProduct.location_id.in_(ids or [0]), SalesDayProduct.business_date >= start, SalesDayProduct.business_date <= end)))
    per_day: dict = defaultdict(lambda: [0.0, 0.0])
    per_loc: dict = defaultdict(lambda: [0.0, 0.0, set()])
    for d, lid, u, n in rows:
        per_day[d][0] += u
        per_day[d][1] += n
        per_loc[lid][0] += u
        per_loc[lid][1] += n
        per_loc[lid][2].add(d)
    day_list = [start + timedelta(days=i) for i in range(days)]
    series_units = [(d.strftime("%a %d %b"), per_day[d][0] if d in per_day else 0.0) for d in day_list]
    total_units = sum(v[0] for v in per_loc.values())
    total_net = sum(v[1] for v in per_loc.values())
    best = max(((d, v) for d, v in per_day.items()), key=lambda t: t[1][1], default=None)
    inv = {i.location_id: i for i in db.scalars(select(InventoryCurrent).where(InventoryCurrent.product_id == pid, InventoryCurrent.location_id.in_(ids or [0])))}
    open_pos = purchasing.open_po_detail(db)
    stores = []
    for l in locs:
        u, n, ds = per_loc.get(l.id, [0.0, 0.0, set()])
        oh = inv[l.id].on_hand if l.id in inv else None
        ads = u / days if days else 0
        stores.append({"loc": l, "units": u, "net": n, "days_sold": len(ds), "on_hand": oh, "incoming": (open_pos.get((l.id, pid)) or {}).get("qty", 0),
                       "cover": (max(oh, 0) / ads) if (oh is not None and ads > 0) else None})
    stores.sort(key=lambda s: -s["net"])
    fr = next(f for f in freshness.freshness(db) if f["domain"] == "sales")
    return render(c, "sales_product.html", p=p, days=days, total_units=total_units, total_net=total_net, best=best, stores=stores, fr=fr,
                  series_units=series_units, daily=sorted(per_day.items(), reverse=True), custom=custom, frm=frm, to=to)


@router.get("/sales")
def sales(loc: int = 0, days: str = "14", frm: str = "", to: str = "", export: str = "", c: Ctx = Depends(need("sales.view"))):
    db = c.db
    location = _pick_loc(c, loc, prefer="store")
    today = today_local()
    end = today
    custom = days == "custom"
    if custom:
        try:
            f_, t_ = date.fromisoformat(frm), date.fromisoformat(to)
            if f_ > t_ or (t_ - f_).days > 180 or t_ > today:
                raise ValueError
            start, n_days, end = f_, (t_ - f_).days + 1, t_
        except ValueError:
            custom, frm, to, n_days = False, "", "", 14
            start = today - timedelta(days=n_days - 1)
    else:
        n_days = min(max(int(days) if days.isdigit() else 14, 1), 120)
        start = today - timedelta(days=n_days - 1)
    rows = []
    top = []
    if location:
        rows = list(db.scalars(select(SalesDay).where(SalesDay.location_id == location.id, SalesDay.business_date >= start, SalesDay.business_date <= end).order_by(SalesDay.business_date.desc())))
        top = list(db.execute(select(Product.name, func.sum(SalesDayProduct.units), func.sum(SalesDayProduct.net_sales)).join(Product, Product.id == SalesDayProduct.product_id)
                              .where(SalesDayProduct.location_id == location.id, SalesDayProduct.business_date >= start, SalesDayProduct.business_date <= end)
                              .group_by(Product.name).order_by(func.sum(SalesDayProduct.net_sales).desc()).limit(15)))
    if export == "csv":
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Location", "Date", "Net sales", "Transactions", "Cash sales", "Other payments", "Cancelled"])
        for r in sorted(rows, key=lambda r: r.business_date):
            w.writerow([location.name if location else "", r.business_date, f"{r.net_sales:.2f}", r.tx_count, f"{r.cash_sales:.2f}",
                        f"{max(r.net_sales - r.cash_sales, 0):.2f}", r.cancelled_count])
        audit.log(db, actor_type="user", actor_id=c.user.id, action="export_sales_daily", entity_type="report", entity_id="sales_daily",
                  after={"rows": len(rows), "from": str(start), "to": str(end), "location": location.id if location else 0})
        db.commit()
        return Response("﻿" + out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="daily-sales-{start}-to-{end}.csv"'})
    mx = max([r.net_sales for r in rows] or [1]) or 1
    totals = {"net": sum(r.net_sales for r in rows), "tx": sum(r.tx_count for r in rows), "cash": sum(r.cash_sales for r in rows), "cancelled": sum(r.cancelled_count for r in rows)}
    totals["other"] = max(totals["net"] - totals["cash"], 0)
    fr = next(f for f in freshness.freshness(db) if f["domain"] == "sales")
    chart = [(r.business_date.strftime("%d %b"), r.net_sales) for r in reversed(rows)]
    compare = []
    if location and rows:  # the window of equal length right before this one, matched day by day
        prev = {r.business_date: r.net_sales for r in db.scalars(select(SalesDay).where(
            SalesDay.location_id == location.id, SalesDay.business_date >= start - timedelta(days=n_days), SalesDay.business_date < start))}
        compare = [prev.get(r.business_date - timedelta(days=n_days), 0.0) for r in reversed(rows)] if prev else []
    return render(c, "sales.html", chart=chart, compare=compare, rows=rows, top=top, location=location, locations=c.locations(), days=n_days, mx=mx, totals=totals, fr=fr,
                  custom=custom, frm=frm, to=to)


# ======================================================================================= transactions
def _period(days: str, frm: str, to: str, default: int = 14, cap: int = 180):
    """Period shared by the transaction pages: a preset (days=N) or a custom range (days=custom&frm&to).
    Returns (start, end, n_days, custom, frm, to); an invalid custom range falls back to the preset."""
    today = today_local()
    if days == "custom":
        try:
            f_, t_ = date.fromisoformat(frm), date.fromisoformat(to)
            if f_ > t_ or (t_ - f_).days > 365 or t_ > today:
                raise ValueError
            return f_, t_, (t_ - f_).days + 1, True, frm, to
        except ValueError:
            pass
    n = min(max(int(days) if days.isdigit() else default, 1), cap)
    return today - timedelta(days=n - 1), today, n, False, "", ""


def _emp_names(db, sh_ids) -> dict[str, str]:
    """StoreHub employee id -> display name for every id seen; unknown ids stay readable."""
    known = {e.storehub_id: e.name for e in db.scalars(select(Employee).where(Employee.storehub_id.in_(sh_ids or {""})))}
    return {s: known.get(s, f"#{s[-6:]}") for s in sh_ids if s}


def _short_loc(name: str) -> str:
    """'The Daily Centro - Bantayan' -> 'Bantayan' for tight table cells."""
    return name.replace("The Daily Centro - ", "").replace("TheDailyCentro ", "")


@router.get("/sales/transactions")
def sales_transactions(loc: int = 0, days: str = "14", frm: str = "", to: str = "", emp: str = "", q: str = "", page: int = 1,
                       export: str = "", c: Ctx = Depends(need("sales.detail"))):
    """Every transaction in the period; a row opens the full receipt. loc=0 combines all locations the user may see."""
    db = c.db
    start, end, n_days, custom, frm, to = _period(days, frm, to)
    ids = [l.id for l in c.locations()]
    if loc:
        c.check_loc(loc)
        ids = [loc]
    stmt = select(SalesTx).where(SalesTx.location_id.in_(ids or [0]), SalesTx.business_date >= start, SalesTx.business_date <= end)
    if emp:
        stmt = stmt.where(SalesTx.employee_sh_id == emp)
    if q.strip():
        stmt = stmt.where(SalesTx.invoice_no.ilike(f"%{q.strip()}%"))
    stmt = stmt.order_by(SalesTx.tx_time.desc())
    loc_names = {l.id: _short_loc(l.name) for l in c.locations()}
    if export == "csv":
        txs_all = list(db.scalars(stmt))
        names = _emp_names(db, {t.employee_sh_id for t in txs_all if t.employee_sh_id})
        pays = defaultdict(list)
        for tid, m, a in db.execute(select(Payment.tx_id, Payment.method, Payment.amount)
                                    .where(Payment.tx_id.in_([t.id for t in txs_all] or [0])).order_by(Payment.id)):
            pays[tid].append(f"{m} {a:.2f}")
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Time", "Invoice", "Store", "Employee", "Type", "Status", "Payments", "Subtotal", "Discount", "Tax", "Total"])
        for t in txs_all:
            w.writerow([fmt_local(t.tx_time), t.invoice_no or "", loc_names.get(t.location_id, ""), names.get(t.employee_sh_id or "", ""),
                        t.tx_type, "Cancelled" if t.cancelled else "", "; ".join(pays.get(t.id, [])), f"{t.subtotal:.2f}", f"{t.discount:.2f}", f"{t.tax:.2f}", f"{t.total:.2f}"])
        audit.log(db, actor_type="user", actor_id=c.user.id, action="export_transactions", entity_type="report", entity_id="transactions",
                  after={"rows": len(txs_all), "days": n_days, "custom": f"{frm}..{to}" if custom else "", "location": loc, "employee": emp})
        db.commit()
        fname = f"transactions-{frm}-to-{to}.csv" if custom else f"transactions-{n_days}d.csv"
        return Response("﻿" + out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{fname}"'})
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = max(page, 1)
    txs = list(db.scalars(stmt.limit(PAGE).offset((page - 1) * PAGE)))
    txids = [t.id for t in txs]
    names = _emp_names(db, {t.employee_sh_id for t in txs if t.employee_sh_id})
    pays = defaultdict(list)
    for tid, m, a in db.execute(select(Payment.tx_id, Payment.method, Payment.amount).where(Payment.tx_id.in_(txids or [0])).order_by(Payment.id)):
        pays[tid].append((m, a))
    units = dict(db.execute(select(SalesLine.tx_id, func.sum(SalesLine.qty)).where(SalesLine.tx_id.in_(txids or [0])).group_by(SalesLine.tx_id)).all())
    emp_ids = [r[0] for r in db.execute(select(SalesTx.employee_sh_id).where(SalesTx.location_id.in_(ids or [0]), SalesTx.business_date >= start,
                                                                             SalesTx.business_date <= end, SalesTx.employee_sh_id.is_not(None)).distinct())]
    employees = sorted(_emp_names(db, emp_ids).items(), key=lambda kv: kv[1])
    fr = next(f for f in freshness.freshness(db) if f["domain"] == "sales")
    return render(c, "sales_tx.html", txs=txs, names=names, pays=pays, units=units, loc_names=loc_names, employees=employees, emp=emp, q=q,
                  total=total, page=page, per=PAGE, offset=(page - 1) * PAGE, locations=c.locations(), loc=loc, days=n_days, custom=custom, frm=frm, to=to, fr=fr)


@router.get("/sales/tx/{txid}")
def sales_tx_detail(txid: int, c: Ctx = Depends(need("sales.detail"))):
    """One receipt: the items sold, payments and totals, exactly as synced from StoreHub."""
    db = c.db
    t = db.get(SalesTx, txid)
    if t is None:
        raise HTTPException(404)
    c.check_loc(t.location_id)
    lines = list(db.execute(select(SalesLine, Product.name, Product.barcode)
                            .outerjoin(Product, Product.id == SalesLine.product_id)
                            .where(SalesLine.tx_id == txid).order_by(SalesLine.line_no)))
    pays = list(db.execute(select(Payment.method, Payment.amount).where(Payment.tx_id == txid).order_by(Payment.id)))
    emp = db.scalar(select(Employee).where(Employee.storehub_id == t.employee_sh_id)) if t.employee_sh_id else None
    return render(c, "sales_tx_detail.html", tx=t, lines=lines, pays=pays, emp=emp, loc=db.get(Location, t.location_id),
                  has_disc=any(l[0].discount for l in lines))


@router.get("/sales/employees")
def sales_by_employee(loc: int = 0, days: str = "14", frm: str = "", to: str = "", export: str = "", c: Ctx = Depends(need("sales.detail"))):
    """Sales per employee in the period: transactions, items, net sales and average basket. Cancelled sales are excluded from the figures."""
    db = c.db
    start, end, n_days, custom, frm, to = _period(days, frm, to)
    ids = [l.id for l in c.locations()]
    if loc:
        c.check_loc(loc)
        ids = [loc]
    scope = [SalesTx.location_id.in_(ids or [0]), SalesTx.business_date >= start, SalesTx.business_date <= end]
    rows = list(db.execute(select(SalesTx.employee_sh_id,
                                  func.count().label("txs"),
                                  func.sum(case((SalesTx.cancelled, 0.0), else_=SalesTx.total)).label("net"),
                                  func.sum(case((SalesTx.cancelled, 1), else_=0)).label("cancelled"),
                                  func.max(SalesTx.tx_time).label("last"))
                           .where(*scope).group_by(SalesTx.employee_sh_id)))
    units = dict(db.execute(select(SalesTx.employee_sh_id, func.sum(SalesLine.qty))
                            .join(SalesLine, SalesLine.tx_id == SalesTx.id)
                            .where(*scope, SalesTx.cancelled == False).group_by(SalesTx.employee_sh_id)).all())  # noqa: E712
    names = _emp_names(db, {r[0] for r in rows if r[0]})
    locs_of = defaultdict(set)
    for eid, lid in db.execute(select(SalesTx.employee_sh_id, SalesTx.location_id).where(*scope).distinct()):
        locs_of[eid].add(lid)
    loc_names = {l.id: _short_loc(l.name) for l in c.locations()}
    stats = [{"sh_id": r[0], "name": names.get(r[0], "—") if r[0] else "—", "txs": r[1], "net": r[2] or 0.0, "cancelled": r[3] or 0,
              "units": units.get(r[0], 0.0) or 0.0, "last": r[4],
              "stores": sorted(loc_names[i] for i in locs_of.get(r[0], set()) if i in loc_names)} for r in rows]
    for s in stats:
        s["avg"] = s["net"] / (s["txs"] - s["cancelled"]) if s["txs"] - s["cancelled"] > 0 else 0.0
    stats.sort(key=lambda s: -s["net"])
    totals = {"net": sum(s["net"] for s in stats), "txs": sum(s["txs"] for s in stats), "units": sum(s["units"] for s in stats)}
    if export == "csv":
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Employee", "Stores", "Transactions", "Cancelled", "Items sold", "Net sales", "Average basket", "Last sale"])
        for s in stats:
            w.writerow([s["name"], ", ".join(s["stores"]), s["txs"], s["cancelled"], f"{s['units']:g}", f"{s['net']:.2f}", f"{s['avg']:.2f}",
                        fmt_local(s["last"]) if s["last"] else ""])
        audit.log(db, actor_type="user", actor_id=c.user.id, action="export_sales_by_employee", entity_type="report", entity_id="sales_by_employee",
                  after={"rows": len(stats), "from": str(start), "to": str(end), "location": loc})
        db.commit()
        return Response("﻿" + out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="sales-by-employee-{start}-to-{end}.csv"'})
    fr = next(f for f in freshness.freshness(db) if f["domain"] == "sales")
    return render(c, "sales_employees.html", stats=stats, totals=totals, locations=c.locations(), loc=loc, days=n_days, custom=custom, frm=frm, to=to, fr=fr)


# ======================================================================================= replenishment
@router.get("/replenishment")
def replenishment_page(loc: int = 0, status: str = "pending", supplier: int = 0, page: int = 1, search: str = "", c: Ctx = Depends(need("repl.view"))):
    db = c.db
    ids = c.location_ids
    q = (select(Recommendation).where(Recommendation.location_id.in_(ids or [0]), Recommendation.business_date == today_local())
         .options(selectinload(Recommendation.product), selectinload(Recommendation.location), selectinload(Recommendation.supplier)))
    if loc:
        c.check_loc(loc)
        q = q.where(Recommendation.location_id == loc)
    if status and status != "all":
        q = q.where(Recommendation.status.in_({"decided": ("approved", "modified", "rejected", "ordered")}.get(status, (status,))))
    if supplier:
        q = q.where(Recommendation.supplier_id == supplier)
    term = search.strip()
    if term:
        like = f"%{term}%"
        q = q.where(Recommendation.product_id.in_(select(Product.id).where(Product.name.ilike(like) | Product.barcode.ilike(like) | Product.sku.ilike(like))))
    q = q.order_by(Recommendation.location_id, Recommendation.supplier_id, Recommendation.value.desc())
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    page = max(page, 1)
    recs = list(db.scalars(q.limit(100).offset((page - 1) * 100)))
    last = db.scalar(select(ReplRun).order_by(ReplRun.id.desc()).limit(1))
    counts = dict(db.execute(select(Recommendation.status, func.count()).where(Recommendation.business_date == today_local(), Recommendation.location_id.in_(ids or [0]))
                             .group_by(Recommendation.status)).all())
    sups = list(db.scalars(select(Supplier).order_by(Supplier.name)))
    return render(c, "replenishment.html", recs=recs, last=last, counts=counts, status=status, loc=loc, supplier=supplier, suppliers=sups, search=term,
                  locations=c.locations(), page=page, total=total, reasons=cfg.get(db, "repl.reason_codes"), metrics=replenishment.metrics(db),
                  sales_only=cfg.get(db, "repl.mode") != "stock_and_sales", cover_days=int(cfg.get(db, "repl.sales_only_cover_days")),
                  limit=float(cfg.get(db, "repl.approval_limit_manager")), conf=replenishment.confidence_label,
                  reason_labels=replenishment.REASON_LABELS, fixes=dq.FIXES)


@router.post("/replenishment/run")
def replenishment_run(csrf: str = Form(""), c: Ctx = Depends(need("repl.run"))):
    c.check_csrf(csrf)
    run = replenishment.run(c.db, actor=c.user)
    if run.status == "blocked":
        return redirect("/replenishment", err=run.note)
    return redirect("/replenishment", ok=f"Suggestions updated: {run.counts['created']} new, {run.counts['updated']} refreshed")


@router.post("/replenishment/{rec_id}/decide")
def replenishment_decide(rec_id: int, action: str = Form(...), qty: str = Form(""), reason: str = Form(""), note: str = Form(""), csrf: str = Form(""),
                         back: str = Form("/replenishment"), c: Ctx = Depends(need("repl.decide"))):
    c.check_csrf(csrf)
    rec = c.db.get(Recommendation, rec_id)
    if rec is None:
        raise HTTPException(404)
    c.check_loc(rec.location_id)
    try:
        replenishment.decide(c.db, rec, c.user, action, parse_float(qty), reason or None, note or None)
    except replenishment.DecisionError as e:
        c.db.rollback()
        return redirect(back, err=str(e))
    return redirect(back, ok=f"{rec.product.name}: {rec.status}")


@router.post("/replenishment/bulk")
def replenishment_bulk(ids: list[int] = Form(default=[]), csrf: str = Form(""), back: str = Form("/replenishment"), c: Ctx = Depends(need("repl.decide"))):
    c.check_csrf(csrf)
    n = fail = 0
    for rid in ids:
        rec = c.db.get(Recommendation, rid)
        if rec is None or rec.location_id not in c.location_ids or rec.status != "pending":
            continue
        try:
            with c.db.begin_nested():
                replenishment.decide(c.db, rec, c.user, "approve")
            n += 1
        except replenishment.DecisionError:
            fail += 1
    return redirect(back, ok=f"Approved {n} suggestion(s)" + (f", {fail} could not be approved (above your limit)" if fail else ""))


@router.post("/replenishment/batches/create")
def batches_create(csrf: str = Form(""), c: Ctx = Depends(need("repl.decide"))):
    c.check_csrf(csrf)
    made = replenishment.create_batches(c.db, c.user, c.location_ids)
    return redirect("/replenishment/batches", ok=f"{len(made)} order batch(es) created" if made else "Nothing approved is waiting to be batched")


@router.get("/replenishment/batches")
def batches(c: Ctx = Depends(need("repl.view"))):
    db = c.db
    q = select(OrderBatch).where(OrderBatch.location_id.in_(c.location_ids or [0]))
    if not c.can("transfer.view"):  # warehouse-to-store transfer batches are for the warehouse team and admins only
        q = q.where(OrderBatch.source_kind != "transfer")
    q = (q.order_by(OrderBatch.id.desc()).limit(60)
         .options(selectinload(OrderBatch.supplier), selectinload(OrderBatch.location), selectinload(OrderBatch.lines).selectinload(Recommendation.product)))
    return render(c, "batches.html", batches=list(db.scalars(q)))


@router.get("/replenishment/batches/{bid}.csv")
def batch_csv(bid: int, c: Ctx = Depends(need("repl.view"))):
    b = c.db.scalar(select(OrderBatch).where(OrderBatch.id == bid).options(selectinload(OrderBatch.lines).selectinload(Recommendation.product),
                                                                         selectinload(OrderBatch.supplier), selectinload(OrderBatch.location)))
    if b is None or (b.source_kind == "transfer" and not c.can("transfer.view")):
        raise HTTPException(404)
    c.check_loc(b.location_id)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Batch", "Destination", "Supplier", "Product", "SKU", "Barcode", "Quantity", "Unit cost", "Line value"])
    for r in b.lines:
        w.writerow([b.id, b.location.name, b.supplier.name if b.supplier else "Warehouse transfer", r.product.name, r.product.sku or "", r.product.barcode or "",
                    f"{r.approved_qty:g}", r.unit_cost or "", round((r.approved_qty or 0) * (r.unit_cost or 0), 2)])
    return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="order-batch-{bid}.csv"'})


@router.post("/replenishment/batches/{bid}/entered")
def batch_entered(bid: int, po_number: str = Form(""), csrf: str = Form(""), c: Ctx = Depends(need("repl.decide"))):
    c.check_csrf(csrf)
    b = c.db.get(OrderBatch, bid)
    if b is None:
        raise HTTPException(404)
    c.check_loc(b.location_id)
    try:
        n = int(po_number) if po_number.strip() else None
        replenishment.mark_entered(c.db, b, c.user, n)
    except (ValueError, replenishment.DecisionError) as e:
        c.db.rollback()
        return redirect("/replenishment/batches", err=str(e) if not isinstance(e, ValueError) else "PO number must be a number")
    return redirect("/replenishment/batches", ok="Marked as entered in StoreHub")


# ======================================================================================= purchase orders
@router.get("/purchase-orders")
def purchase_orders(view: str = "open", loc: int = 0, page: int = 1, c: Ctx = Depends(need("po.view"))):
    db = c.db
    stale_days = int(cfg.get(db, "po.stale_days"))
    q = select(PurchaseOrder).options(selectinload(PurchaseOrder.supplier), selectinload(PurchaseOrder.location), selectinload(PurchaseOrder.lines)) \
        .order_by(PurchaseOrder.sh_po_id.desc())
    if loc:
        c.check_loc(loc)
        q = q.where(PurchaseOrder.location_id == loc)
    pos = list(db.scalars(q))
    now = now_utc()
    rows = []
    for po in pos:
        kind = purchasing.classify_po(po, stale_days, now)
        if view == "open" and kind not in ("incoming", "stale"):
            continue
        if view == "closed" and kind not in ("closed", "deleted"):
            continue
        rows.append((po, kind, sum(max(0, l.ordered_qty - l.received_qty) for l in po.lines)))
    total = len(rows)
    page = max(page, 1)
    return render(c, "purchase_orders.html", rows=rows[(page - 1) * PAGE: page * PAGE], view=view, loc=loc, locations=c.locations(), page=page, total=total,
                  stale_days=stale_days)


@router.get("/purchase-orders/{po_id}")
def po_detail(po_id: int, c: Ctx = Depends(need("po.view"))):
    po = c.db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == po_id).options(selectinload(PurchaseOrder.lines).selectinload(PoLine.product),
                                                                                   selectinload(PurchaseOrder.supplier), selectinload(PurchaseOrder.location)))
    if po is None:
        raise HTTPException(404)
    c.check_loc(po.location_id)
    kind = purchasing.classify_po(po, int(cfg.get(c.db, "po.stale_days")))
    from ..services import order_documents
    return render(c, "po_detail.html", po=po, kind=kind, document=order_documents.document(c.db, po, "po"),
                  document_base=f"/purchase-orders/{po.id}", exact=order_documents.display)


from ..models import PoLine  # noqa: E402  (used in po_detail)


@router.post("/purchase-orders/{po_id}/receive")
def po_receive(po_id: int, csrf: str = Form(""), c: Ctx = Depends(need("receiving.count"))):
    c.check_csrf(csrf)
    po = c.db.get(PurchaseOrder, po_id)
    if po is None:
        raise HTTPException(404)
    c.check_loc(po.location_id)
    try:
        rec = receiving.start(c.db, c.user, po)
    except receiving.ReceivingError as e:
        return redirect(f"/purchase-orders/{po_id}", err=str(e))
    return redirect(f"/receiving/{rec.id}")


# ======================================================================================= warehouse transfers: dispatch and store receipt
@router.post("/replenishment/batches/{bid}/dispatch")
def batch_dispatch(bid: int, csrf: str = Form(""), c: Ctx = Depends(need("transfer.dispatch"))):
    c.check_csrf(csrf)
    b = c.db.scalar(select(OrderBatch).where(OrderBatch.id == bid).options(selectinload(OrderBatch.location), selectinload(OrderBatch.lines)))
    if b is None:
        raise HTTPException(404)
    c.check_loc(b.location_id)
    try:
        transfers.dispatch(c.db, c.user, b)
    except transfers.TransferError as e:
        return redirect("/replenishment/batches", err=str(e))
    return redirect("/replenishment/batches", ok=f"Transfer #{bid} marked as dispatched")


@router.post("/replenishment/batches/{bid}/receive")
async def batch_receive(bid: int, request: Request, c: Ctx = Depends(need("transfer.receive"))):
    form = await request.form()
    c.check_csrf(str(form.get("csrf", "")))
    b = c.db.scalar(select(OrderBatch).where(OrderBatch.id == bid).options(selectinload(OrderBatch.location), selectinload(OrderBatch.lines).selectinload(Recommendation.product)))
    if b is None:
        raise HTTPException(404)
    c.check_loc(b.location_id)
    counts = {l.id: parse_float(form.get(f"q_{l.id}")) for l in b.lines}
    try:
        transfers.receive(c.db, c.user, b, counts)
    except transfers.TransferError as e:
        c.db.rollback()
        return redirect("/replenishment/batches", err=str(e))
    return redirect("/replenishment/batches", ok="Transfer received" + (". Differences were found and an issue was opened." if any(l.received_qty != l.approved_qty for l in b.lines) else "."))
