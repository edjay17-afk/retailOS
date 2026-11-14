"""Inventory, sales, replenishment and purchase orders."""
from __future__ import annotations

import csv
import io
from urllib.parse import parse_qsl, urlencode
from collections import defaultdict
from datetime import date, timedelta, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from sqlalchemy import case, func, select
from sqlalchemy.orm import selectinload

from .. import audit
from .. import settings_service as cfg
from ..models import (Employee, InventoryCurrent, Location, OrderBatch, Payment, Product, PurchaseOrder, Recommendation, ReplRun, SalesDay, SalesDayProduct,
                      SalesLine, SalesTx, Supplier)
from ..services import dq, freshness, po_drafts, purchasing, receiving, replenishment, replenishment_sheets, stock_estimate, stock_moves, transfer_sheets, transfers
from ..timeutil import fmt_local, now_utc, today_local, tz, to_local
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
def inventory(request: Request, loc: int = 0, q: str = "", status: str = "", category: str = "", inc: str = "", page: int = 1, export: str = "", c: Ctx = Depends(need("inventory.view"))):
    db = c.db
    if not request.url.query and len(c.locations()) > 1:   # the plain Stock page compares the stores; any filter or ?view=list keeps the single-store list
        return redirect("/inventory/compare")
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
            .where(InventoryCurrent.location_id == location.id, Product.in_api.is_(True)).options(selectinload(Product.supplier)))   # only what StoreHub lists now
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
    est_adj = stock_estimate.adjustments(db) if location.source == "storehub" else {}
    rows = []
    for inv, p in pairs:
        ads = (sold.get(p.id, 0) / days) if days else 0
        d = open_pos.get((location.id, p.id))
        a = est_adj.get((location.id, p.id)) if location.source == "storehub" else None
        rows.append({"est": stock_estimate.estimate(inv.on_hand, a), "est_note": stock_estimate.note(a), "p": p, "on_hand": inv.on_hand, "incoming": d["qty"] if d else 0, "late": d["late_qty"] if d else 0, "pos": d["pos"] if d else [], "ads": ads,
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
                        r["on_hand"], r["incoming"] if location.source == "storehub" else "Unavailable", r["late"] if location.source == "storehub" else "Unavailable", " ".join(f'#{x["no"]}' for x in r["pos"]), round(r["ads"], 3), "" if r["cover"] is None else round(r["cover"], 1), r["synced"]])
        return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="inventory-{location.id}-{today}.csv"'})
    total = len(rows)
    stats = {"count": total, "value": sum(max(r["on_hand"], 0) * (r["p"].cost or 0) for r in rows),
             "negative": sum(1 for r in rows if r["on_hand"] < 0), "zero": sum(1 for r in rows if r["on_hand"] == 0),
             "low": sum(1 for r in rows if r["cover"] is not None and r["cover"] < 3 and r["on_hand"] >= 0)}
    page = max(page, 1)
    cats = [x for x in db.scalars(select(Product.category).where(Product.category.is_not(None), Product.in_api.is_(True)).distinct().order_by(Product.category))]
    fr = next(f for f in freshness.freshness(db) if f["domain"] == "inventory")
    return render(c, "inventory.html", rows=rows[(page - 1) * PAGE: page * PAGE], location=location, locations=c.locations(), q=q, status=status,
                  category=category, inc=inc, page=page, total=total, cats=cats, fr=fr, days=days, stats=stats)


LEVEL_ORDER = {"neg": 0, "out": 1, "low": 2}


def _compare_rows(db, locs, q: str, category: str):
    """One row per product with its stock in every location the user may see, and how worrying each number is.
    out / negative: nothing on the shelf of a product that sold in the last 30 days (negative is always shown); low: under 3 days of stock at the recent sales rate."""
    ids = [l.id for l in locs] or [0]
    today = today_local()
    start = today - timedelta(days=int(cfg.get(db, "repl.lookback_days")))
    trading = {lid: n for lid, n in db.execute(select(SalesDay.location_id, func.count()).where(
        SalesDay.location_id.in_(ids), SalesDay.business_date >= start, SalesDay.business_date < today, SalesDay.tx_count > 0).group_by(SalesDay.location_id))}
    sold = {(lid, pid): u or 0.0 for lid, pid, u in db.execute(select(SalesDayProduct.location_id, SalesDayProduct.product_id, func.sum(SalesDayProduct.units)).where(
        SalesDayProduct.location_id.in_(ids), SalesDayProduct.business_date >= start, SalesDayProduct.business_date < today).group_by(SalesDayProduct.location_id, SalesDayProduct.product_id))}
    recent = set(db.execute(select(SalesDayProduct.location_id, SalesDayProduct.product_id).where(
        SalesDayProduct.location_id.in_(ids), SalesDayProduct.units > 0, SalesDayProduct.business_date > today - timedelta(days=30)).distinct()).all())
    sold_anywhere = {pid for _, pid in recent}
    open_pos = purchasing.open_po_detail(db)
    stmt = select(InventoryCurrent, Product).join(Product, Product.id == InventoryCurrent.product_id).where(InventoryCurrent.location_id.in_(ids), Product.in_api.is_(True))   # only what StoreHub lists now
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(Product.name.ilike(like) | Product.barcode.ilike(like) | Product.sku.ilike(like))
    if category:
        stmt = stmt.where(Product.category == category)
    by_id = {l.id: l for l in locs}
    rows: dict[int, dict] = {}
    for inv, p in db.execute(stmt):
        loc = by_id[inv.location_id]
        ads = (sold.get((loc.id, p.id), 0.0) / trading[loc.id]) if trading.get(loc.id) else 0.0
        cover = (max(inv.on_hand, 0) / ads) if ads > 0 else None
        seen = ((loc.id, p.id) in recent) if loc.type == "store" else (p.id in sold_anywhere)
        if inv.on_hand < 0:
            level = "neg"
        elif inv.on_hand == 0:
            level = "out" if seen else "zero"
        elif loc.type == "store" and cover is not None and cover < 3:
            level = "low"
        else:
            level = "ok"
        inc = open_pos.get((loc.id, p.id)) if loc.source == "storehub" else None
        row = rows.setdefault(p.id, {"p": p, "cells": {}, "total": 0.0, "alerts": 0})
        row["cells"][loc.id] = {"qty": inv.on_hand, "level": level, "ads": ads, "cover": cover, "incoming": inc["qty"] if inc else 0}
        row["total"] += inv.on_hand
        row["alerts"] += 1 if level in LEVEL_ORDER else 0
    # every product StoreHub lists is on the page, also one that has no stock record in these locations yet
    sources = {(l.source, l.source_account) for l in locs}
    extra = select(Product).where(Product.in_api.is_(True), Product.id.not_in(list(rows) or [0]))
    if q.strip():
        like = f"%{q.strip()}%"
        extra = extra.where(Product.name.ilike(like) | Product.barcode.ilike(like) | Product.sku.ilike(like))
    if category:
        extra = extra.where(Product.category == category)
    for p in db.scalars(extra):
        if (p.source, p.source_account) in sources:
            rows[p.id] = {"p": p, "cells": {}, "total": 0.0, "alerts": 0}
    return list(rows.values())


MOVE_ROWS = 100


@router.post("/inventory/compare/transfer")
async def inventory_move_to_sheet(request: Request, c: Ctx = Depends(need("transfer.sheet"))):
    """The ticked products, with the quantities shown, become lines of the transfer sheet for the store (an open draft for that store is reused)."""
    form = await request.form()
    c.check_csrf(str(form.get("csrf", "")))
    try:
        store_id = int(form.get("store_id", 0))
        pids = list(dict.fromkeys(int(x) for x in form.getlist("ids")))
    except ValueError:
        raise HTTPException(400, "Bad selection")
    store = c.db.get(Location, store_id)
    back = f"/inventory/compare?view=move&store={store_id}"
    if store is None or store.id not in c.location_ids or store.closed:
        raise HTTPException(404)
    if not pids or len(pids) > 100:
        return redirect(back, err=c.t("Select between 1 and 100 products"))
    try:
        sheet = transfer_sheets.create(c.db, c.user, store.id)
        for pid in pids:
            p = c.db.get(Product, pid)
            if p is None:
                raise transfer_sheets.SheetError("Product not found")
            transfer_sheets.add_line(c.db, sheet, p, form.get(f"q_{pid}", ""))
        c.db.commit()
    except transfer_sheets.SheetError as e:
        c.db.rollback()
        return redirect(back, err=c.t(str(e)))
    return redirect(f"/transfer-sheets/{sheet.id}", ok=f"{c.t('Transfer sheet')} TS-{sheet.id:05d}: {len(pids)} {c.t('products')}")


@router.get("/inventory/compare")
def inventory_compare(q: str = "", category: str = "", attention: str = "", sort: str = "attention", page: int = 1, export: str = "", view: str = "all", store: int = 0, status: str = "", est: str = "", loc: int = 0,
                      c: Ctx = Depends(need("inventory.view"))):
    """Stock compare. The default is every product in every store and the warehouse side by side; view=move lists what the warehouse can send to a store."""
    db = c.db
    if view == "move" and not export:
        wh = transfer_sheets.warehouse(db)
        shops = stock_moves.stores(c.locations())
        counts = {l.id: len(stock_moves.candidates(db, wh.id, l.id)) for l in shops} if wh else {}
        store = store if store in counts else (max(counts, key=lambda k: counts[k]) if counts else 0)
        rows = stock_moves.candidates(db, wh.id, store, q=q) if wh and store else []
        cur = next((l for l in shops if l.id == store), None)
        return render(c, "stock_move.html", shops=shops, counts=counts, store=cur, rows=rows[:MOVE_ROWS], total=len(rows), q=q, wh=wh, cover=stock_moves.cover_days(db),
                      short=stock_moves.short_count(db, store) if store else 0, can_sheet=c.can("transfer.sheet"), fr=next(f for f in freshness.freshness(db) if f["domain"] == "inventory"),
                      cut=lambda n: n.replace("The Daily Centro - ", ""), capped=len(rows) > MOVE_ROWS)
    locs = sorted(c.locations(), key=lambda l: (l.type != "warehouse", l.name))
    rows = _compare_rows(db, locs, q, category)
    if loc:   # one store: only its products, and the negative / zero / low filters look at that store alone
        c.check_loc(loc)
        rows = [r for r in rows if loc in r["cells"]]

    def seen(r):   # the cells the status filters look at
        return [x for lid, x in r["cells"].items() if not loc or lid == loc]
    if est:   # estimated stock: StoreHub's stock plus purchase orders and transfers that are still open, as if all of them were completed
        adj = stock_estimate.adjustments(db)
        by_loc = {l.id: l for l in locs}
        for r in rows:
            r["total"] = 0.0
            for lid, x in r["cells"].items():
                a = adj.get((lid, r["p"].id))
                x["actual"], x["adj"] = x["qty"], stock_estimate.note(a)
                x["qty"] = stock_estimate.estimate(x["qty"], a)
                if x["qty"] < 0:
                    x["level"] = "neg"
                elif x["qty"] == 0:
                    x["level"] = x["level"] if x["level"] in ("out", "zero") else "out"
                elif by_loc[lid].type == "store" and x["ads"] > 0 and x["qty"] / x["ads"] < 3:
                    x["level"] = "low"
                else:
                    x["level"] = "ok"
                r["total"] += x["qty"]
            r["alerts"] = sum(1 for x in r["cells"].values() if x["level"] in LEVEL_ORDER)
    if attention:
        rows = [r for r in rows if r["alerts"]]
    rows.sort(key=(lambda r: (-r["alerts"], r["p"].name.lower())) if sort != "name" else (lambda r: r["p"].name.lower()))
    summary = {"products": len(rows), "out": sum(1 for r in rows for x in r["cells"].values() if x["level"] in ("out", "neg")),
               "low": sum(1 for r in rows for x in r["cells"].values() if x["level"] == "low")}
    # the store cards, the status chips and the "next step" of each row
    wh = transfer_sheets.warehouse(db)
    open_stores = stock_moves.stores(locs)
    send_by_store = {l.id: {x["p"].id for x in stock_moves.candidates(db, wh.id, l.id)} for l in open_stores} if wh else {}
    send_ids = set().union(*send_by_store.values()) if send_by_store else set()
    health = {}
    for lid, neg, zero, pos in db.execute(select(InventoryCurrent.location_id, func.sum(case((InventoryCurrent.on_hand < 0, 1), else_=0)), func.sum(case((InventoryCurrent.on_hand == 0, 1), else_=0)),
                                                  func.sum(case((InventoryCurrent.on_hand > 0, 1), else_=0))).join(Product, Product.id == InventoryCurrent.product_id)
                                         .where(InventoryCurrent.location_id.in_([l.id for l in locs] or [0]), Product.in_api.is_(True)).group_by(InventoryCurrent.location_id)):
        health[lid] = {"neg": int(neg or 0), "zero": int(zero or 0), "ok": int(pos or 0)}
    if est:   # the store cards follow the estimate too
        health = {l.id: {"neg": 0, "zero": 0, "ok": 0} for l in locs}
        for r in rows:
            for lid, x in r["cells"].items():
                health[lid]["neg" if x["qty"] < 0 else "zero" if x["qty"] == 0 else "ok"] += 1
    for r in rows:
        levels = [x["level"] for x in r["cells"].values()]
        if not levels:
            r["next"] = "none"
            continue
        store_neg = sum(1 for lid, x in r["cells"].items() if x["level"] == "neg" and lid in send_by_store)
        if r["p"].id in send_ids:
            r["next"] = "send"
        elif levels.count("neg") >= 2:
            r["next"] = "count"
        elif any(v in ("out", "zero", "low") for v in levels) or levels.count("neg"):
            r["next"] = "reorder"
        else:
            r["next"] = "ok"
    send_here = send_by_store.get(loc, set()) if loc else send_ids
    chips = {"all": len(rows), "neg": sum(1 for r in rows if any(x["level"] == "neg" for x in seen(r))),
             "zero": sum(1 for r in rows if any(x["level"] in ("out", "zero") for x in seen(r))),
             "low": sum(1 for r in rows if any(x["level"] == "low" for x in seen(r))), "send": sum(1 for r in rows if r["p"].id in send_here)}
    if status == "neg":
        rows = [r for r in rows if any(x["level"] == "neg" for x in seen(r))]
    elif status == "zero":
        rows = [r for r in rows if any(x["level"] in ("out", "zero") for x in seen(r))]
    elif status == "low":
        rows = [r for r in rows if any(x["level"] == "low" for x in seen(r))]
    elif status == "send":
        rows = [r for r in rows if r["p"].id in send_here]
    if export == "csv":
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Product", "SKU", "Barcode", "Category"] + [l.name for l in locs] + ["Total"])
        for r in rows:
            w.writerow([r["p"].name, r["p"].sku or "", r["p"].barcode or "", r["p"].category or ""] + [r["cells"][l.id]["qty"] if l.id in r["cells"] else "" for l in locs] + [r["total"]])
        return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="stock-by-store-{today_local()}.csv"'})
    page = max(page, 1)
    cats = [x for x in db.scalars(select(Product.category).where(Product.category.is_not(None), Product.in_api.is_(True)).distinct().order_by(Product.category))]
    fr = next(f for f in freshness.freshness(db) if f["domain"] == "inventory")
    return render(c, "stock_compare.html", rows=rows[(page - 1) * PAGE: page * PAGE], locs=locs, total=len(rows), page=page, q=q, category=category, attention=attention, sort=sort,
                  cats=cats, fr=fr, summary=summary, short=lambda n: n.replace("The Daily Centro - ", "").replace("TheDailyCentro ", ""),
                  status=status, est=est, loc=loc, chips=chips, health=health, send_counts={lid: len(v) for lid, v in send_by_store.items()}, warehouse=wh)


