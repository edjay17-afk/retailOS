"""Data-quality layer. Detects impossible, incomplete or suspicious data before it feeds replenishment or reports.

Every finding is a structured DqEvent (never only a log line). Events resolve themselves when the condition clears.
Critical findings also open an issue so a person is responsible for them.
"""
from __future__ import annotations

import statistics
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import settings_service as cfg
from ..models import (DqEvent, InventoryChange, InventoryCurrent, Location, PoLine, Product, PurchaseOrder, SalesDay,
                      SalesDayProduct, SalesLine, SalesTx)
from ..timeutil import now_utc, today_local
from . import freshness as fresh
from . import issues, purchasing

RULES = ["negative_stock", "stale_sync", "no_supplier", "no_cost", "no_barcode", "duplicate_barcode", "stale_open_po",
         "po_zero_qty", "sales_spike", "sales_without_stock_change", "po_received_no_stock_change", "unknown_product_in_sale"]

RULE_LABELS = {
    "negative_stock": "Negative stock", "stale_sync": "Stale data", "no_supplier": "Product has no supplier",
    "no_cost": "Product has no cost", "no_barcode": "Product has no barcode", "duplicate_barcode": "Duplicate barcode",
    "stale_open_po": "Open PO far past its date", "po_zero_qty": "PO line with no quantity", "sales_spike": "Sales spike",
    "sales_without_stock_change": "Sales but stock did not move", "po_received_no_stock_change": "PO received but stock did not change",
    "unknown_product_in_sale": "Sale of unknown product", "sync_failure": "Sync failing",
    "reconciliation_mismatch": "Reconciliation mismatch", "missing_store_mapping": "Unmapped store",
}

# A blocked suggestion or finding must name its missing data and link to the screen where a person fixes it.
# Covers both DqEvent rules and replenishment needs_review reason codes.
FIXES: dict[str, tuple[str, str]] = {
    "negative_stock": ("/counts", "Count it now"),
    "no_supplier": ("/products", "Set a supplier"),
    "no_cost": ("/products", "Fix the product"),
    "no_barcode": ("/products", "Fix the product"),
    "duplicate_barcode": ("/products", "Fix the product"),
    "no_case_pack": ("/products", "Set the case pack"),
    "conflicting_po": ("/purchase-orders", "Resolve the PO"),
    "stale_open_po": ("/purchase-orders", "Resolve the PO"),
    "po_zero_qty": ("/purchase-orders", "Resolve the PO"),
    "stale_sync": ("/sync", "Check the sync"),
    "sync_failure": ("/sync", "Check the sync"),
    "data_quality": ("/data-quality", "See the findings"),
    # short_history has no fix link: only time and sales history resolve it
}


def fix_for(rule: str) -> tuple[str, str] | None:
    """(url, label) of the screen that fixes this rule or reason code, or None."""
    return FIXES.get(rule)

Desired = dict[str, dict[str, Any]]


def _apply(db: Session, rule: str, desired: Desired) -> tuple[int, int]:
    existing = {e.dedupe_key: e for e in db.scalars(select(DqEvent).where(DqEvent.rule == rule))}
    now = now_utc()
    opened = resolved = 0
    for key, d in desired.items():
        e = existing.get(key)
        if e is None:
            db.add(DqEvent(rule=rule, dedupe_key=key, **d))
            opened += 1
        else:
            e.last_seen = now
            e.message = d["message"]
            e.detail = d.get("detail")
            e.severity = d["severity"]
            if e.resolved_at is not None:
                e.resolved_at = None
                opened += 1
    for key, e in existing.items():
        if key not in desired and e.resolved_at is None:
            e.resolved_at = now
            resolved += 1
    return opened, resolved


def _ev(sev: str, etype: str, eid: Any, msg: str, loc: int | None = None, prod: int | None = None, detail: dict | None = None) -> dict:
    return dict(severity=sev, entity_type=etype, entity_id=None if eid is None else str(eid), location_id=loc, product_id=prod,
                message=msg, detail=detail)


