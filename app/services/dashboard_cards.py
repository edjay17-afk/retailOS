"""The Today page: the sales hero, the stat boxes, one card per store and the warehouse jobs.

Everything here is counted from data we already hold (sales days, transactions, stock levels, checklists, issues, orders); nothing is estimated.
A transaction counts the way the sales days count it: not cancelled and not a return.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (InventoryCurrent, Inspection, Issue, Product, PurchaseOrder, Recommendation, SalesDay, SalesDayProduct, SalesShift, SalesTx,
                      TransferSheet)
from ..timeutil import now_utc, to_local
from . import inspections, purchasing

OPEN_ISSUE = ("open", "acknowledged", "in_progress", "resolved")
DONE = ("completed", "late", "submitted")
ACCENTS = ("#2f5f9e", "#2563eb", "#9333ea", "#ea580c", "#0891b2", "#be185d")


def store_cards(db: Session, stores: list[dict], today, stock_health) -> list[dict]:
    """`stores` is the dashboard's list (id, name, today, yesterday, series, labels); `stock_health([ids])` gives {"health": {ok, low, out, total}}."""
    local_now = to_local(now_utc())
    ids = [s["id"] for s in stores] or [0]
    tx_today = dict(db.execute(select(SalesDay.location_id, SalesDay.tx_count).where(SalesDay.location_id.in_(ids), SalesDay.business_date == today)).all())
    total_today = sum(s["today"] for s in stores)
    top_id = max(stores, key=lambda s: s["today"])["id"] if len([s for s in stores if s["today"] > 0]) > 1 else None
    out = []
    for n, s in enumerate(stores):
        lid = s["id"]
        shift = db.scalar(select(SalesShift).where(SalesShift.location_id == lid, SalesShift.opened_at >= local_now - timedelta(days=2))
                          .order_by(SalesShift.opened_at.desc()).limit(1))
        insp = dict(db.execute(select(Inspection.status, func.count()).where(Inspection.location_id == lid, Inspection.business_date == today, inspections.active_scope())
                               .group_by(Inspection.status)).all())
        total = sum(insp.values())
        done = sum(v for k, v in insp.items() if k in DONE)
        h = stock_health([lid])["health"]
        peak = max(s["series"]) if s["series"] else 0
        out.append({
            "id": lid, "name": s["name"].replace("The Daily Centro - ", ""), "today": s["today"], "yesterday": s["yesterday"], "accent": ACCENTS[n % len(ACCENTS)],
            "has_sales": bool(any(s["series"]) or s["today"]), "top": lid == top_id and s["today"] > 0,
            "bars": [{"h": round(v / peak * 100, 1) if peak > 0 else 0, "label": s["labels"][i], "amount": v} for i, v in enumerate(s["series"])],
            "share": round(s["today"] / total_today * 100) if total_today > 0 else 0, "tx": tx_today.get(lid, 0),
            "register": shift.register if shift else "", "shift_open": bool(shift and shift.closed_at is None),
            "shift_time": (shift.opened_at if shift and shift.closed_at is None else (shift.closed_at if shift else None)),
            "health": h, "out_of_stock": h["out"],
            "health_pct": {k: (round(h[k] / h["total"] * 100) if h["total"] else 0) for k in ("ok", "low", "out")},
            "orders": db.scalar(select(func.count()).select_from(Recommendation).where(Recommendation.location_id == lid, Recommendation.business_date == today,
                                                                                      Recommendation.status == "pending")) or 0,
            "checks_total": total, "checks_left": total - done, "checks_pct": round(done / total * 100) if total else 0,
            "issues": db.scalar(select(func.count()).select_from(Issue).where(Issue.location_id == lid, Issue.status.in_(OPEN_ISSUE))) or 0,
        })
    return out


def hourly(db: Session, ids: list[int], today) -> list[dict]:
    """Transactions per hour today (local time) from the first trading hour (6am at the latest) to now."""
    counts: dict[int, int] = {}
    for (t,) in db.execute(select(SalesTx.tx_time).where(SalesTx.location_id.in_(ids or [0]), SalesTx.business_date == today, SalesTx.cancelled.is_(False),
                                                         SalesTx.tx_type != "Return")):
        counts[t.hour] = counts.get(t.hour, 0) + 1
    if not counts:
        return []
    first, last = min(min(counts), 6), max(max(counts), to_local(now_utc()).hour)
    return [{"hour": h, "label": f"{(h % 12) or 12}{'am' if h < 12 else 'pm'}", "n": counts.get(h, 0)} for h in range(first, last + 1)]