# ======================================================================================= sales
@router.get("/sales/products")
def sales_by_product(loc: int = 0, days: str = "14", q: str = "", category: str = "", sort: str = "sales", page: int = 1, export: str = "",
                     frm: str = "", to: str = "", supplier: int = 0, c: Ctx = Depends(need("sales.view"))):
    """Every product sold in the period, from the line items of StoreHub transactions. loc=0 combines all locations the user may see.
    Period is a preset (days=N) or a custom range (days=custom&frm=YYYY-MM-DD&to=YYYY-MM-DD)."""
    db = c.db
    end = today_local()
    custom = days == "custom"
    if custom:
        try:
            f_, t_ = date.fromisoformat(frm), date.fromisoformat(to)
            if f_ > t_ or (t_ - f_).days > 400 or t_ > end:
                raise ValueError
            start, n_days, end = f_, (t_ - f_).days + 1, t_
        except ValueError:
            raise HTTPException(400, "Invalid date range. Enter valid dates, with From on or before To, no future dates, and no more than 401 days.")
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
    if supplier:
        stmt = stmt.where(Product.supplier_id == supplier)
    order = {"units": sold.c.units.desc(), "name": Product.name.asc(), "price": (sold.c.net / sold.c.units).desc()}.get(sort, sold.c.net.desc())
    rows = list(db.execute(stmt.order_by(order, Product.name)))
    total_net = sum(r[2] or 0 for r in rows) or 0.0
    total_units = sum(r[1] or 0 for r in rows) or 0.0
    if export in ("csv", "detailed"):
        out = io.StringIO()
        w = csv.writer(out)
        if export == "detailed":
            supplier_names = dict(db.execute(select(Supplier.id, Supplier.name)).all())
            location_name = next((l.name for l in c.locations() if l.id == loc), "All selected locations")
            filtered_ids = stmt.with_only_columns(Product.id).order_by(None)
            daily = {(pid, day): (units or 0, net or 0) for pid, day, units, net in db.execute(
                select(SalesDayProduct.product_id, SalesDayProduct.business_date, func.sum(SalesDayProduct.units), func.sum(SalesDayProduct.net_sales)).where(
                    SalesDayProduct.product_id.in_(filtered_ids), SalesDayProduct.location_id.in_(ids or [0]),
                    SalesDayProduct.business_date >= start, SalesDayProduct.business_date <= end
                ).group_by(SalesDayProduct.product_id, SalesDayProduct.business_date))}
            dates = [start + timedelta(days=offset) for offset in range(n_days)]
            headers = ["Product", "Barcode", "SKU", "Category", "Supplier", "Location",
                       "Total units sold", "Total net sales (PHP)", "Average price (PHP)"]
            for day in dates:
                headers.extend([f"{day.isoformat()} | Units", f"{day.isoformat()} | Net sales (PHP)"])
            w.writerow(headers)
            daily_totals = {day: [0.0, 0.0] for day in dates}
            for prod, product_units, product_net, _ in rows:
                product_units, product_net = product_units or 0, product_net or 0
                values = [prod.name, prod.barcode or "", prod.sku or "", prod.category or "",
                          supplier_names.get(prod.supplier_id, ""), location_name, f"{product_units:g}", f"{product_net:.2f}",
                          f"{product_net / product_units:.2f}" if product_units else "0.00"]
                for day in dates:
                    units, net = daily.get((prod.id, day), (0, 0))
                    values.extend([f"{units:g}", f"{net:.2f}"])
                    daily_totals[day][0] += units
                    daily_totals[day][1] += net
                w.writerow(values)
            if rows:
                totals = ["GRAND TOTAL", "", "", "", "", location_name, f"{total_units:g}", f"{total_net:.2f}",
                          f"{total_net / total_units:.2f}" if total_units else "0.00"]
                for day in dates:
                    units, net = daily_totals[day]
                    totals.extend([f"{units:g}", f"{net:.2f}"])
                w.writerow(totals)
        else:
            w.writerow(["Product", "Barcode", "Category", "Units sold", "Net sales", "Share of sales %", "Average price", "Days with sales"])
            for prod, units, net, ds in rows:
                w.writerow([prod.name, prod.barcode or "", prod.category or "", f"{units:g}", f"{net:.2f}", f"{(net / total_net * 100) if total_net else 0:.2f}",
                            f"{(net / units) if units else 0:.2f}", ds])
        audit.log(db, actor_type="user", actor_id=c.user.id, action="export_sales_by_product", entity_type="report", entity_id="sales_by_product",
                  after={"rows": len(rows), "days": n_days, "custom": f"{frm}..{to}" if custom else "", "location": loc, "supplier": supplier, "category": category, "search": q, "format": export})
        db.commit()
        fname = f"sales-by-product-{frm}-to-{to}.csv" if custom else f"sales-by-product-{n_days}d.csv"
        if export == "detailed":
            fname = f"sales-by-product-daily-{start}-to-{end}.csv"
        return Response("﻿" + out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{fname}"'})
    unlisted = 0.0  # sold items whose product StoreHub no longer lists are not in a product row; say so, so the period still adds up to the day totals
    if not q.strip() and not category and not supplier:
        day_net = db.scalar(select(func.sum(SalesDay.net_sales)).where(SalesDay.location_id.in_(ids or [0]), SalesDay.business_date >= start, SalesDay.business_date <= end)) or 0.0
        unlisted = round(day_net - total_net, 2) if day_net - total_net > 0.5 else 0.0
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
    cats = [x for x in db.scalars(select(Product.category).where(Product.category.is_not(None), Product.in_api.is_(True)).distinct().order_by(Product.category))]
    fr = next(f for f in freshness.freshness(db) if f["domain"] == "sales")
    return render(c, "sales_products.html", rows=shown, series=series, labels=[d.strftime("%a %d %b") for d in day_list], total=len(rows), total_net=total_net,
                  total_units=total_units, page=page, per=PAGE, offset=(page - 1) * PAGE, locations=c.locations(), loc=loc, days=n_days, q=q, category=category,
                  supplier=supplier, suppliers=list(db.scalars(select(Supplier).order_by(Supplier.name))), range_from=start.isoformat(), range_to=end.isoformat(), today_iso=today_local().isoformat(),
                  sort=sort, cats=cats, fr=fr, custom=custom, frm=frm, to=to, unlisted=unlisted)


@router.get("/sales/product/{pid}")
def sales_of_product(pid: int, days: str = "30", frm: str = "", to: str = "", list_query: str = "", loc: int = 0, c: Ctx = Depends(need("sales.view"))):
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
            if f_ > t_ or (t_ - f_).days > 400 or t_ > today:
                raise ValueError
            start, end, days = f_, t_, (t_ - f_).days + 1
        except ValueError:
            raise HTTPException(400, "Invalid date range. Enter valid dates, with From on or before To, no future dates, and no more than 401 days.")
    else:
        days = min(max(int(days) if days.isdigit() else 30, 1), 365)
        start = end - timedelta(days=days - 1)
    locs = c.locations()
    if loc:
        c.check_loc(loc)
        locs = [l for l in locs if l.id == loc]
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
    # Keep list navigation local and carry only supported filters (never export actions).
    list_filters = [(key, value) for key, value in parse_qsl(list_query)
                    if key in {"loc", "days", "frm", "to", "supplier", "category", "q", "sort", "page"}]
    list_query = urlencode(list_filters)
    fallback = {"loc": loc, "days": "custom", "frm": frm, "to": to} if custom else {"loc": loc, "days": days}
    sales_products_url = "/sales/products?" + (list_query or urlencode(fallback))
    return render(c, "sales_product.html", loc=loc, selected_location=locs[0].name if loc and locs else "All my locations", list_query=list_query, sales_products_url=sales_products_url, p=p, days=days, total_units=total_units, total_net=total_net, best=best, stores=stores, fr=fr,
                  series_units=series_units, daily=sorted(per_day.items(), reverse=True), custom=custom, frm=frm, to=to)


@router.get("/sales/report.xlsx")
def sales_report(loc: int = 0, c: Ctx = Depends(need("sales.view"))):
    """The whole sales history of one store as an Excel file in the layout of StoreHub's sales report (a sheet per month, Monthly Summary, All Sales)."""
    from ..services import sales_report
    location = _pick_loc(c, loc, prefer="store")
    if location is None:
        raise HTTPException(404)
    if not c.db.scalar(select(func.count()).select_from(SalesDay).where(SalesDay.location_id == location.id)):
        return redirect(f"/sales?loc={location.id}", err=c.t("There are no sales for this store yet."))
    raw = sales_report.workbook(c.db, location)
    audit.log(c.db, actor_type="user", actor_id=c.user.id, action="export_sales_report", entity_type="report", entity_id="sales_report", after={"location": location.id})
    c.db.commit()
    return Response(raw, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{sales_report.file_name(location)}"'})


class _Day:
    """One date's totals added up over several stores, shaped like a SalesDay for the page."""
    def __init__(self, day, rows):
        self.business_date = day
        for f in ("tx_count", "cancelled_count", "return_count", "gross_sales", "returns_total", "net_sales", "tax", "cash_sales", "other_sales"):
            setattr(self, f, round(sum(getattr(r, f) or 0 for r in rows), 2) if f not in ("tx_count", "cancelled_count", "return_count") else sum(getattr(r, f) or 0 for r in rows))
        gp_vals = [getattr(r, "gross_profit", None) for r in rows if getattr(r, "gross_profit", None) is not None]
        self.gross_profit = round(sum(gp_vals), 2) if gp_vals else None


def _sum_days(rows) -> list:
    by_day: dict = defaultdict(list)
    for r in rows:
        by_day[r.business_date].append(r)
    return [_Day(d, by_day[d]) for d in sorted(by_day, reverse=True)]


def _pay_bucket(method: str | None) -> str:
    """Group POS tender names into Cash, GCash/Maya e-wallet, or Card/Other."""
    m = (method or "").lower()
    if "cash" in m:
        return "cash"
    if any(k in m for k in ("gcash", "maya", "paymaya", "grab", "qr", "wallet", "shopeepay")):
        return "ewallet"
    return "card"


def _sales_pay_breakdown(db, loc_ids: list[int], start: date, end: date):
    """Aggregate tender split per day and overall for completed sales in [start, end]."""
    per_day: dict[date, dict[str, float]] = defaultdict(lambda: {"cash": 0.0, "ewallet": 0.0, "card": 0.0})
    overall: dict[str, float] = {"cash": 0.0, "ewallet": 0.0, "card": 0.0}
    by_method: dict[str, float] = defaultdict(float)
    if not loc_ids:
        return overall, per_day, []
    for bdate, method, amt in db.execute(
        select(SalesTx.business_date, Payment.method, func.sum(Payment.amount))
        .select_from(Payment)
        .join(SalesTx, SalesTx.id == Payment.tx_id)
        .where(
            SalesTx.location_id.in_(loc_ids),
            SalesTx.business_date >= start,
            SalesTx.business_date <= end,
            SalesTx.cancelled.is_(False),
            SalesTx.tx_type != "Return",
        )
        .group_by(SalesTx.business_date, Payment.method)
    ):
        val = float(amt or 0.0)
        b = _pay_bucket(method)
        per_day[bdate][b] += val
        overall[b] += val
        by_method[method or "Other"] += val
    methods = sorted(((k, round(v, 2)) for k, v in by_method.items() if abs(v) > 0.005), key=lambda x: -x[1])
    return overall, per_day, methods


@router.get("/sales")
def sales(loc: int = 0, days: str = "14", frm: str = "", to: str = "", export: str = "", c: Ctx = Depends(need("sales.view"))):
    db = c.db
    stores_in = [l for l in c.locations() if l.type == "store"]
    combined = not loc and len(stores_in) > 1        # "All": every store together (the warehouse sells nothing and is not listed)
    location = _pick_loc(c, loc, prefer="store")
    loc_ids = [l.id for l in stores_in] if combined else ([location.id] if location else [])
    today = today_local()
    end = today
    custom = days == "custom"
    if custom:
        try:
            f_, t_ = date.fromisoformat(frm), date.fromisoformat(to)
            if f_ > t_ or (t_ - f_).days > 400 or t_ > today:
                raise ValueError
            start, n_days, end = f_, (t_ - f_).days + 1, t_
        except ValueError:
            raise HTTPException(400, "Invalid date range. Enter valid dates, with From on or before To, no future dates, and no more than 401 days.")
    else:
        n_days = min(max(int(days) if days.isdigit() else 14, 1), 120)
        start = today - timedelta(days=n_days - 1)
    rows = []
    top = []
    top_units = []
    if location:
        rows = list(db.scalars(select(SalesDay).where(SalesDay.location_id.in_(loc_ids), SalesDay.business_date >= start, SalesDay.business_date <= end).order_by(SalesDay.business_date.desc())))
        if combined:
            rows = _sum_days(rows)
        top = list(db.execute(select(Product.name, func.sum(SalesDayProduct.units), func.sum(SalesDayProduct.net_sales)).join(Product, Product.id == SalesDayProduct.product_id)
                              .where(SalesDayProduct.location_id.in_(loc_ids), SalesDayProduct.business_date >= start, SalesDayProduct.business_date <= end)
                              .group_by(Product.name).order_by(func.sum(SalesDayProduct.net_sales).desc()).limit(15)))
        top_units = list(db.execute(select(Product.name, func.sum(SalesDayProduct.units), func.sum(SalesDayProduct.net_sales)).join(Product, Product.id == SalesDayProduct.product_id)
                                    .where(SalesDayProduct.location_id.in_(loc_ids), SalesDayProduct.business_date >= start, SalesDayProduct.business_date <= end)
                                    .group_by(Product.name).order_by(func.sum(SalesDayProduct.units).desc()).limit(8)))
    if export == "csv":
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Location", "Date", "Net sales", "Transactions", "Cash sales", "Other payments", "Cancelled"])
        for r in sorted(rows, key=lambda r: r.business_date):
            w.writerow([("All stores" if combined else location.name) if location else "", r.business_date, f"{r.net_sales:.2f}", r.tx_count, f"{r.cash_sales:.2f}",
                        f"{max(r.net_sales - r.cash_sales, 0):.2f}", r.cancelled_count])
        audit.log(db, actor_type="user", actor_id=c.user.id, action="export_sales_daily", entity_type="report", entity_id="sales_daily",
                  after={"rows": len(rows), "from": str(start), "to": str(end), "location": 0 if combined else (location.id if location else 0)})
        db.commit()
        return Response("﻿" + out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="daily-sales-{start}-to-{end}.csv"'})
    mx = max([r.net_sales for r in rows] or [1]) or 1
    totals = {
        "net": sum(r.net_sales for r in rows),
        "gross": sum(r.gross_sales or 0 for r in rows),
        "returns": sum(r.returns_total or 0 for r in rows),
        "tx": sum(r.tx_count for r in rows),
        "cash": sum(r.cash_sales for r in rows),
        "cancelled": sum(r.cancelled_count for r in rows),
        "tax": sum(r.tax or 0 for r in rows),
    }
    totals["other"] = max(totals["net"] - totals["cash"], 0)
    has_gp = any(r.gross_profit is not None for r in rows)
    totals["gp"] = sum((r.gross_profit or 0) for r in rows) if has_gp else None
    totals["has_gp"] = has_gp

    pay_overall, day_pay_map, _ = _sales_pay_breakdown(db, loc_ids, start, end)
    day_splits = {}
    for r in rows:
        dp = day_pay_map.get(r.business_date)
        if dp and (dp["ewallet"] > 0 or dp["card"] > 0):
            ew, cd = dp["ewallet"], dp["card"]
            # Keep ewallet + card reconciled with r.other_sales when rounded or returns exist
            other_total = max(r.other_sales or (r.net_sales - r.cash_sales), 0)
            split_sum = ew + cd
            if split_sum > 0 and other_total >= 0:
                ew_r = round(other_total * (ew / split_sum), 2)
                cd_r = round(max(other_total - ew_r, 0), 2)
            else:
                ew_r, cd_r = round(ew, 2), round(cd, 2)
        else:
            ew_r, cd_r = 0.0, round(max(r.other_sales or (r.net_sales - r.cash_sales), 0), 2)
        day_splits[r.business_date] = {"ewallet": ew_r, "card": cd_r}
    totals["ewallet"] = round(sum(v["ewallet"] for v in day_splits.values()), 2)
    totals["card"] = round(sum(v["card"] for v in day_splits.values()), 2)

    prev_totals = None   # the window of equal length right before this one; None when there is nothing to compare with
    fr = next(f for f in freshness.freshness(db) if f["domain"] == "sales")
    ordered_rows = list(reversed(rows))
    chart = [(r.business_date.strftime("%d %b"), round(r.net_sales, 2), r.business_date.isoformat()) for r in ordered_rows]
    tx_chart = [(r.business_date.strftime("%d %b"), int(r.tx_count or 0), r.business_date.isoformat()) for r in ordered_rows]
    basket_chart = [(r.business_date.strftime("%d %b"), round(r.net_sales / r.tx_count, 2) if r.tx_count else 0.0, r.business_date.isoformat()) for r in ordered_rows]
    compare = []
    tx_compare = []
    basket_compare = []
    hourly = False
    if n_days == 1 and location and rows:   # one day is shown by hour: a single bar for a whole day says very little
        by_hour: dict[int, float] = defaultdict(float)
        by_hour_tx: dict[int, int] = defaultdict(int)
        for t_time, tx_type, total in db.execute(select(SalesTx.tx_time, SalesTx.tx_type, SalesTx.total).where(
                SalesTx.location_id.in_(loc_ids), SalesTx.business_date == end, SalesTx.cancelled.is_(False))):
            h = to_local(t_time).hour
            by_hour[h] += -total if tx_type == "Return" else total
            if tx_type != "Return":
                by_hour_tx[h] += 1
        if by_hour:
            first, last = min(min(by_hour), 6), max(by_hour)
            if end == today:
                last = max(last, to_local(now_utc()).hour)
            hours_seq = list(range(first, last + 1))
            chart = [(f"{(h % 12) or 12}{'am' if h < 12 else 'pm'}", round(by_hour.get(h, 0.0), 2), end.isoformat()) for h in hours_seq]
            tx_chart = [(f"{(h % 12) or 12}{'am' if h < 12 else 'pm'}", by_hour_tx.get(h, 0), end.isoformat()) for h in hours_seq]
            basket_chart = [(f"{(h % 12) or 12}{'am' if h < 12 else 'pm'}", round(by_hour.get(h, 0.0) / by_hour_tx[h], 2) if by_hour_tx.get(h) else 0.0, end.isoformat()) for h in hours_seq]
            hourly = True
    if location and rows and not hourly:  # the window of equal length right before this one, matched day by day
        prev: dict = defaultdict(float)
        prev_tx_map: dict = defaultdict(int)
        for r in db.scalars(select(SalesDay).where(SalesDay.location_id.in_(loc_ids), SalesDay.business_date >= start - timedelta(days=n_days), SalesDay.business_date < start)):
            prev[r.business_date] += r.net_sales
            prev_tx_map[r.business_date] += r.tx_count
        if prev:
            prev_totals = {"net": sum(prev.values()), "tx": sum(prev_tx_map.values())}
        compare = [round(prev.get(r.business_date - timedelta(days=n_days), 0.0), 2) for r in ordered_rows] if prev else []
        tx_compare = [int(prev_tx_map.get(r.business_date - timedelta(days=n_days), 0)) for r in ordered_rows] if prev else []
        basket_compare = [
            round(prev.get(r.business_date - timedelta(days=n_days), 0.0) / prev_tx_map[r.business_date - timedelta(days=n_days)], 2)
            if prev_tx_map.get(r.business_date - timedelta(days=n_days)) else 0.0
            for r in ordered_rows
        ] if prev else []

    # Multi-store comparison (shown when user has >1 store; highlights active branch when filtering)
    store_stats = []
    if len(stores_in) > 1:
        s_ids = [s.id for s in stores_in]
        cur_by_store: dict[int, dict[str, float]] = {sid: {"net": 0.0, "tx": 0} for sid in s_ids}
        prev_by_store: dict[int, float] = defaultdict(float)
        for lid, net_s, tx_c in db.execute(
            select(SalesDay.location_id, func.sum(SalesDay.net_sales), func.sum(SalesDay.tx_count))
            .where(SalesDay.location_id.in_(s_ids), SalesDay.business_date >= start, SalesDay.business_date <= end)
            .group_by(SalesDay.location_id)
        ):
            cur_by_store[lid] = {"net": float(net_s or 0.0), "tx": int(tx_c or 0)}
        for lid, net_s in db.execute(
            select(SalesDay.location_id, func.sum(SalesDay.net_sales))
            .where(SalesDay.location_id.in_(s_ids), SalesDay.business_date >= start - timedelta(days=n_days), SalesDay.business_date < start)
            .group_by(SalesDay.location_id)
        ):
            prev_by_store[lid] = float(net_s or 0.0)
        all_stores_net = sum(v["net"] for v in cur_by_store.values())
        for s in stores_in:
            st = cur_by_store[s.id]
            pv = prev_by_store.get(s.id, 0.0)
            delta = ((st["net"] - pv) / pv * 100) if pv > 0 else None
            store_stats.append({
                "id": s.id,
                "name": _short_loc(s.name),
                "full_name": s.name,
                "net": round(st["net"], 2),
                "tx": st["tx"],
                "basket": round(st["net"] / st["tx"], 2) if st["tx"] else 0.0,
                "share": round(st["net"] / all_stores_net * 100, 1) if all_stores_net > 0 else 0.0,
                "delta": round(delta, 1) if delta is not None else None,
            })
        store_stats.sort(key=lambda x: -x["net"])

    # Weekday rhythm (average daily net sales per weekday Mon..Sun)
    dow_buckets: dict[int, list[float]] = defaultdict(list)
    for r in rows:
        dow_buckets[r.business_date.weekday()].append(r.net_sales)
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dow_stats = []
    for idx, label in enumerate(dow_names):
        vals = dow_buckets.get(idx, [])
        avg_v = sum(vals) / len(vals) if vals else 0.0
        dow_stats.append({"label": label, "avg": round(avg_v, 2), "days": len(vals)})
    dow_active = [d for d in dow_stats if d["days"] > 0]
    dow_mx = max([d["avg"] for d in dow_active] or [1.0]) or 1.0
    peak_dow = max(dow_active, key=lambda d: d["avg"]) if dow_active else None
    low_dow = min(dow_active, key=lambda d: d["avg"]) if len(dow_active) > 1 else None

    # Peak day & period signals
    peak_day = max(rows, key=lambda r: r.net_sales) if len(rows) > 1 else None
    avg_day_net = (totals["net"] / len(rows)) if rows else 0.0
    peak_vs_avg = ((peak_day.net_sales - avg_day_net) / avg_day_net * 100) if (peak_day and avg_day_net > 0) else 0.0
    if peak_vs_avg < 10.0:
        peak_day = None
    cx_rate = (totals["cancelled"] / (totals["tx"] + totals["cancelled"]) * 100) if (totals["tx"] + totals["cancelled"]) > 0 else 0.0
    ret_rate = (totals["returns"] / totals["gross"] * 100) if totals["gross"] > 0 else 0.0

    chart_payload = {
        "labels": [p[0] for p in chart],
        "dates": [p[2] for p in chart],
        "hourly": hourly,
        "net": [p[1] for p in chart],
        "net_prev": compare,
        "tx": [p[1] for p in tx_chart],
        "tx_prev": tx_compare,
        "basket": [p[1] for p in basket_chart],
        "basket_prev": basket_compare,
    }
    return render(
        c, "sales.html",
        chart=chart, compare=compare, hourly=hourly, combined=combined, rows=rows,
        top=top, top_units=top_units, location=location, locations=c.locations(), days=n_days,
        mx=mx, totals=totals, prev_totals=prev_totals, fr=fr, custom=custom, frm=frm, to=to,
        range_from=start.isoformat(), range_to=end.isoformat(), today_iso=today.isoformat(),
        day_splits=day_splits, store_stats=store_stats, dow_stats=dow_stats, dow_mx=dow_mx,
        peak_dow=peak_dow, low_dow=low_dow, peak_day=peak_day, peak_vs_avg=peak_vs_avg,
        cx_rate=cx_rate, ret_rate=ret_rate, chart_payload=chart_payload,
    )


@router.get("/sales/day-panel")
def sales_day_panel(day: str = "", loc: int = 0, c: Ctx = Depends(need("sales.view"))):
    """Slide-over daily summary drawer for /sales: hourly curve, payment breakdown, top SKUs."""
    try:
        d = date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "Invalid date.")
    db = c.db
    stores_in = [l for l in c.locations() if l.type == "store"]
    combined = not loc and len(stores_in) > 1
    location = _pick_loc(c, loc, prefer="store")
    if location is None:
        raise HTTPException(404)
    loc_ids = [l.id for l in stores_in] if combined else [location.id]
    day_rows = list(db.scalars(select(SalesDay).where(SalesDay.location_id.in_(loc_ids), SalesDay.business_date == d)))
    summary = _sum_days(day_rows)[0] if day_rows else None
    if summary is None:
        class _EmptyDay:
            business_date = d
            tx_count = cancelled_count = return_count = 0
            gross_sales = returns_total = net_sales = tax = cash_sales = other_sales = 0.0
        summary = _EmptyDay()

    _, _, methods = _sales_pay_breakdown(db, loc_ids, d, d)
    if not methods and (summary.cash_sales or summary.other_sales):
        if summary.cash_sales:
            methods.append(("Cash", round(summary.cash_sales, 2)))
        if summary.other_sales:
            methods.append(("Other", round(summary.other_sales, 2)))

    by_hour: dict[int, float] = defaultdict(float)
    for t_time, tx_type, total in db.execute(
        select(SalesTx.tx_time, SalesTx.tx_type, SalesTx.total).where(
            SalesTx.location_id.in_(loc_ids), SalesTx.business_date == d, SalesTx.cancelled.is_(False)
        )
    ):
        by_hour[to_local(t_time).hour] += -total if tx_type == "Return" else total
    hours = []
    if by_hour:
        first, last = min(min(by_hour), 7), max(by_hour)
        h_mx = max(by_hour.values()) or 1.0
        for h in range(first, last + 1):
            val = round(by_hour.get(h, 0.0), 2)
            hours.append({
                "label": f"{(h % 12) or 12}{'a' if h < 12 else 'p'}",
                "val": val,
                "pct": round(max(val, 0.0) / h_mx * 100, 1) if h_mx else 0.0,
            })

    top_prods = list(db.execute(
        select(Product.name, func.sum(SalesDayProduct.units), func.sum(SalesDayProduct.net_sales))
        .join(Product, Product.id == SalesDayProduct.product_id)
        .where(SalesDayProduct.location_id.in_(loc_ids), SalesDayProduct.business_date == d)
        .group_by(Product.name).order_by(func.sum(SalesDayProduct.net_sales).desc()).limit(6)
    ))
    loc_q = 0 if combined else location.id
    return render(
        c, "_sales_day_panel.html",
        day=d, loc_label=c.t("All stores") if combined else location.name,
        loc_q=loc_q, summary=summary, methods=methods, hours=hours, top_prods=top_prods,
    )


# ======================================================================================= transactions
def _period(days: str, frm: str, to: str, default: int = 14, cap: int = 180):
    """Period shared by the transaction pages: a preset (days=N) or a custom range (days=custom&frm&to).
    Returns (start, end, n_days, custom, frm, to); an invalid custom range returns an explicit error."""
    today = today_local()
    if days == "custom":
        try:
            f_, t_ = date.fromisoformat(frm), date.fromisoformat(to)
            if f_ > t_ or (t_ - f_).days > 400 or t_ > today:
                raise ValueError
            return f_, t_, (t_ - f_).days + 1, True, frm, to
        except ValueError:
            raise HTTPException(400, "Invalid date range. Enter valid dates, with From on or before To, no future dates, and no more than 401 days.")
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
                       export: str = "", shift_id: int = 0, pay: str = "", c: Ctx = Depends(need("sales.detail"))):
    """Every transaction in the period; a row opens the full receipt. loc=0 combines all locations the user may see."""
    db = c.db
    shift = None
    shift_end = None
    if shift_id:
        from ..models import SalesShift
        from ..services import sales_shifts
        shift = db.get(SalesShift, shift_id)
        if shift is None:
            raise HTTPException(404, "Shift not found")
        c.check_loc(shift.location_id)
        matched = sales_shifts.cashier_employee_ids(db, [shift])
        if shift.id not in matched:
            raise HTTPException(400, "The shift cashier cannot be uniquely matched to an employee.")
        loc, emp = shift.location_id, matched[shift.id]
        shift_end = shift.closed_at or to_local(now_utc())
        if shift_end <= shift.opened_at:
            raise HTTPException(400, "Invalid shift time range")
        days, frm, to = "custom", shift.opened_at.date().isoformat(), shift_end.date().isoformat()
    start, end, n_days, custom, frm, to = _period(days, frm, to)
    ids = [l.id for l in c.locations()]
    if loc:
        c.check_loc(loc)
        ids = [loc]
    stmt = select(SalesTx).where(SalesTx.location_id.in_(ids or [0]))
    if shift:
        # Shift-report times are Manila local; transaction timestamps are naive UTC.
        opened_utc = shift.opened_at.replace(tzinfo=tz()).astimezone(timezone.utc).replace(tzinfo=None)
        closed_utc = shift_end.replace(tzinfo=tz()).astimezone(timezone.utc).replace(tzinfo=None)
        stmt = stmt.where(SalesTx.tx_time >= opened_utc, SalesTx.tx_time < closed_utc)
    else:
        stmt = stmt.where(SalesTx.business_date >= start, SalesTx.business_date <= end)
    pay = pay.strip().lower()[:40]

    def _xtra(s):
        # employee / invoice-search / payment filters, shared with the previous-period comparison
        if emp:
            s = s.where(SalesTx.employee_sh_id == emp)
        if q.strip():
            s = s.where(SalesTx.invoice_no.ilike(f"%{q.strip()}%"))
        if pay == "!cash":      # any payment that is not cash (GCash, Food Panda, Grab, cards ...)
            s = s.where(select(Payment.id).where(Payment.tx_id == SalesTx.id, func.lower(Payment.method) != "cash").exists())
        elif pay:
            s = s.where(select(Payment.id).where(Payment.tx_id == SalesTx.id, func.lower(Payment.method) == pay).exists())
        return s

    stmt = _xtra(stmt).order_by(SalesTx.tx_time.desc())
    loc_names = {l.id: _short_loc(l.name) for l in c.locations()}
    if export == "csv":
        txs_all = list(db.scalars(stmt))
        ids_all = [t.id for t in txs_all]
        names = _emp_names(db, {t.employee_sh_id for t in txs_all if t.employee_sh_id})
        pays = defaultdict(list)
        lines = defaultdict(list)
        for k in range(0, len(ids_all), 500):          # in chunks, so a long period never hits the database's limit on a list of ids
            chunk = ids_all[k:k + 500]
            for tid, m, a in db.execute(select(Payment.tx_id, Payment.method, Payment.amount).where(Payment.tx_id.in_(chunk)).order_by(Payment.id)):
                pays[tid].append(f"{m} {a:.2f}")
            for ln, pname in db.execute(select(SalesLine, Product.name).outerjoin(Product, Product.id == SalesLine.product_id)
                                        .where(SalesLine.tx_id.in_(chunk)).order_by(SalesLine.tx_id, SalesLine.line_no)):
                lines[ln.tx_id].append((ln.qty, pname or "(unknown product)", ln.total))

        def cell(v) -> str:                            # text that starts with = + - @ must not run as a formula in Excel
            v = str(v or "")
            return "'" + v if v.lstrip().startswith(("=", "+", "-", "@")) else v
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Time", "Invoice", "Store", "Employee", "Type", "Status", "Payments", "Products", "Subtotal", "Discount", "Tax", "Total"])
        for t in txs_all:
            products = "; ".join(f"{pname} x{qty:g} ({amount:.2f})" for qty, pname, amount in lines.get(t.id, []))      # every product of the invoice in one cell: its quantity, then its total
            w.writerow([fmt_local(t.tx_time), t.invoice_no or "", loc_names.get(t.location_id, ""), names.get(t.employee_sh_id or "", ""),
                        t.tx_type, "Cancelled" if t.cancelled else "", "; ".join(pays.get(t.id, [])), cell(products), f"{t.subtotal:.2f}", f"{t.discount:.2f}", f"{t.tax:.2f}", f"{t.total:.2f}"])
        audit.log(db, actor_type="user", actor_id=c.user.id, action="export_transactions", entity_type="report", entity_id="transactions",
                  after={"rows": len(txs_all), "days": n_days, "custom": f"{frm}..{to}" if custom else "", "location": loc, "employee": emp, "shift_id": shift_id})
        db.commit()
        fname = f"transactions-{frm}-to-{to}.csv" if custom else f"transactions-{n_days}d.csv"
        return Response("\ufeff" + out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{fname}"'})
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
    if emp and emp not in emp_ids:
        emp_ids.append(emp)
    employees = sorted(_emp_names(db, emp_ids).items(), key=lambda kv: kv[1])
    methods: dict[str, str] = {}      # one entry per payment method, however it is capitalised (GCASH and GCash are the same)
    for (m,) in db.execute(select(Payment.method).join(SalesTx, SalesTx.id == Payment.tx_id).where(SalesTx.location_id.in_(ids or [0]), SalesTx.business_date >= start,
                                                                                               SalesTx.business_date <= end).distinct()):
        if m and m.strip().lower() != "cash":
            methods.setdefault(m.strip().lower(), m.strip())
    pay_options = sorted(methods.items(), key=lambda kv: kv[1].lower())
    fr = next(f for f in freshness.freshness(db) if f["domain"] == "sales")
    # figures for everything the filters match (not just this page): sales net of returns, how many, the average basket and the cash share
    base = stmt.order_by(None)
    signed = case((SalesTx.tx_type == "Return", -SalesTx.total), else_=SalesTx.total)
    live = SalesTx.cancelled.is_(False)
    net, live_n = db.execute(base.with_only_columns(func.coalesce(func.sum(signed), 0.0), func.count()).where(live)).one()
    sale_sum, sale_n = db.execute(base.with_only_columns(func.coalesce(func.sum(SalesTx.total), 0.0), func.count()).where(live, SalesTx.tx_type != "Return")).one()
    returned_n = db.scalar(base.with_only_columns(func.count()).where(live, SalesTx.tx_type == "Return")) or 0
    cancelled_n = db.scalar(base.with_only_columns(func.count()).where(SalesTx.cancelled.is_(True))) or 0
    sale_ids = base.with_only_columns(SalesTx.id).where(live, SalesTx.tx_type != "Return")
    cash_amt, paid_amt = db.execute(select(func.coalesce(func.sum(case((func.lower(Payment.method) == "cash", Payment.amount), else_=0.0)), 0.0), func.coalesce(func.sum(Payment.amount), 0.0))
                                    .where(Payment.tx_id.in_(sale_ids))).one()
    # tender split of the filtered sales, bucketed for the cash-share cell
    split = {"cash": 0.0, "ewallet": 0.0, "card": 0.0}
    for m, amt in db.execute(select(Payment.method, func.coalesce(func.sum(Payment.amount), 0.0)).where(Payment.tx_id.in_(sale_ids)).group_by(Payment.method)):
        split[_pay_bucket(m)] += amt or 0.0
    returns_amt = db.scalar(base.with_only_columns(func.coalesce(func.sum(SalesTx.total), 0.0)).where(live, SalesTx.tx_type == "Return")) or 0.0
    summary = {"net": net, "count": live_n, "avg": (sale_sum / sale_n) if sale_n else 0.0, "cash_pct": round(cash_amt / paid_amt * 100) if paid_amt else 0, "returned": returned_n, "cancelled": cancelled_n,
               "split": split, "returns_amt": returns_amt, "cx_pct": (cancelled_n / (live_n + cancelled_n) * 100) if live_n + cancelled_n else 0.0}
    prev_summary = None    # the window of equal length right before this one; None in shift mode or when empty
    if not shift:
        pstmt = _xtra(select(SalesTx).where(SalesTx.location_id.in_(ids or [0]),
                                            SalesTx.business_date >= start - timedelta(days=n_days), SalesTx.business_date < start))
        p_net, p_n = db.execute(pstmt.with_only_columns(func.coalesce(func.sum(signed), 0.0), func.count()).where(live)).one()
        p_sale_sum, p_sale_n = db.execute(pstmt.with_only_columns(func.coalesce(func.sum(SalesTx.total), 0.0), func.count()).where(live, SalesTx.tx_type != "Return")).one()
        if p_n:
            prev_summary = {"net": p_net, "count": p_n, "avg": (p_sale_sum / p_sale_n) if p_sale_n else 0.0}
    day_net = {d: (n, k) for d, n, k in db.execute(base.with_only_columns(SalesTx.business_date, func.coalesce(func.sum(signed), 0.0), func.count()).where(live).group_by(SalesTx.business_date))}
    today = today_local()
    groups: list[dict] = []   # the page is shown a day at a time, newest first
    for t in txs:
        if not groups or groups[-1]["date"] != t.business_date:
            n, k = day_net.get(t.business_date, (0.0, 0))
            groups.append({"date": t.business_date, "rows": [], "net": n, "count": k, "rel": "Today" if t.business_date == today else "Yesterday" if t.business_date == today - timedelta(days=1) else ""})
        groups[-1]["rows"].append(t)
    return render(c, "sales_tx.html", summary=summary, prev_summary=prev_summary, groups=groups, today=today, pay=pay, pay_options=pay_options, txs=txs, shift=shift, shift_end=shift_end, names=names, pays=pays, units=units, loc_names=loc_names, employees=employees, emp=emp, q=q,
                  total=total, page=page, per=PAGE, offset=(page - 1) * PAGE, locations=c.locations(), loc=loc, days=n_days, custom=custom, frm=frm, to=to, fr=fr,
                  range_from=start.isoformat(), range_to=end.isoformat(), today_iso=today_local().isoformat())


@router.get("/sales/tx/{txid}/panel")
def sales_tx_panel(txid: int, c: Ctx = Depends(need("sales.detail"))):
    """The compact receipt shown in the slide-over on the Transactions list."""
    db = c.db
    t = db.get(SalesTx, txid)
    if t is None:
        raise HTTPException(404)
    c.check_loc(t.location_id)
    lines = list(db.execute(select(SalesLine, Product.name).outerjoin(Product, Product.id == SalesLine.product_id).where(SalesLine.tx_id == txid).order_by(SalesLine.line_no)))
    pays = list(db.execute(select(Payment.method, Payment.amount).where(Payment.tx_id == txid).order_by(Payment.id)))
    emp = db.scalar(select(Employee).where(Employee.storehub_id == t.employee_sh_id)) if t.employee_sh_id else None
    return render(c, "_tx_panel.html", tx=t, lines=lines, pays=pays, emp=emp, loc=db.get(Location, t.location_id), units=sum(l[0].qty or 0 for l in lines))


@router.get("/sales/tx/{txid}")
def sales_tx_detail(txid: int, c: Ctx = Depends(need("sales.detail"))):
    """One receipt: the items sold, payments and totals, exactly as synced from StoreHub."""
    db = c.db
    t = db.get(SalesTx, txid)
    if t is None:
        raise HTTPException(404)
    c.check_loc(t.location_id)
    lines = list(db.execute(select(SalesLine, Product.name, Product.barcode, Product.sku)
                            .outerjoin(Product, Product.id == SalesLine.product_id)
                            .where(SalesLine.tx_id == txid).order_by(SalesLine.line_no)))
    pays = list(db.execute(select(Payment.method, Payment.amount).where(Payment.tx_id == txid).order_by(Payment.id)))
    emp = db.scalar(select(Employee).where(Employee.storehub_id == t.employee_sh_id)) if t.employee_sh_id else None
    # the sale just before and just after this one among the stores this person may see, and (for a return) the sale it came from
    mine = [l.id for l in c.locations()] or [0]
    newer_id = db.scalar(select(SalesTx.id).where(SalesTx.location_id.in_(mine), (SalesTx.tx_time > t.tx_time) | ((SalesTx.tx_time == t.tx_time) & (SalesTx.id > t.id)))
                         .order_by(SalesTx.tx_time.asc(), SalesTx.id.asc()).limit(1))
    older_id = db.scalar(select(SalesTx.id).where(SalesTx.location_id.in_(mine), (SalesTx.tx_time < t.tx_time) | ((SalesTx.tx_time == t.tx_time) & (SalesTx.id < t.id)))
                         .order_by(SalesTx.tx_time.desc(), SalesTx.id.desc()).limit(1))
    orig_id = db.scalar(select(SalesTx.id).where(SalesTx.invoice_no == t.sale_invoice_no, SalesTx.location_id == t.location_id, SalesTx.id != t.id).limit(1)) if t.sale_invoice_no else None
    return render(c, "sales_tx_detail.html", tx=t, lines=lines, pays=pays, emp=emp, loc=db.get(Location, t.location_id),
                  has_disc=any(l[0].discount for l in lines), units=sum(l[0].qty or 0 for l in lines), newer_id=newer_id, older_id=older_id, orig_id=orig_id)


def _emp_scope(c: Ctx, loc: int, days: str, frm: str, to: str):
    start, end, n_days, custom, frm, to = _period(days, frm, to)
    ids = [l.id for l in c.locations()]
    if loc:
        c.check_loc(loc)
        ids = [loc]
    scope = [SalesTx.location_id.in_(ids or [0]), SalesTx.business_date >= start, SalesTx.business_date <= end]
    return start, end, n_days, custom, frm, to, ids, scope


def _initials(name: str) -> str:
    parts = [p for p in (name or "").replace("—", "").split() if p]
    if not parts:
        return "—"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


@router.get("/sales/employees")
def sales_by_employee(loc: int = 0, days: str = "14", frm: str = "", to: str = "", export: str = "", c: Ctx = Depends(need("sales.detail"))):
    """Sales per employee in the period: transactions, items, net sales and average basket. Cancelled sales are excluded from the figures."""
    db = c.db
    start, end, n_days, custom, frm, to, ids, scope = _emp_scope(c, loc, days, frm, to)
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
    stats = []
    for r in rows:
        sh_id, txs, net, cancelled, last = r[0], r[1], r[2] or 0.0, r[3] or 0, r[4]
        live_txs = max(txs - cancelled, 0)
        emp_units = units.get(sh_id, 0.0) or 0.0
        name = names.get(sh_id, "—") if sh_id else "—"
        stats.append({
            "sh_id": sh_id,
            "emp_key": sh_id if sh_id else "__none__",
            "name": name,
            "initials": _initials(name),
            "txs": txs,
            "net": net,
            "cancelled": cancelled,
            "units": emp_units,
            "last": last,
            "stores": sorted(loc_names[i] for i in locs_of.get(sh_id, set()) if i in loc_names),
            "avg": net / live_txs if live_txs > 0 else 0.0,
            "units_per_tx": emp_units / live_txs if live_txs > 0 else 0.0,
            "cancel_rate": (cancelled / txs * 100.0) if txs else 0.0,
        })
    stats.sort(key=lambda s: -s["net"])
    total_net = sum(s["net"] for s in stats)
    total_txs = sum(s["txs"] for s in stats)
    total_units = sum(s["units"] for s in stats)
    total_cancelled = sum(s["cancelled"] for s in stats)
    total_live = max(total_txs - total_cancelled, 0)
    totals = {
        "net": total_net,
        "txs": total_txs,
        "units": total_units,
        "cancelled": total_cancelled,
        "avg": total_net / total_live if total_live > 0 else 0.0,
        "avg_per_day": total_net / n_days if n_days > 0 else 0.0,
        "units_per_tx": total_units / total_live if total_live > 0 else 0.0,
        "cancel_rate": (total_cancelled / total_txs * 100.0) if total_txs else 0.0,
        "top3_share": (sum(s["net"] for s in stats[:3]) / total_net * 100.0) if total_net else 0.0,
    }
    for s in stats:
        s["share"] = (s["net"] / total_net * 100.0) if total_net else 0.0
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

    prev_start, prev_end = start - timedelta(days=n_days), start - timedelta(days=1)
    prev_scope = [SalesTx.location_id.in_(ids or [0]), SalesTx.business_date >= prev_start, SalesTx.business_date <= prev_end]
    p_net, p_txs, p_live = db.execute(select(
        func.coalesce(func.sum(case((SalesTx.cancelled, 0.0), else_=SalesTx.total)), 0.0),
        func.count(),
        func.coalesce(func.sum(case((SalesTx.cancelled, 0), else_=1)), 0),
    ).where(*prev_scope)).one()
    prev_totals = None
    if p_txs:
        p_units = db.scalar(select(func.coalesce(func.sum(SalesLine.qty), 0.0)).select_from(SalesTx).join(SalesLine, SalesLine.tx_id == SalesTx.id)
                            .where(*prev_scope, SalesTx.cancelled == False)) or 0.0  # noqa: E712
        prev_totals = {
            "net": float(p_net or 0.0),
            "txs": int(p_txs),
            "units": float(p_units),
            "avg": (float(p_net or 0.0) / p_live) if p_live else 0.0,
        }

    store_rows = list(db.execute(select(
        SalesTx.location_id,
        func.coalesce(func.sum(case((SalesTx.cancelled, 0.0), else_=SalesTx.total)), 0.0),
    ).where(*scope).group_by(SalesTx.location_id)))
    store_stats = sorted(
        [{"id": lid, "name": loc_names.get(lid, f"#{lid}"), "net": float(snet or 0.0)} for lid, snet in store_rows if lid in loc_names],
        key=lambda r: -r["net"],
    )
    max_store_net = max((r["net"] for r in store_stats), default=1.0) or 1.0
    for sr in store_stats:
        sr["share"] = (sr["net"] / total_net * 100.0) if total_net else 0.0
        sr["bar_pct"] = (sr["net"] / max_store_net * 100.0) if max_store_net else 0.0

    checks = []
    hi_cancel = sorted([s for s in stats if s["cancelled"] >= 2 and s["cancel_rate"] >= 3.0], key=lambda s: -s["cancel_rate"])
    for s in hi_cancel[:2]:
        checks.append({"kind": "warn", "emp": s, "type": "cancel"})
    unassigned = next((s for s in stats if not s["sh_id"]), None)
    if unassigned:
        checks.append({"kind": "info", "emp": unassigned, "type": "unassigned"})

    active_staff = db.scalar(select(func.count()).select_from(Employee)) or len(stats)
    fr = next(f for f in freshness.freshness(db) if f["domain"] == "sales")
    return render(c, "sales_employees.html", stats=stats, totals=totals, prev_totals=prev_totals,
                  store_stats=store_stats, checks=checks, active_staff=active_staff,
                  locations=c.locations(), loc=loc, days=n_days, custom=custom, frm=frm, to=to, fr=fr,
                  range_from=start.isoformat(), range_to=end.isoformat(), today_iso=today_local().isoformat())


@router.get("/sales/employees/panel")
def sales_employee_panel(emp: str = "", loc: int = 0, days: str = "14", frm: str = "", to: str = "", c: Ctx = Depends(need("sales.detail"))):
    """Slide-over breakdown for a single employee on Sales by employee: daily trend, store split, and recent receipts."""
    db = c.db
    start, end, n_days, custom, frm, to, ids, scope = _emp_scope(c, loc, days, frm, to)
    unassigned = not emp or emp == "__none__"
    emp_filter = SalesTx.employee_sh_id.is_(None) if unassigned else (SalesTx.employee_sh_id == emp)
    emp_scope = scope + [emp_filter]

    txs_count, net, cancelled = db.execute(select(
        func.count(),
        func.coalesce(func.sum(case((SalesTx.cancelled, 0.0), else_=SalesTx.total)), 0.0),
        func.coalesce(func.sum(case((SalesTx.cancelled, 1), else_=0)), 0),
    ).where(*emp_scope)).one()
    units = db.scalar(select(func.coalesce(func.sum(SalesLine.qty), 0.0)).select_from(SalesTx).join(SalesLine, SalesLine.tx_id == SalesTx.id)
                      .where(*emp_scope, SalesTx.cancelled == False)) or 0.0  # noqa: E712
    live_txs = max((txs_count or 0) - (cancelled or 0), 0)
    emp_info = {
        "txs": int(txs_count or 0),
        "net": float(net or 0.0),
        "cancelled": int(cancelled or 0),
        "units": float(units),
        "avg": float(net or 0.0) / live_txs if live_txs > 0 else 0.0,
        "units_per_tx": float(units) / live_txs if live_txs > 0 else 0.0,
    }

    trend_start = max(start, end - timedelta(days=29))
    by_day = {d: float(v or 0.0) for d, v in db.execute(
        select(SalesTx.business_date, func.sum(case((SalesTx.cancelled, 0.0), else_=SalesTx.total)))
        .where(*emp_scope, SalesTx.business_date >= trend_start, SalesTx.business_date <= end)
        .group_by(SalesTx.business_date)
    )}
    span_days = (end - trend_start).days + 1
    daily = []
    for offset in range(span_days):
        d = trend_start + timedelta(days=offset)
        daily.append({"label": d.strftime("%d %b"), "net": by_day.get(d, 0.0)})

    loc_names = {l.id: _short_loc(l.name) for l in c.locations()}
    split_rows = sorted(
        [(lid, float(v or 0.0)) for lid, v in db.execute(
            select(SalesTx.location_id, func.sum(case((SalesTx.cancelled, 0.0), else_=SalesTx.total)))
            .where(*emp_scope).group_by(SalesTx.location_id)
        ) if lid in loc_names],
        key=lambda x: -x[1],
    )
    max_split = max((v for _, v in split_rows), default=1.0) or 1.0
    store_split = [{"name": loc_names[lid], "net": v, "pct": (v / max_split * 100.0) if max_split else 0.0} for lid, v in split_rows]

    recent_txs = list(db.scalars(select(SalesTx).where(*emp_scope).order_by(SalesTx.tx_time.desc()).limit(6)))
    qs = f"loc={loc}&days={'custom' if custom else n_days}"
    if custom:
        qs += f"&frm={frm}&to={to}"
    if not unassigned:
        qs += f"&emp={emp}"
    return render(c, "_employee_panel.html", emp_info=emp_info, daily=daily, store_split=store_split,
                  recent_txs=recent_txs, loc_names=loc_names, tx_url=f"/sales/transactions?{qs}")


@router.get("/sales/shifts")
def sales_by_shift(loc: int = 0, days: str = "14", frm: str = "", to: str = "", status: str = "", export: str = "", c: Ctx = Depends(need("sales.detail"))):
    """Register shifts as StoreHub's own Shift Reports show them: opened and closed, cashier, sales, and how it was paid. Read from StoreHub's back office."""
    from ..models import SalesShift
    from ..services import sales_shifts
    db = c.db
    start, end, n_days, custom, frm, to = _period(days, frm, to)
    ids = [l.id for l in c.locations() if l.type == "store"]
    if loc:
        c.check_loc(loc)
        ids = [loc]
    all_rows = sales_shifts.shifts(db, start, end, ids or [0])
    status = status if status in ("open", "closed") else ""
    open_count = sum(1 for r in all_rows if r.closed_at is None)
    closed_count = len(all_rows) - open_count
    if status == "open":
        rows = [r for r in all_rows if r.closed_at is None]
    elif status == "closed":
        rows = [r for r in all_rows if r.closed_at is not None]
    else:
        rows = all_rows
    names = {l.id: _short_loc(l.name) for l in c.locations()}
    tot = sales_shifts.totals(rows)
    methods = sorted(tot["payments"], key=lambda m: (m != "Cash", m))
    if export == "csv":
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Opened", "Closed", "Store", "Register", "Cashier", "Transactions", "Total sales", "Opening fund"] + methods + ["Withdrawals", "Voids", "Refunds"])
        for r in rows:
            w.writerow([r.opened_at.strftime("%Y-%m-%d %H:%M"), r.closed_at.strftime("%Y-%m-%d %H:%M") if r.closed_at else "", names.get(r.location_id, ""),
                        r.register, r.cashier, r.tx_count, f"{r.total_sales:.2f}", "" if r.opening_fund is None else f"{r.opening_fund:.2f}"]
                       + [f"{(r.payments or {}).get(m, [0, 0])[1]:.2f}" for m in methods] + [f"{r.withdrawals:.2f}", f"{r.void_total:.2f}", f"{r.refund_total:.2f}"])
        audit.log(db, actor_type="user", actor_id=c.user.id, action="export_sales_by_shift", entity_type="report", entity_id="sales_by_shift",
                  after={"rows": len(rows), "from": str(start), "to": str(end), "location": loc, "status": status})
        db.commit()
        return Response("﻿" + out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="sales-by-shift-{start}-to-{end}.csv"'})
    unread = sum(1 for r in rows if not r.detail_ok)
    pay_sum = sum(tot["payments"].values())
    cash_amt = tot["payments"].get("Cash", 0.0)
    cash_pct = round(cash_amt * 100 / pay_sum) if pay_sum else 0
    payment_mix = [(m, tot["payments"][m], round(tot["payments"][m] * 100 / pay_sum, 1) if pay_sum else 0.0)
                   for m in methods if tot["payments"][m] > 0]
    shift_meta = {}
    for r in rows:
        dur = ""
        if r.closed_at and r.closed_at >= r.opened_at:
            mins = int((r.closed_at - r.opened_at).total_seconds() // 60)
            dur = f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m"
        parts = [p for p in (r.cashier or "").replace(",", " ").split() if p]
        initials = ("".join(p[0] for p in parts[:2])).upper() or "?"
        tone = sum(ord(ch) for ch in (r.cashier or "?")) % 4
        shift_meta[r.id] = {"dur": dur, "initials": initials, "tone": tone}
    return render(c, "sales_shifts.html", rows=rows, tot=tot, methods=methods, names=names, locations=[l for l in c.locations() if l.type == "store"], loc=loc,
                  days=n_days, custom=custom, frm=frm, to=to, status=status, open_count=open_count, closed_count=closed_count, total_all=len(all_rows),
                  avg_shift=round(tot["sales"] / tot["shifts"], 2) if tot["shifts"] else 0.0, cash_amt=cash_amt, cash_pct=cash_pct,
                  payment_mix=payment_mix, net_drawer=round(cash_amt - tot["withdrawals"], 2),
                  drop_count=sum(r.withdrawals_qty or (1 if r.withdrawals else 0) for r in rows),
                  store_count=len({r.location_id for r in rows}), shift_meta=shift_meta,
                  unread=unread, behind=sales_shifts.behind(db),
                  cashier_ids=sales_shifts.cashier_employee_ids(db, rows),
                  range_from=start.isoformat(), range_to=end.isoformat(), today_iso=today_local().isoformat())


def _zread_float(val: str) -> float:
    try:
        return float((val or "").replace(",", "").strip())
    except ValueError:
        return 0.0


def _zread_summary_fields(receipt: dict | None) -> dict:
    """Pull verbatim display strings from official StoreHub receipt_data without recalculating any figures."""
    if not receipt:
        return {
            "beg_si": "", "end_si": "", "sales_day": "", "net_amount": "",
            "vat_amount": "", "short_over": "", "cash_drawer": "", "opening_fund": "", "mix": [],
        }
    counters = dict(receipt.get("counters") or [])
    sales = dict(receipt.get("sales") or [])
    breakdown = dict(receipt.get("breakdown") or [])
    txn = {k.rstrip(":"): v for k, v in (receipt.get("transaction") or [])}
    colls = receipt.get("collections") or []
    parsed = [(c["method"], _zread_float(c["amount"])) for c in colls if _zread_float(c["amount"]) > 0]
    total_amt = sum(a for _, a in parsed)
    mix = []
    if total_amt > 0:
        for method, amt in parsed[:3]:
            low = method.lower()
            tone = "cash" if "cash" in low else ("ewallet" if any(k in low for k in ("gcash", "maya", "paymaya", "qr", "grab", "online", "transfer")) else "other")
            mix.append({"method": method, "pct": max(1, round(amt * 100 / total_amt)), "tone": tone})
    return {
        "beg_si": counters.get("Beg. SI #", ""),
        "end_si": counters.get("End. SI #", ""),
        "sales_day": sales.get("Sales for the Day", ""),
        "net_amount": breakdown.get("Net Amount", ""),
        "vat_amount": breakdown.get("VAT AMOUNT", ""),
        "short_over": txn.get("Short/Over", ""),
        "cash_drawer": txn.get("Cash in Drawer", ""),
        "opening_fund": txn.get("Opening Fund", ""),
        "mix": mix,
    }


def _short_si(val: str) -> str:
    s = (val or "").strip()
    if not s or s.upper() == "N/A":
        return s
    return s.lstrip("0") or "0"


@router.get("/sales/zreadings")
def zreading_reports(loc: int = 0, days: str = "14", frm: str = "", to: str = "", export: str = "", inspect: str = "", c: Ctx = Depends(need("sales.detail"))):
    """Official Z-reading reports copied from StoreHub with GET requests only; no figures are recalculated."""
    from ..models import ZReading  # noqa: F401  (register the model for metadata on older start-up paths)
    from ..services import zreadings, zreceipts
    db = c.db
    start, end, n_days, custom, frm, to = _period(days, frm, to)
    ids = [l.id for l in c.locations() if l.type == "store"]
    if loc:
        c.check_loc(loc)
        ids = [loc]
    rows = zreadings.readings(db, start, end, ids or [0])
    names = {l.id: _short_loc(l.name) for l in c.locations()}
    replaced = zreadings.interim(db, rows)
    tot = zreadings.totals(rows, set(replaced))
    if export == "csv":
        out = io.StringIO()
        w = csv.writer(out)
        extra_heads, extra = zreceipts.csv_columns([zreceipts.receipt_data(r) for r in rows])   # everything printed on the receipt, as columns
        w.writerow(["Close Time", "Z-Count", "Store", "Register", "Total Transactions", "Gross Sales (PHP)", "StoreHub Report ID", "Note", *extra_heads])
        for r, more in zip(rows, extra):
            w.writerow([r.close_at.strftime("%m/%d/%Y %H:%M"), r.z_count, names.get(r.location_id, ""), r.register, r.tx_count, f"{r.gross_sales:.2f}", r.sh_id,
                        f"Interim: included in the report closed {replaced[r.id].close_at.strftime('%m/%d/%Y %H:%M')}" if r.id in replaced else "", *more])
        audit.log(db, actor_type="user", actor_id=c.user.id, action="export_zreadings", entity_type="report", entity_id="zreadings",
                  after={"rows": len(rows), "from": str(start), "to": str(end), "location": loc})
        db.commit()
        return Response("﻿" + out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="z-readings-{start}-to-{end}.csv"'})

    by_store: dict[int, list[ZReading]] = {}
    row_meta: dict[int, dict] = {}
    previews: dict[str, dict] = {}
    selected_row = None
    selected_receipt = None
    selected_meta = None
    for r in rows:
        by_store.setdefault(r.location_id, []).append(r)
        rec = zreceipts.receipt_data(r) if r.detail_ok else None
        meta = _zread_summary_fields(rec)
        si_range = ""
        if meta["beg_si"] or meta["end_si"]:
            si_range = f"{_short_si(meta['beg_si'])} → {_short_si(meta['end_si'])}" if meta["end_si"] else _short_si(meta["beg_si"])
        meta["si_range"] = si_range
        row_meta[r.id] = {"receipt": rec, **meta}
        previews[r.sh_id] = {
            "sh_id": r.sh_id,
            "z_count": r.z_count,
            "store": names.get(r.location_id, str(r.location_id)),
            "register": r.register,
            "close_at": r.close_at.strftime("%m/%d/%Y %H:%M"),
            "tx_count": r.tx_count,
            "gross": f"{r.gross_sales:,.2f}",
            "detail_ok": bool(r.detail_ok and rec),
            "beg_si_short": _short_si(meta["beg_si"]),
            "end_si_short": _short_si(meta["end_si"]),
            "receipt": rec,
        }
        if inspect and r.sh_id == inspect:
            selected_row, selected_receipt, selected_meta = r, rec, meta
    if selected_row is None and rows:
        selected_row = next((r for r in rows if r.id not in replaced and row_meta[r.id]["receipt"]), None) \
            or next((r for r in rows if row_meta[r.id]["receipt"]), None) \
            or rows[0]
        selected_receipt = row_meta[selected_row.id]["receipt"]
        selected_meta = row_meta[selected_row.id]

    store_groups = []
    for loc_id, srows in by_store.items():
        regs = sorted({str(x.register) for x in srows if x.register})
        ptus = [previews[x.sh_id]["receipt"]["ptu"] for x in srows if previews[x.sh_id]["receipt"] and previews[x.sh_id]["receipt"].get("ptu")]
        s_tot = zreadings.totals(srows, set(replaced))
        store_groups.append({
            "location_id": loc_id,
            "name": names.get(loc_id, str(loc_id)),
            "registers": ", ".join(regs),
            "ptu": ptus[0] if ptus else "",
            "rows": srows,
            "totals": s_tot,
        })

    return render(
        c, "zreadings.html",
        rows=rows, store_groups=store_groups, row_meta=row_meta, tot=tot, replaced=replaced, names=names,
        selected_row=selected_row, selected_receipt=selected_receipt, selected_meta=selected_meta, previews=previews,
        locations=[l for l in c.locations() if l.type == "store"], loc=loc,
        days=n_days, custom=custom, frm=frm, to=to, unread=sum(1 for r in rows if not r.detail_ok),
        range_from=start.isoformat(), range_to=end.isoformat(), today_iso=today_local().isoformat(),
    )


@router.get("/sales/zreadings/receipts")
def zreading_receipt_slips(loc: int = 0, days: str = "14", frm: str = "", to: str = "", c: Ctx = Depends(need("sales.detail"))):
    """Print all fully read official reports in the selected period two per A4 sheet; local data only, no StoreHub request."""
    from ..services import zreadings, zreceipts
    db = c.db
    start, end, n_days, custom, frm, to = _period(days, frm, to)
    ids = [l.id for l in c.locations() if l.type == "store"]
    if loc:
        c.check_loc(loc)
        ids = [loc]
    rows = zreadings.readings(db, start, end, ids or [0])
    interim_ids = zreadings.interim(db, rows)
    receipts = [data for row in rows if row.id not in interim_ids and (data := zreceipts.receipt_data(row)) is not None]
    params = {"loc": loc, "days": days}
    if custom:
        params.update(frm=frm, to=to)
    return render(c, "zreading_receipt.html", receipts=receipts, bulk=True, back="/sales/zreadings?" + urlencode(params))


@router.get("/sales/zreadings/{sh_id}")
def zreading_detail(sh_id: str, c: Ctx = Depends(need("sales.detail"))):
    from ..models import ZReading
    from ..services import zreceipts
    db = c.db
    row = db.scalar(select(ZReading).where(ZReading.sh_id == sh_id))
    if row is None:
        raise HTTPException(404, "Z-reading not found")
    c.check_loc(row.location_id)
    names = {l.id: _short_loc(l.name) for l in c.locations()}
    receipt = zreceipts.receipt_data(row) if row.detail_ok else None
    meta = _zread_summary_fields(receipt)
    return render(c, "zreading_detail.html", r=row, store=names.get(row.location_id, row.location_id), receipt=receipt, meta=meta)


@router.get("/sales/zreadings/{sh_id}/receipt")
def zreading_receipt(sh_id: str, c: Ctx = Depends(need("sales.detail"))):
    from ..models import ZReading
    from ..services import zreceipts
    db = c.db
    row = db.scalar(select(ZReading).where(ZReading.sh_id == sh_id))
    if row is None:
        raise HTTPException(404, "Z-reading not found")
    c.check_loc(row.location_id)
    data = zreceipts.receipt_data(row)
    if data is None:
        raise HTTPException(409, "The official detail report has not been read yet")
    return render(c, "zreading_receipt.html", receipts=[data], back=f"/sales/zreadings/{sh_id}")


# ======================================================================================= replenishment
@router.get("/replenishment")
def replenishment_page(request: Request, loc: int = 0, status: str = "pending", supplier: int = 0, page: int = 1, search: str = "", c: Ctx = Depends(need("repl.view"))):
    if c.source_scope.startswith("loyverse"):
        return redirect("/replenishment/loyverse")
    qp = request.query_params
    if qp.get("view") != "list" and not any(k in qp for k in ("status", "loc", "supplier", "search", "page")):   # the plain page is the order sheet
        return redirect("/replenishment/sheet", **{k: qp[k] for k in ("ok", "err") if qp.get(k)})
    db = c.db
    replenishment.ensure_current(db)
    ids = c.location_ids
    q = (select(Recommendation).join(Location, Location.id == Recommendation.location_id)
         .where(Recommendation.location_id.in_(ids or [0]), Recommendation.business_date == today_local(), Location.type == "store")
         .options(selectinload(Recommendation.product), selectinload(Recommendation.location), selectinload(Recommendation.supplier)))
    if loc:
        c.check_loc(loc)
        q = q.where(Recommendation.location_id == loc)
    if status and status != "all":
        q = q.where(Recommendation.status.in_({"decided": ("approved", "modified", "rejected", "ordered")}.get(status, (status,))))
    if supplier:
        q = q.where((Recommendation.supplier_id == supplier) | Recommendation.product_id.in_(select(Product.id).where(Product.supplier_id == supplier)))
    term = search.strip()
    if term:
        like = f"%{term}%"
        q = q.where(Recommendation.product_id.in_(select(Product.id).where(Product.name.ilike(like) | Product.barcode.ilike(like) | Product.sku.ilike(like))))
    q = q.order_by(Recommendation.location_id, Recommendation.supplier_id, Recommendation.value.desc())
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    page = max(page, 1)
    recs = list(db.scalars(q.limit(100).offset((page - 1) * 100)))
    last = db.scalar(select(ReplRun).order_by(ReplRun.id.desc()).limit(1))
    counts_q = (select(Recommendation.status, func.count()).join(Location, Location.id == Recommendation.location_id)
                .where(Recommendation.business_date == today_local(), Recommendation.location_id.in_(ids or [0]), Location.type == "store"))
    if loc:
        counts_q = counts_q.where(Recommendation.location_id == loc)
    if supplier:
        counts_q = counts_q.where((Recommendation.supplier_id == supplier) | Recommendation.product_id.in_(select(Product.id).where(Product.supplier_id == supplier)))
    counts = dict(db.execute(counts_q.group_by(Recommendation.status)).all())
    sups = list(db.scalars(select(Supplier).order_by(Supplier.name)))
    return render(c, "replenishment.html", recs=recs, last=last, counts=counts, status=status, loc=loc, supplier=supplier, suppliers=sups, search=term,
                  locations=[l for l in c.locations() if l.type == "store" and not l.closed], page=page, total=total, reasons=cfg.get(db, "repl.reason_codes"), metrics=replenishment.metrics(db),
                  sales_only=cfg.get(db, "repl.mode") != "stock_and_sales", cover_days=int(cfg.get(db, "repl.sales_only_cover_days")),
                  limit=float(cfg.get(db, "repl.approval_limit_manager")), conf=replenishment.confidence_label,
                  reason_labels=replenishment.REASON_LABELS, fixes=dq.FIXES)


SHEET_PAGE = 40


@router.get("/replenishment/sheet")
def replenishment_sheet(status: str = "pending", supplier: int = 0, search: str = "", page: int = 1, loc: int = 0, todo: str = "", c: Ctx = Depends(need("repl.view"))):
    """One sheet for every store: what each store should get, what the warehouse has, and what is left to order from suppliers."""
    if c.source_scope.startswith("loyverse"):
        return redirect("/replenishment/loyverse")
    db = c.db
    replenishment.ensure_current(db)
    today = today_local()
    all_stores = sorted([l for l in c.locations() if l.type == "store" and not l.closed], key=lambda l: l.name)
    wh_loc = next((l for l in c.locations() if l.type == "warehouse" and l.source == "storehub" and l.monitored), None)
    wh_view = bool(wh_loc and loc == wh_loc.id)   # the warehouse picked: what the stores need that it must order from suppliers
    stores = [l for l in all_stores if l.id == loc] or all_stores   # one store when a store is picked
    loc = loc if wh_view or any(l.id == loc for l in all_stores) else 0
    store_ids = [l.id for l in stores] or [0]
    q = (select(Recommendation).where(Recommendation.location_id.in_(store_ids), Recommendation.business_date == today)
         .options(selectinload(Recommendation.product), selectinload(Recommendation.location), selectinload(Recommendation.supplier)))
    if status and status != "all":
        q = q.where(Recommendation.status.in_({"decided": ("approved", "modified", "rejected", "ordered")}.get(status, (status,))))
    if supplier:
        q = q.where((Recommendation.supplier_id == supplier) | Recommendation.product_id.in_(select(Product.id).where(Product.supplier_id == supplier)))
    term = search.strip()
    if term:
        like = f"%{term}%"
        q = q.where(Recommendation.product_id.in_(select(Product.id).where(Product.name.ilike(like) | Product.barcode.ilike(like) | Product.sku.ilike(like))))
    recs = list(db.scalars(q))
    sales_only = cfg.get(db, "repl.mode") != "stock_and_sales"
    can_decide, can_sheet = c.can("repl.decide"), c.can("transfer.sheet")
    wh_ids = [l.id for l in c.locations() if l.type == "warehouse" and l.source == "storehub"]
    wh_stock = {pid: float(n or 0) for pid, n in db.execute(select(InventoryCurrent.product_id, func.sum(InventoryCurrent.on_hand)).where(
        InventoryCurrent.location_id.in_(wh_ids or [0])).group_by(InventoryCurrent.product_id))} if wh_ids else {}
    rows: dict[int, dict] = {}
    for r in recs:
        decided = r.status in ("approved", "modified", "ordered")
        qty = (r.approved_qty if r.approved_qty is not None else r.final_qty) if decided else (r.saved_qty if r.saved_qty is not None else r.final_qty)
        level = ""
        if not sales_only and r.ads and r.ads > 0:
            cover = max(r.on_hand, 0) / r.ads
            level = "u" if cover < 1 else "w" if cover < 3 else ""
        editable = r.status == "pending" and can_decide and not r.batch_id and not r.transfer_sheet_id
        selectable = bool(can_decide and not r.batch_id and not r.transfer_sheet_id and ((can_sheet and r.source_kind == "transfer" and r.status in ("pending", "approved", "modified"))
                                                                                           or (not can_sheet and r.status == "pending")))
        row = rows.setdefault(r.product_id, {"p": r.product, "supplier": r.supplier or r.product.supplier, "cells": {}, "need": 0.0, "unit": r.unit_cost or 0.0, "selectable": False, "review": False})
        row["cells"][r.location_id] = {"r": r, "qty": qty, "level": level, "editable": editable, "selectable": selectable, "review": r.status == "needs_review"}
        row["need"] += qty
        row["selectable"] = row["selectable"] or selectable
        row["review"] = row["review"] or r.status == "needs_review"
    for row in rows.values():
        wh = wh_stock.get(row["p"].id) if wh_ids else None
        row["wh"] = wh
        need = row["need"]
        if wh is None or need <= wh:
            row["todo"] = ("send", need, 0.0)
        elif wh <= 0:
            row["todo"] = ("order", 0.0, need)
        else:
            row["todo"] = ("both", wh, need - wh)
        row["value"] = need * row["unit"]
    ordered = sorted(rows.values(), key=lambda x: (-x["value"], x["p"].name.lower()))
    todo = todo if todo in ("send", "order", "both") else ""
    base_for_counts = [x for x in ordered if x["todo"][0] in ("order", "both")] if wh_view else ordered
    todo_counts = {
        "all": len(base_for_counts),
        "send": 0 if wh_view else sum(1 for x in base_for_counts if x["todo"][0] == "send"),
        "order": sum(1 for x in base_for_counts if x["todo"][0] == "order"),
        "both": sum(1 for x in base_for_counts if x["todo"][0] == "both"),
    }
    if wh_view:   # what the warehouse must order from suppliers (a product it can only partly cover counts, for the part it lacks); "send" has nothing to show here
        ordered = [] if todo == "send" else [x for x in ordered if x["todo"][0] in ("order", "both")]
    elif todo:   # what to do: send from the warehouse, order from the supplier, or both
        ordered = [x for x in ordered if x["todo"][0] == todo]
    send_value = sum(x["todo"][1] * x["unit"] for x in ordered)
    order_value = sum(x["todo"][2] * x["unit"] for x in ordered)
    col_totals = {l.id: sum(x["cells"][l.id]["qty"] for x in ordered if l.id in x["cells"]) for l in stores}
    total = len(ordered)
    page = max(page, 1)
    counts_q = (select(Recommendation.status, func.count()).where(Recommendation.business_date == today, Recommendation.location_id.in_(store_ids)))
    if supplier:
        counts_q = counts_q.where((Recommendation.supplier_id == supplier) | Recommendation.product_id.in_(select(Product.id).where(Product.supplier_id == supplier)))
    counts = dict(db.execute(counts_q.group_by(Recommendation.status)).all())
    sups = list(db.scalars(select(Supplier).order_by(Supplier.name)))
    last = db.scalar(select(ReplRun).order_by(ReplRun.id.desc()).limit(1))
    return render(c, "replenishment_sheet.html", rows=ordered[(page - 1) * SHEET_PAGE: page * SHEET_PAGE], stores=stores, all_stores=all_stores, wh_loc=wh_loc, wh_view=wh_view, loc=loc, counts=counts, status=status, supplier=supplier, suppliers=sups,
                  search=term, todo=todo, todo_counts=todo_counts, page=page, total=total, last=last, has_wh=bool(wh_ids), sales_only=sales_only, cover_days=int(cfg.get(db, "repl.sales_only_cover_days")),
                  limit=float(cfg.get(db, "repl.approval_limit_manager")), kpi={"products": total, "value": sum(x["value"] for x in ordered), "send": send_value, "order": order_value},
                  col_totals=col_totals, grand=sum(x["need"] for x in ordered), short=lambda n: n.replace("The Daily Centro - ", ""), pages=SHEET_PAGE)


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
        if action == "save":
            replenishment_sheets.save_quantity(c.db, rec, c.user, qty)
        else:
            replenishment.decide(c.db, rec, c.user, action, parse_float(qty), reason or None, note or None)
    except replenishment.DecisionError as e:
        c.db.rollback()
        if action == "save" and c.request.headers.get("X-Requested-With") == "fetch":
            return JSONResponse({"error": c.t(str(e))}, status_code=400)
        return redirect(back, err=str(e))
    if action == "save":
        c.db.commit()
        if c.request.headers.get("X-Requested-With") == "fetch":
            return JSONResponse({"quantity": rec.saved_qty, "message": c.t("Saved")})
        return redirect(back, ok=c.t("Saved"))
    return redirect(back, ok=f"{rec.product.name}: {rec.status}")


@router.post("/replenishment/transfer-sheets")
def replenishment_transfer_sheets(ids: list[int] = Form(default=[]), csrf: str = Form(""),
                                 c: Ctx = Depends(need("repl.decide"))):
    c.check_csrf(csrf)
    if not c.can("transfer.sheet"):
        raise HTTPException(403, "Your role does not allow transfer sheet creation")
    try:
        sheets = replenishment_sheets.create_sheets(c.db, c.user, ids, c.location_ids)
        c.db.commit()
    except (replenishment.DecisionError, transfer_sheets.SheetError) as e:
        c.db.rollback()
        return redirect("/replenishment", err=c.t(str(e)))
    names = ", ".join(f"TS-{s.id:05d}" for s in sheets)
    target = f"/transfer-sheets/{sheets[0].id}" if len(sheets) == 1 else "/transfer-sheets"
    return redirect(target, ok=c.t("Transfer sheets created") + ": " + names)


@router.post("/replenishment/proceed")
def replenishment_proceed(ids: list[int] = Form(default=[]), csrf: str = Form(""), c: Ctx = Depends(need("repl.decide"))):
    """What the warehouse has goes on transfer sheets; what it does not have goes on a purchase order to the supplier."""
    c.check_csrf(csrf)
    try:
        out = replenishment_sheets.proceed(c.db, c.user, ids, c.location_ids, c.can("transfer.sheet"), c.can("po.create"))
        c.db.commit()
    except (replenishment.DecisionError, transfer_sheets.SheetError, po_drafts.PoError) as e:
        c.db.rollback()
        return redirect("/replenishment", err=c.t(str(e)))
    sheets, draft = out["sheets"], out["draft"]
    parts = []
    if sheets:
        parts.append(c.t("Transfer sheets created") + ": " + ", ".join(f"TS-{x.id:05d}" for x in sheets))
    if draft is not None:
        parts.append(c.t("Purchase order started") + f" ({draft.supplier.name}, {out['po_lines']} " + c.t("products") + ")")
    if draft is not None:
        return redirect("/purchase-orders/new", ok=" · ".join(parts))
    target = f"/transfer-sheets/{sheets[0].id}" if len(sheets) == 1 else "/transfer-sheets"
    return redirect(target, ok=" · ".join(parts))


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
                replenishment.decide(c.db, rec, c.user, "approve", rec.saved_qty)
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
    if not c.can("transfer.receive"):  # warehouse-to-store transfer batches are for the warehouse team and admins only
        q = q.where(OrderBatch.source_kind != "transfer")
    q = (q.order_by(OrderBatch.id.desc()).limit(60)
         .options(selectinload(OrderBatch.supplier), selectinload(OrderBatch.location), selectinload(OrderBatch.lines).selectinload(Recommendation.product)))
    return render(c, "batches.html", batches=list(db.scalars(q)))


@router.get("/replenishment/batches/{bid}.csv")
def batch_csv(bid: int, c: Ctx = Depends(need("repl.view"))):
    b = c.db.scalar(select(OrderBatch).where(OrderBatch.id == bid).options(selectinload(OrderBatch.lines).selectinload(Recommendation.product),
                                                                         selectinload(OrderBatch.supplier), selectinload(OrderBatch.location)))
    if b is None or (b.source_kind == "transfer" and not c.can("transfer.receive")):
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
def purchase_orders(view: str = "open", loc: int = 0, q: str = "", urgency: str = "all", page: int = 1, c: Ctx = Depends(need("po.view"))):
    db = c.db
    stale_days = int(cfg.get(db, "po.stale_days"))
    stmt = select(PurchaseOrder).options(selectinload(PurchaseOrder.supplier), selectinload(PurchaseOrder.location), selectinload(PurchaseOrder.lines)) \
        .order_by(PurchaseOrder.sh_po_id.desc())
    if loc:
        c.check_loc(loc)
        stmt = stmt.where(PurchaseOrder.location_id == loc)
    pos = list(db.scalars(stmt))
    now = now_utc()
    today = today_local()
    search = q.strip().lower()
    urgency_key = urgency if urgency in ("all", "stale", "due_soon", "incoming") else "all"

    def days_to(po):   # whole days from today to the expected date (negative: late)
        return (to_local(po.expected_at).date() - today).days if po.expected_at else None

    def matches_search(po):
        if not search:
            return True
        hay = f"po #{po.sh_po_id} {po.sh_po_id} {po.supplier.name if po.supplier else ''} {po.location.name if po.location else ''}".lower()
        return search in hay

    rows = []
    kinds = []
    for po in pos:
        kind = purchasing.classify_po(po, stale_days, now)
        kinds.append((po, kind))
        if view == "open" and kind not in ("incoming", "stale"):
            continue
        if view == "closed" and kind not in ("closed", "deleted"):
            continue
        d_left = days_to(po)
        if urgency_key == "stale" and kind != "stale":
            continue
        if urgency_key == "incoming" and kind != "incoming":
            continue
        if urgency_key == "due_soon" and not (kind == "incoming" and d_left is not None and d_left <= 3):
            continue
        if not matches_search(po):
            continue
        ordered_qty = sum(max(0, l.ordered_qty or 0) for l in po.lines)
        received_qty = sum(max(0, l.received_qty or 0) for l in po.lines)
        remaining = sum(max(0, l.ordered_qty - l.received_qty) for l in po.lines)
        pct = min(100, int(round(100 * received_qty / ordered_qty))) if ordered_qty > 0 else 0
        rows.append((po, kind, remaining, d_left, ordered_qty, received_qty, pct))
    total = len(rows)
    page = max(page, 1)
    page_rows = rows[(page - 1) * PAGE: page * PAGE]

    # the page is shown in groups: what needs attention first, then what is on the way, then what is closed
    groups = []
    for key, which in (("stale", ("stale",)), ("incoming", ("incoming",)), ("closed", ("closed", "deleted"))):
        items = [r for r in page_rows if r[1] in which]
        if items:
            groups.append({"key": key, "rows": items, "total_value": sum((r[0].total or 0) for r in items)})
    phone_rows = [(r[0], r[1], r[2]) for r in page_rows]
    on_way = [po for po, kind in kinds if kind == "incoming"]
    summary = {"value": sum(po.total or 0 for po in on_way), "incoming": len(on_way), "stale": sum(1 for _po, kind in kinds if kind == "stale"),
               "due_soon": sum(1 for po in on_way if (days_to(po) is not None and days_to(po) <= 3)),
               "open": sum(1 for _po, kind in kinds if kind in ("incoming", "stale")), "closed": sum(1 for _po, kind in kinds if kind in ("closed", "deleted")), "all": len(kinds)}
    return render(c, "purchase_orders.html", rows=phone_rows, view=view, loc=loc, q=q, urgency=urgency_key,
                  locations=c.locations(), page=page, total=total, stale_days=stale_days, groups=groups, summary=summary)


@router.get("/purchase-orders/{po_id}")
def po_detail(po_id: int, c: Ctx = Depends(need("po.view"))):
    po = c.db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == po_id).options(selectinload(PurchaseOrder.lines).selectinload(PoLine.product),
                                                                                   selectinload(PurchaseOrder.supplier), selectinload(PurchaseOrder.location)))
    if po is None:
        raise HTTPException(404)
    c.check_loc(po.location_id)
    kind = purchasing.classify_po(po, int(cfg.get(c.db, "po.stale_days")))
    from ..services import order_documents
    from ..models import Receiving
    local_receiving = c.db.scalar(select(Receiving).where(Receiving.po_id == po.id).order_by(Receiving.created_at.desc(), Receiving.id.desc()))
    got_any = any(l.received_present and l.received_qty for l in po.lines)
    return render(c, "po_detail.html", po=po, kind=kind, local_receiving=local_receiving, got_any=got_any, units=sum(l.ordered_qty or 0 for l in po.lines),
                  days_left=((to_local(po.expected_at).date() - today_local()).days if po.expected_at else None), document=order_documents.document(c.db, po, "po"),
                  document_base=f"/purchase-orders/{po.id}", exact=order_documents.display, six=order_documents.six_decimals,
                  calculated_po=order_documents.calculate(order_documents.initial(c.db, po, "po")))


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