# ---------------------------------------------------------------------------------------------- rules
def r_negative_stock(db: Session) -> Desired:
    q = (select(InventoryCurrent.location_id, InventoryCurrent.product_id, InventoryCurrent.on_hand, Product.name, Location.name)
         .join(Product, Product.id == InventoryCurrent.product_id).join(Location, Location.id == InventoryCurrent.location_id)
         .where(InventoryCurrent.on_hand < 0, Location.monitored.is_(True)))
    return {f"neg:{l}:{p}": _ev("warning", "product", p, f"{pn} at {ln}: on hand is {qty:g}", l, p, {"on_hand": qty})
            for l, p, qty, pn, ln in db.execute(q)}


def r_stale_sync(db: Session) -> Desired:
    out: Desired = {}
    for f in fresh.freshness(db):
        if f["status"] == "fresh":
            continue
        age = "never synced" if f["age_min"] is None else f"last success {f['age_min']:.0f} min ago (limit {f['limit_min']})"
        out[f"stale:{f['domain']}"] = _ev("critical" if f["critical"] else "warning", "sync", f["domain"],
                                         f"{f['label']} data is stale: {age}", detail={"domain": f["domain"]})
    return out


def r_no_supplier(db: Session) -> Desired:
    since = today_local() - timedelta(days=30)
    sold = select(SalesDayProduct.product_id).where(SalesDayProduct.business_date >= since).distinct()
    q = select(Product.id, Product.name).where(Product.in_api.is_(True), Product.track_stock.is_(True), Product.supplier_id.is_(None),
                                               Product.id.in_(sold))
    return {f"nosup:{i}": _ev("warning", "product", i, f"{n}: sells but has no supplier, so it cannot be ordered", prod=i) for i, n in db.execute(q)}


def r_no_cost(db: Session) -> Desired:
    q = select(Product.id, Product.name).where(Product.in_api.is_(True), Product.track_stock.is_(True),
                                               (Product.cost.is_(None)) | (Product.cost <= 0))
    return {f"nocost:{i}": _ev("warning", "product", i, f"{n}: no cost set in StoreHub, margin and order value are unreliable", prod=i)
            for i, n in db.execute(q)}


def r_no_barcode(db: Session) -> Desired:
    q = select(Product.id, Product.name).where(Product.in_api.is_(True), Product.track_stock.is_(True),
                                               (Product.barcode.is_(None)) | (Product.barcode == ""))
    return {f"nobc:{i}": _ev("info", "product", i, f"{n}: no barcode", prod=i) for i, n in db.execute(q)}


def r_duplicate_barcode(db: Session) -> Desired:
    dup = select(Product.barcode).where(Product.in_api.is_(True), Product.barcode.is_not(None), Product.barcode != "") \
        .group_by(Product.barcode).having(func.count() > 1)
    out: Desired = {}
    for bc in db.scalars(dup):
        names = [n for n in db.scalars(select(Product.name).where(Product.barcode == bc, Product.in_api.is_(True)))]
        out[f"dupbc:{bc}"] = _ev("info", "product", bc, f"Barcode {bc} is used by {len(names)} products: {', '.join(names[:3])}")
    return out


def r_stale_open_po(db: Session) -> Desired:
    _, stale = purchasing.incoming_map(db)
    days = int(cfg.get(db, "po.stale_days"))
    out: Desired = {}
    for po in stale:
        ref = purchasing.po_reference_date(po)
        late = (now_utc() - ref).days if ref else None
        out[f"stalepo:{po.sh_po_id}"] = _ev("warning", "po", po.sh_po_id,
                                           f"PO #{po.sh_po_id} is still {po.status} and {late if late is not None else '?'} days past its date; "
                                           f"it is not counted as incoming (limit {days} days). Receive it or cancel it in StoreHub.",
                                           po.location_id, None, {"status": po.status, "days_late": late})
    return out