def biggest_gap(db: Session, ids: list[int], today) -> dict | None:
    """Of the products that are out of stock in these stores and sold in the last 30 days, the one that sells the most per day."""
    since = today - timedelta(days=30)
    out_ids = set(db.scalars(select(InventoryCurrent.product_id).where(InventoryCurrent.location_id.in_(ids or [0]), InventoryCurrent.on_hand <= 0)))
    if not out_ids:
        return None
    row = db.execute(select(SalesDayProduct.product_id, func.sum(SalesDayProduct.units).label("u")).where(
        SalesDayProduct.location_id.in_(ids or [0]), SalesDayProduct.product_id.in_(out_ids), SalesDayProduct.business_date > since, SalesDayProduct.units > 0)
        .group_by(SalesDayProduct.product_id).order_by(func.sum(SalesDayProduct.units).desc()).limit(1)).first()
    if not row:
        return None
    return {"name": db.get(Product, row[0]).name, "per_day": round(float(row[1]) / 30, 1)}


def best_sellers_today(db: Session, ids: list[int], today, limit: int = 4) -> list[dict]:
    rows = db.execute(select(SalesDayProduct.product_id, func.sum(SalesDayProduct.units).label("u")).where(
        SalesDayProduct.location_id.in_(ids or [0]), SalesDayProduct.business_date == today, SalesDayProduct.units > 0)
        .group_by(SalesDayProduct.product_id).order_by(func.sum(SalesDayProduct.units).desc()).limit(limit)).all()
    top = float(rows[0][1]) if rows else 0
    return [{"name": db.get(Product, pid).name, "units": float(u), "pct": round(float(u) / top * 100) if top else 0} for pid, u in rows]


def warehouse_jobs(db: Session, can, recs: tuple) -> list[dict]:
    """The warehouse's waiting jobs, only for what this user may open."""
    jobs = []
    if can("po.view"):
        pos = list(db.scalars(select(PurchaseOrder).where(PurchaseOrder.status.in_(purchasing.OPEN_STATUSES), PurchaseOrder.missing_from_api.is_(False))
                              .order_by(PurchaseOrder.created_sh)))
        if pos:
            oldest = pos[0]
            days = (now_utc() - oldest.created_sh).days if oldest.created_sh else None
            jobs.append({"n": len(pos), "key": "po", "href": "/purchase-orders", "oldest_no": oldest.sh_po_id, "oldest_days": days,
                         "supplier": oldest.supplier.name if oldest.supplier else ""})
    if can("transfer.sheet"):
        drafts = db.scalar(select(func.count()).select_from(TransferSheet).where(TransferSheet.status == "draft")) or 0
        waiting = db.scalar(select(func.count()).select_from(TransferSheet).where(TransferSheet.status == "submitted")) or 0
        if drafts + waiting:
            jobs.append({"n": drafts + waiting, "key": "sheets", "href": "/transfer-sheets", "drafts": drafts, "waiting": waiting})
    if can("repl.view") and recs and recs[0]:
        jobs.append({"n": recs[0], "key": "recs", "href": "/replenishment", "value": recs[1]})
    return jobs


def overview(db: Session, cards: list[dict], stores: list[dict], today, wjobs: list[dict], open_issues: int, can) -> dict:
    """The numbers of the stat boxes: transactions, out of stock, and the jobs waiting."""
    ids = [s["id"] for s in stores]
    tx = sum(c["tx"] for c in cards)
    total_today = sum(s["today"] for s in stores)
    hrs = hourly(db, ids, today)
    peak = max((h["n"] for h in hrs), default=0)
    for h in hrs:
        h["pct"] = round(h["n"] / peak * 100) if peak else 0
        h["peak"] = bool(peak and h["n"] == peak)
    out_total = sum(c["out_of_stock"] for c in cards)
    most = max((c["out_of_stock"] for c in cards), default=0)
    checks_total, checks_left = sum(c["checks_total"] for c in cards), sum(c["checks_left"] for c in cards)
    jobs = [{"key": j["key"], "n": j["n"]} for j in wjobs]
    if can("issue.view"):
        jobs.append({"key": "issues", "n": open_issues})
    return {
        "tx": tx, "avg_basket": round(total_today / tx, 2) if tx else 0, "hourly": hrs,
        "out_total": out_total, "out_by_store": [{"name": c["name"], "n": c["out_of_stock"], "pct": round(c["out_of_stock"] / most * 100) if most else 0} for c in cards],
        "gap": biggest_gap(db, ids, today),
        "jobs": jobs, "jobs_total": sum(j["n"] for j in jobs),
        "checks_total": checks_total, "checks_done": checks_total - checks_left, "checks_pct": round((checks_total - checks_left) / checks_total * 100) if checks_total else 0,
        "best": best_sellers_today(db, ids, today),
    }
