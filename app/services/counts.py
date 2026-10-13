"""Targeted cycle counts and the StoreHub stock-take export. StoreHub stock can only be changed in its back office,
so an approved count produces a file for the stock take import, and the next sync verifies the new quantity."""
from __future__ import annotations

import csv
import io
import random
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import audit
from .. import settings_service as cfg
from ..models import (CycleCount, CycleCountLine, InventoryCurrent, Location, Product, SalesDayProduct, User)
from ..timeutil import now_utc, today_local
from . import inventory_events as ev, issues


class CountError(Exception):
    pass


def suggest(db: Session, loc: Location, n: int = 30) -> list[tuple[Product, float, str]]:
    """Pick what to count: negative stock first, then high value, high sales, repeat mismatches, then a random sample."""
    inv = {r.product_id: r for r in db.scalars(select(InventoryCurrent).where(InventoryCurrent.location_id == loc.id))}
    prods = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(list(inv) or [0]), Product.in_api.is_(True)))}
    picked: dict[int, str] = {}

    def add(pids, reason, limit):
        c = 0
        for pid in pids:
            if pid in picked or pid not in prods:
                continue
            picked[pid] = reason
            c += 1
            if c >= limit or len(picked) >= n:
                break

    neg = sorted([pid for pid, r in inv.items() if r.on_hand < 0], key=lambda p: inv[p].on_hand)
    add(neg, "negative_stock", max(n // 2, 5))
    high_value = sorted([pid for pid in inv if prods.get(pid) and prods[pid].cost and inv[pid].on_hand > 0],
                        key=lambda p: -(inv[p].on_hand * (prods[p].cost or 0)))
    add(high_value, "high_value", n // 5 + 1)
    since = today_local() - timedelta(days=14)
    top_sales = [pid for pid, in db.execute(select(SalesDayProduct.product_id).where(
        SalesDayProduct.location_id == loc.id, SalesDayProduct.business_date >= since).group_by(SalesDayProduct.product_id)
        .order_by(func.sum(SalesDayProduct.units).desc()).limit(60))]
    add(top_sales, "high_sales", n // 5 + 1)
    repeat = [pid for pid, in db.execute(select(CycleCountLine.product_id).join(CycleCount, CycleCount.id == CycleCountLine.count_id).where(
        CycleCount.location_id == loc.id, CycleCountLine.variance.is_not(None), func.abs(CycleCountLine.variance) > 0,
        CycleCount.created_at >= now_utc() - timedelta(days=30)).group_by(CycleCountLine.product_id))]
    add(repeat, "repeat_mismatch", n // 5 + 1)
    pool = [pid for pid in inv if pid not in picked and pid in prods and inv[pid].on_hand > 0]
    random.Random(today_local().toordinal() + loc.id).shuffle(pool)
    add(pool, "random", n)
    return [(prods[pid], inv[pid].on_hand, reason) for pid, reason in picked.items()]


def create(db: Session, user: User, loc: Location, n: int = 30) -> CycleCount:
    existing = db.scalar(select(CycleCount).where(CycleCount.location_id == loc.id, CycleCount.business_date == today_local(), CycleCount.status == "open"))
    if existing:
        return existing
    cc = CycleCount(location_id=loc.id, business_date=today_local(), created_by=user.id)
    for prod, qty, reason in suggest(db, loc, n):
        cc.lines.append(CycleCountLine(product_id=prod.id, reason=reason, system_qty=qty))
    db.add(cc)
    db.flush()
    audit.log(db, actor_type="human", actor_id=user.id, action="count.create", entity_type="cycle_count", entity_id=cc.id, after={"lines": len(cc.lines)})
    return cc


def create_targeted(db: Session, user: User, loc: Location, product_ids: list[int]) -> CycleCount:
    """A count for specific products (e.g. straight from a data-quality finding). Merges into today's open count
    at the location instead of opening a second one; products already in it are skipped."""
    inv = {r.product_id: r.on_hand for r in db.scalars(select(InventoryCurrent).where(InventoryCurrent.location_id == loc.id))}
    cc = db.scalar(select(CycleCount).where(CycleCount.location_id == loc.id, CycleCount.business_date == today_local(), CycleCount.status == "open"))
    if cc is None:
        cc = CycleCount(location_id=loc.id, business_date=today_local(), created_by=user.id)
        db.add(cc)
        db.flush()
    have = {l.product_id for l in cc.lines}
    added = 0
    for pid in dict.fromkeys(product_ids):  # keep the order, drop duplicates
        if pid in have:
            continue
        qty = inv.get(pid, 0.0)
        cc.lines.append(CycleCountLine(product_id=pid, reason="negative_stock" if qty < 0 else "targeted", system_qty=qty))
        added += 1
    db.flush()
    audit.log(db, actor_type="human", actor_id=user.id, action="count.create", entity_type="cycle_count", entity_id=cc.id,
              after={"lines": added, "targeted": [p for p in dict.fromkeys(product_ids)]})
    return cc


def enter(db: Session, cc: CycleCount, user: User, rows: list[dict]) -> None:
    if cc.status != "open":
        raise CountError("This count is already submitted")
    cur = {r.product_id: r.on_hand for r in db.scalars(select(InventoryCurrent).where(InventoryCurrent.location_id == cc.location_id))}
    by_id = {l.id: l for l in cc.lines}
    for r in rows:
        l = by_id.get(r["line_id"])
        if l is None or r.get("counted") is None:
            continue
        if r["counted"] < 0:
            raise CountError("A count cannot be negative")
        l.system_qty = cur.get(l.product_id, l.system_qty)  # compare against the freshest stock figure
        l.counted_qty = r["counted"]
        l.variance = round(r["counted"] - l.system_qty, 3)
        l.note = (r.get("note") or None)


def submit(db: Session, cc: CycleCount, user: User) -> CycleCount:
    if cc.status != "open":
        raise CountError("Already submitted")
    if not any(l.counted_qty is not None for l in cc.lines):
        raise CountError("Count at least one item")
    unexplained = [l for l in cc.lines if l.counted_qty is not None and l.variance and abs(l.variance) > 1e-9 and not (l.note or "").strip()]
    if unexplained:  # V2.1 MUST 3: an adjustment is only proposed with a reason
        raise CountError(f"{len(unexplained)} counted item(s) differ from the system. Write the reason in the note for each of them.")
    cc.status = "submitted"
    audit.log(db, actor_type="human", actor_id=user.id, action="count.submit", entity_type="cycle_count", entity_id=cc.id)
    return cc


submit_count = submit


def approve(db: Session, cc: CycleCount, user: User) -> CycleCount:
    if cc.status != "submitted":
        raise CountError("Only a submitted count can be approved")
    diffs = [l for l in cc.lines if l.variance and abs(l.variance) > 1e-9]
    cc.status, cc.approved_by, cc.approved_at = "approved", user.id, now_utc()
    _record_events(db, cc, user)
    if diffs:
        lines = [f"{l.product.name}: system {l.system_qty:g}, counted {l.counted_qty:g} ({l.variance:+g})" for l in diffs[:15]]
        issues.create_issue(db, type="stock", severity="P2", location_id=cc.location_id, source="count", source_ref=f"count:{cc.id}", created_by=user.id,
                            title=f"Cycle count {cc.business_date}: {len(diffs)} difference(s) at {cc.location.name}",
                            description="; ".join(lines) + (f" (+{len(diffs) - 15} more)" if len(diffs) > 15 else "") +
                            ". Enter the adjustment in StoreHub with the stock take import (download the file from the count page).",
                            dedupe_key=f"count:{cc.id}")
    audit.log(db, actor_type="human", actor_id=user.id, action="count.approve", entity_type="cycle_count", entity_id=cc.id, after={"differences": len(diffs)})
    return cc


def delete(db: Session, cc: CycleCount, user: User) -> None:
    """Remove a count and its lines. The audit trail keeps a record of what was deleted."""
    audit.log(db, actor_type="human", actor_id=user.id, action="count.delete", entity_type="cycle_count", entity_id=cc.id,
              before={"status": cc.status, "date": str(cc.business_date), "location_id": cc.location_id, "lines": len(cc.lines)})
    db.delete(cc)


COUNT_TEMPLATE_CSV = "Product,Counted,Note,Location\n4800016644801,12,,\n"
_COUNT_FIELDS = {
    "product": ("product", "barcode", "sku", "item", "name", "product name", "item name", "description"),
    "counted": ("counted", "counted qty", "counted quantity", "count", "qty", "quantity", "actual", "physical", "physical count"),
    "note": ("note", "notes", "remark", "remarks", "comment"),
    "location": ("location", "store", "branch", "outlet"),
}


def import_counts(db: Session, user: User, filename: str, data: bytes, locations: list[Location], default_location_id: int | None, submit: bool = False) -> dict:
    """Record counted quantities from a CSV or Excel file. They go into today's open count at each store (or open one), where the differences against
    StoreHub stock are worked out and can be reviewed before the count is submitted. A product named twice keeps its last quantity."""
    from . import tabular
    table = tabular.clean_rows(tabular.read_table(filename, data))
    if not table:
        raise ValueError("The file is empty")
    col = tabular.map_columns(table[0], _COUNT_FIELDS, ("product", "counted"))
    rows = tabular.body(table)
    default = next((l for l in locations if l.id == default_location_id), None)
    per_loc: dict[int, dict[int, tuple[float, str | None]]] = {}
    errors: list[tuple[int, str]] = []
    for n, row in enumerate(rows, start=2):
        loc = default
        if str(tabular.cell(row, col, "location") or "").strip():
            loc = tabular.match_location(tabular.cell(row, col, "location"), locations)
            if loc is None:
                errors.append((n, f"location '{tabular.cell(row, col, 'location')}' not found or not allowed"))
                continue
        elif loc is None:
            errors.append((n, "no location chosen"))
            continue
        key = str(tabular.cell(row, col, "product") if tabular.cell(row, col, "product") is not None else "").strip()
        prod, why = tabular.find_product(db, key) if key else (None, "product is empty")
        qty = tabular.to_qty(tabular.cell(row, col, "counted"))
        if not why and (qty is None or qty < 0):
            why = "counted quantity must be a number, zero or more"
        if why:
            errors.append((n, why))
            continue
        note = str(tabular.cell(row, col, "note") or "").strip()[:300] or None
        per_loc.setdefault(loc.id, {})[prod.id] = (qty, note)
    by_id = {l.id: l for l in locations}
    counted = created = 0
    submitted: list[int] = []
    for lid, items in per_loc.items():
        loc = by_id[lid]
        inv = {r.product_id: r.on_hand for r in db.scalars(select(InventoryCurrent).where(InventoryCurrent.location_id == lid))}
        cc = db.scalar(select(CycleCount).where(CycleCount.location_id == lid, CycleCount.business_date == today_local(), CycleCount.status == "open"))
        if cc is None:
            cc = CycleCount(location_id=lid, business_date=today_local(), created_by=user.id)
            db.add(cc)
            db.flush()
            created += 1
        have = {l.product_id: l for l in cc.lines}
        for pid in items:
            if pid not in have:
                line = CycleCountLine(product_id=pid, reason="uploaded", system_qty=inv.get(pid, 0.0))
                cc.lines.append(line)
        db.flush()
        have = {l.product_id: l for l in cc.lines}
        enter(db, cc, user, [{"line_id": have[pid].id, "counted": qty, "note": note} for pid, (qty, note) in items.items()])
        counted += len(items)
        audit.log(db, actor_type="human", actor_id=user.id, action="count.import", entity_type="cycle_count", entity_id=cc.id, after={"file": filename, "lines": len(items)})
        if submit:
            try:
                submit_count(db, cc, user)
                submitted.append(cc.id)
            except CountError as e:
                errors.append((0, f"{loc.name}: {e} The count is saved but not submitted."))
    return {"lines": counted, "counts": len(per_loc), "created": created, "submitted": submitted, "errors": errors}


def stock_take_csv(cc: CycleCount) -> str:
    """File for StoreHub's Stock Take import. Columns follow the StoreHub export (product, SKU, category, quantity); each row
    carries the StoreHub product id because the import needs it. Check the headers against the template you download."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Product ID", "Product Name", "SKU", "Barcode", "Category", "System Quantity", "Counted Quantity"])
    for l in cc.lines:
        if l.counted_qty is None:
            continue
        p = l.product
        w.writerow([p.storehub_id, p.name, p.sku or "", p.barcode or "", p.category or "", f"{l.system_qty:g}", f"{l.counted_qty:g}"])
    return out.getvalue()


# ======================================================================================= V2.1 MUST 3: events, reconciliation, repeat variance
def _record_events(db: Session, cc: CycleCount, user: User) -> None:
    """Each counted line leaves a cycle_count event. A difference stays `pending` (an adjustment is only proposed) until StoreHub shows the change;
    a count that agrees with the system is settled at once. Several differences on the same product and place raise a root-cause issue."""
    threshold = int(cfg.get(db, "counts.repeat_variance_count"))
    for l in cc.lines:
        if l.counted_qty is None:
            continue
        var = l.variance or 0.0
        has_var = abs(var) > 1e-9
        ev.record(db, uid=f"cnt:{l.id}", event_type="cycle_count", user=user, product_id=l.product_id, location_id=cc.location_id, quantity=abs(var),
                  direction="+" if var >= 0 else "-", occurred_at=cc.approved_at, reason_code=l.reason, reason_text=l.note,
                  status="pending" if has_var else "matched")
        if has_var and threshold > 0:
            since = now_utc() - timedelta(days=90)
            n = db.scalar(select(func.count()).select_from(ev.InventoryEvent).where(
                ev.InventoryEvent.event_type == "cycle_count", ev.InventoryEvent.quantity > 0, ev.InventoryEvent.product_id == l.product_id,
                ev.InventoryEvent.location_id == cc.location_id, ev.InventoryEvent.recorded_at >= since)) or 0
            if n >= threshold:
                issues.create_issue(db, type="stock", severity="P2", location_id=cc.location_id, product_id=l.product_id, source="count",
                                    source_ref=f"count:{cc.id}", created_by=user.id,
                                    title=f"Repeated count differences: {l.product.name if l.product else l.product_id} at {cc.location.name}",
                                    description=f"{n} cycle counts in the last 90 days found a difference for this product. Find the cause (receiving, picking, "
                                                "theft, wrong barcode) instead of adjusting again.",
                                    dedupe_key=f"repeatvar:{cc.location_id}:{l.product_id}:{today_local().isocalendar()[1]}")


def reconcile_events(db: Session) -> dict:
    """A count difference is reconciled when a later StoreHub stock change shows the counted quantity (or the proposed correction).
    Still unsupported after the grace period, it is flagged for review once."""
    from ..models import InventoryChange
    grace = timedelta(days=int(cfg.get(db, "counts.reconcile_grace_days")))
    matched = review = 0
    q = select(ev.InventoryEvent).where(ev.InventoryEvent.event_type == "cycle_count", ev.InventoryEvent.reconciliation_status == "pending",
                                        ev.InventoryEvent.recorded_at >= now_utc() - timedelta(days=60))
    for e in list(db.scalars(q)):
        line = db.get(CycleCountLine, int(e.event_uid.split(":")[1]))
        if line is None:
            continue
        signed = e.quantity if e.direction == "+" else -e.quantity
        changes = db.scalars(select(InventoryChange).where(InventoryChange.location_id == e.location_id, InventoryChange.product_id == e.product_id,
                                                           InventoryChange.observed_at >= e.recorded_at))
        if any(abs(c.new_qty - (line.counted_qty or 0)) < 1e-6 or abs(c.delta - signed) < 1e-6 for c in changes):
            ev.resolve(db, e, "matched", "StoreHub stock changed to the counted quantity")
            matched += 1
        elif now_utc() - e.recorded_at > grace:
            ev.resolve(db, e, "needs_review", f"StoreHub still has not reflected the count after {grace.days} days")
            issues.create_issue(db, type="stock", severity="P3", location_id=e.location_id, product_id=e.product_id, source="count", source_ref=f"count_event:{e.id}",
                                title="Count adjustment is not in StoreHub yet",
                                description=f"A count found a difference of {signed:+g} but StoreHub stock has not changed. Enter the stock take in StoreHub.",
                                dedupe_key=f"cntpend:{e.id}")
            review += 1
    return {"matched": matched, "needs_review": review}