def r_po_zero_qty(db: Session) -> Desired:
    q = (select(PurchaseOrder.sh_po_id, func.count(PoLine.id)).join(PoLine, PoLine.po_id == PurchaseOrder.id)
         .where(PurchaseOrder.status.in_(purchasing.OPEN_STATUSES), PurchaseOrder.missing_from_api.is_(False), PoLine.ordered_qty <= 0)
         .group_by(PurchaseOrder.sh_po_id))
    return {f"pozero:{po}": _ev("warning", "po", po, f"Open PO #{po} has {n} line(s) with no ordered quantity") for po, n in db.execute(q)}


def r_sales_spike(db: Session) -> Desired:
    sigma = float(cfg.get(db, "dq.sales_spike_sigma"))
    floor = float(cfg.get(db, "dq.sales_spike_min"))
    today = today_local()
    out: Desired = {}
    for loc in db.scalars(select(Location).where(Location.monitored.is_(True))):
        rows = {r.business_date: r.net_sales for r in db.scalars(select(SalesDay).where(
            SalesDay.location_id == loc.id, SalesDay.business_date >= today - timedelta(days=21)))}
        for back in range(0, 3):
            d = today - timedelta(days=back)
            hist = [v for dd, v in rows.items() if d - timedelta(days=14) <= dd < d]
            if d not in rows or len(hist) < 7:
                continue
            mean, sd = statistics.mean(hist), statistics.pstdev(hist)
            if rows[d] > mean + sigma * max(sd, 1.0) and rows[d] >= floor:
                out[f"spike:{loc.id}:{d}"] = _ev("warning", "location", loc.id,
                                                f"Sales at {loc.name} on {d} were {rows[d]:,.2f}, far above the recent average of {mean:,.2f}",
                                                loc.id, None, {"date": str(d), "value": rows[d], "mean": mean, "sd": sd})
    return out


def _history_hours(db: Session) -> float:
    first = db.scalar(select(func.min(InventoryChange.observed_at)))
    from ..models import SyncRun
    run = db.scalar(select(func.min(SyncRun.started_at)).where(SyncRun.job == "inventory"))
    start = min([t for t in (first, run) if t])  if (first or run) else None
    return (now_utc() - start).total_seconds() / 3600 if start else 0.0


def r_sales_without_stock_change(db: Session) -> Desired:
    """Items sold in the last 24h whose stock never moved: StoreHub may not be tracking them, or stock is not decrementing."""
    if _history_hours(db) < 26:
        return {}
    since = now_utc() - timedelta(hours=24)
    sold = (select(SalesTx.location_id, SalesLine.product_id, func.sum(SalesLine.qty))
            .join(SalesLine, SalesLine.tx_id == SalesTx.id)
            .where(SalesTx.tx_time >= since, SalesTx.cancelled.is_(False), SalesLine.item_type == "Item", SalesLine.product_id.is_not(None))
            .group_by(SalesTx.location_id, SalesLine.product_id))
    moved = {(l, p) for l, p in db.execute(select(InventoryChange.location_id, InventoryChange.product_id)
                                           .where(InventoryChange.observed_at >= since).distinct())}
    tracked = {pid for pid in db.scalars(select(Product.id).where(Product.track_stock.is_(True)))}
    out: Desired = {}
    names = {p.id: p.name for p in db.scalars(select(Product).where(Product.id.in_([p for _, p, _ in db.execute(sold)] or [0])))}
    locs = {l.id: l.name for l in db.scalars(select(Location))}
    for l, p, q in db.execute(sold):
        if p in tracked and (l, p) not in moved and q > 0:
            out[f"nomove:{l}:{p}"] = _ev("warning", "product", p, f"{names.get(p, p)} at {locs.get(l)}: {q:g} sold in 24h but stock did not change",
                                        l, p, {"sold": q})
    return out


def r_po_received_no_stock_change(db: Session) -> Desired:
    hist_start = db.scalar(select(func.min(InventoryChange.observed_at)))
    from ..models import SyncRun
    first_run = db.scalar(select(func.min(SyncRun.started_at)).where(SyncRun.job == "inventory"))
    if first_run is None:
        return {}
    since = now_utc() - timedelta(days=3)
    out: Desired = {}
    q = select(PurchaseOrder).where(PurchaseOrder.status == "completed", PurchaseOrder.missing_from_api.is_(False),
                                    PurchaseOrder.modified_sh >= since, PurchaseOrder.modified_sh <= now_utc() - timedelta(hours=24),
                                    PurchaseOrder.modified_sh >= first_run, PurchaseOrder.location_id.is_not(None)).options(selectinload(PurchaseOrder.lines))
    for po in db.scalars(q):
        for ln in po.lines:
            if ln.product_id is None or ln.received_qty <= 0:
                continue
            up = db.scalar(select(func.count()).select_from(InventoryChange).where(
                InventoryChange.location_id == po.location_id, InventoryChange.product_id == ln.product_id,
                InventoryChange.delta > 0, InventoryChange.observed_at >= po.created_sh))
            if not up:
                out[f"norecv:{po.sh_po_id}:{ln.product_id}"] = _ev(
                    "warning", "po", po.sh_po_id, f"PO #{po.sh_po_id} shows {ln.received_qty:g} received but stock never increased", po.location_id, ln.product_id)
    return out


def r_unknown_product_in_sale(db: Session) -> Desired:
    since = today_local() - timedelta(days=7)
    q = (select(SalesLine.product_sh_id, func.count()).join(SalesTx, SalesTx.id == SalesLine.tx_id)
         .where(SalesLine.product_id.is_(None), SalesLine.item_type == "Item", SalesTx.business_date >= since).group_by(SalesLine.product_sh_id))
    return {f"unkprod:{sid}": _ev("warning", "product", sid, f"{n} sale line(s) in the last 7 days refer to a product not in the catalog ({sid})")
            for sid, n in db.execute(q)}


RULE_FUNCS = {
    "negative_stock": r_negative_stock, "stale_sync": r_stale_sync, "no_supplier": r_no_supplier, "no_cost": r_no_cost,
    "no_barcode": r_no_barcode, "duplicate_barcode": r_duplicate_barcode, "stale_open_po": r_stale_open_po, "po_zero_qty": r_po_zero_qty,
    "sales_spike": r_sales_spike, "sales_without_stock_change": r_sales_without_stock_change,
    "po_received_no_stock_change": r_po_received_no_stock_change, "unknown_product_in_sale": r_unknown_product_in_sale,
}


def run_all(db: Session) -> dict[str, dict[str, int]]:
    """Run every rule, store events, auto-resolve cleared ones, and raise issues for critical findings."""
    summary: dict[str, dict[str, int]] = {}
    for rule, fn in RULE_FUNCS.items():
        desired = fn(db)
        opened, resolved = _apply(db, rule, desired)
        summary[rule] = {"open_now": len(desired), "opened": opened, "resolved": resolved}
    db.flush()
    _raise_issues(db)
    return summary


def _raise_issues(db: Session) -> None:
    for ev in db.scalars(select(DqEvent).where(DqEvent.severity == "critical", DqEvent.resolved_at.is_(None), DqEvent.issue_id.is_(None))):
        issue = issues.create_issue(db, type="data_quality" if ev.rule != "sync_failure" else "system", severity="P1",
                                    title=ev.message[:200], description=ev.message, location_id=ev.location_id, source="system",
                                    source_ref=f"dq:{ev.id}", dedupe_key=f"dq:{ev.dedupe_key}")
        ev.issue_id = issue.id


def open_counts(db: Session) -> dict[str, int]:
    rows = db.execute(select(DqEvent.rule, func.count()).where(DqEvent.resolved_at.is_(None)).group_by(DqEvent.rule)).all()
    return {r: n for r, n in rows}
