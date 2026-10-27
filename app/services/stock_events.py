"""Stock event capture: write-offs and unplanned receipts. Every physical change to stock leaves a trace here
(who, why, how much, photo). The change itself is keyed into the StoreHub back office by a person — the issue is
the to-do — and the next inventory sync verifies that the matching stock move was actually made."""
from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..models import InventoryChange, Issue, IssueEvent, Location, Product, StockEvent, User
from ..timeutil import now_utc
from . import inventory_events as iev, issues, notifications

KINDS = ("write_off", "unplanned_receipt")
REASONS = {
    "write_off": ["damaged", "expired", "spoiled", "theft/loss", "supplier return", "other"],
    "unplanned_receipt": ["delivery without PO", "supplier return reversal", "transfer in", "other"],
}
KIND_LABEL = {"write_off": "write-off", "unplanned_receipt": "unplanned receipt"}


class StockEventError(Exception):
    pass


def create(db: Session, user: User, *, kind: str, location_id: int, product_id: int, qty: float, reason: str,
           note: str | None = None, photo_path: str | None = None, supplier_id: int | None = None, already_in_storehub: bool = False) -> StockEvent:
    from .sources import matches
    if not matches(db.get(Product, product_id), db.get(Location, location_id)):
        raise StockEventError("Choose a product from the same POS account as the store")
    if kind not in KINDS:
        raise StockEventError(f"kind must be one of {', '.join(KINDS)}")
    if qty is None or qty <= 0:
        raise StockEventError("Quantity must be greater than zero")
    if reason not in REASONS[kind]:
        raise StockEventError(f"Reason must be one of: {', '.join(REASONS[kind])}")
    if kind == "write_off":
        supplier_id = None  # a supplier only means something for an unplanned receipt
    ev = StockEvent(kind=kind, location_id=location_id, product_id=product_id, qty=qty, reason=reason,
                    note=note, photo_path=photo_path, supplier_id=supplier_id, created_by=user.id)
    if already_in_storehub:  # the change is already in StoreHub (the file came from there): nothing to key in, so it starts out verified
        ev.verified_at = now_utc()
    db.add(ev)
    db.flush()
    audit.log(db, actor_type="human", actor_id=user.id, action="stock_event.create", entity_type="stock_event", entity_id=ev.id,
              after={"kind": kind, "location_id": location_id, "product_id": product_id, "qty": qty, "reason": reason,
                     "note": note, "photo": bool(photo_path), "supplier_id": supplier_id})
    iev.record(db, uid=f"stockevt:{ev.id}", event_type="write_off" if kind == "write_off" else "supplier_receiving", user=user, product_id=product_id,
               location_id=location_id, quantity=qty, direction="-" if kind == "write_off" else "+", reason_code=reason, reason_text=note,
               evidence_path=photo_path, status="matched" if already_in_storehub else "pending")
    if already_in_storehub:
        return ev
    prod, loc = db.get(Product, product_id), db.get(Location, location_id)
    pos_name = "Loyverse" if loc and loc.source == "loyverse" else "StoreHub"
    label = KIND_LABEL[kind]
    issue = issues.create_issue(
        db, type="stock", severity="P3", location_id=location_id, product_id=product_id, source="manual",
        source_ref=f"stock_event:{ev.id}", created_by=user.id,
        title=f"Key the {label} into {pos_name}: {prod.name if prod else product_id} at {loc.name if loc else location_id} ({qty:g})",
        description=f"A {label} of {qty:g} ({reason}) was recorded in RetailOS. Enter the same adjustment in the {pos_name} back office "
                    "so stock updates. This issue closes by itself when a sync sees the change.",
        dedupe_key=f"stockevent:{ev.id}")
    ev.verify_issue_id = issue.id
    notifications.notify(db, role="store_manager", location_id=location_id, level="info",
                         title=f"{label.capitalize()} recorded: {prod.name if prod else product_id} ({qty:g}) — key it into {pos_name}",
                         link="/stock-events", dedupe_key=f"stockevent:note:{ev.id}")
    return ev


EVENT_TEMPLATE_CSV = "Product,Qty,Type,Reason,Note,Location,Supplier\n4800016644801,3,Write-off,damaged,Dropped box,,\n"
_EVENT_FIELDS = {
    "product": ("product", "barcode", "sku", "item", "name", "product name", "item name", "description"),
    "qty": ("qty", "quantity", "units", "amount"),
    "kind": ("type", "kind", "change", "change type", "event"),
    "reason": ("reason", "why", "cause"),
    "note": ("note", "notes", "remark", "remarks", "comment"),
    "location": ("location", "store", "branch", "outlet"),
    "supplier": ("supplier", "vendor"),
}
_REASON_WORDS = {"theft": "theft/loss", "loss": "theft/loss", "lost": "theft/loss", "stolen": "theft/loss", "return": "supplier return", "transfer": "transfer in", "delivery": "delivery without po"}


def _kind_of(text: str, default: str | None) -> str | None:
    t = (text or "").strip().lower()
    if not t:
        return default
    if re.search(r"receipt|receiv|delivery|\bin\b|add", t):
        return "unplanned_receipt"
    if re.search(r"write|off|loss|waste|damag|expire|spoil|out", t):
        return "write_off"
    return None


def _reason_of(text: str, kind: str) -> tuple[str, str | None]:
    """The reason from the list that matches what was typed (case and wording forgiven); anything else becomes 'other' and the typed words go into the note."""
    t = (text or "").strip()
    allowed = REASONS[kind]
    low = t.lower()
    hit = next((r for r in allowed if r.lower() == low), None) or next((r for r in allowed if low and (low in r.lower() or r.lower() in low)), None)
    if hit is None:
        for word, mapped in _REASON_WORDS.items():
            if word in low and mapped in [x.lower() for x in allowed]:
                hit = next(x for x in allowed if x.lower() == mapped)
                break
    if hit:
        return hit, None
    return "other", (t or None)


def import_events(db: Session, user: User, filename: str, data: bytes, locations: list[Location], default_location_id: int | None,
                  default_kind: str | None = None, already_in_storehub: bool = False) -> dict:
    """Record write-offs and unplanned receipts from a CSV or Excel file. Good rows are saved, the rest are listed with the reason.
    The same line uploaded twice on the same day is recorded once."""
    from . import tabular
    from ..models import Supplier
    table = tabular.clean_rows(tabular.read_table(filename, data))
    if not table:
        raise ValueError("The file is empty")
    col = tabular.map_columns(table[0], _EVENT_FIELDS, ("product", "qty"))
    rows = tabular.body(table)
    default = next((l for l in locations if l.id == default_location_id), None)
    suppliers = {s.name.strip().lower(): s.id for s in db.scalars(select(Supplier))}
    today = now_utc().date()
    added, duplicates, errors = 0, 0, []
    seen: set = set()
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
        kind = _kind_of(str(tabular.cell(row, col, "kind") or ""), default_kind)
        if kind is None:
            errors.append((n, "the type must be a write-off or an unplanned receipt"))
            continue
        key = str(tabular.cell(row, col, "product") if tabular.cell(row, col, "product") is not None else "").strip()
        prod, why = tabular.find_product(db, key) if key else (None, "product is empty")
        qty = tabular.to_qty(tabular.cell(row, col, "qty"))
        if not why and (qty is None or qty <= 0):
            why = "quantity must be a number above zero"
        if why:
            errors.append((n, why))
            continue
        reason, extra = _reason_of(str(tabular.cell(row, col, "reason") or ""), kind)
        note = " - ".join(x for x in (str(tabular.cell(row, col, "note") or "").strip(), extra) if x)[:500] or None
        sig = (loc.id, prod.id, kind, qty, reason)
        same = db.scalar(select(StockEvent.id).where(StockEvent.location_id == loc.id, StockEvent.product_id == prod.id, StockEvent.kind == kind, StockEvent.qty == qty,
                                                     StockEvent.reason == reason, func.date(StockEvent.created_at) == str(today)).limit(1))
        if same or sig in seen:
            duplicates += 1
            continue
        seen.add(sig)
        supplier_id = suppliers.get(str(tabular.cell(row, col, "supplier") or "").strip().lower()) if kind == "unplanned_receipt" else None
        create(db, user, kind=kind, location_id=loc.id, product_id=prod.id, qty=qty, reason=reason, note=note, supplier_id=supplier_id, already_in_storehub=already_in_storehub)
        added += 1
    audit.log(db, actor_type="human", actor_id=user.id, action="stock_event.import", entity_type="stock_event", entity_id=None,
              after={"file": filename, "added": added, "duplicates": duplicates, "errors": len(errors), "already_in_storehub": already_in_storehub})
    return {"added": added, "duplicates": duplicates, "errors": errors}


def verify_with_sync(db: Session) -> dict:
    """After each inventory sync: an event is verified when StoreHub stock moved the way the event says it should
    (down for a write-off, up for an unplanned receipt, at least half the recorded quantity)."""
    verified = 0
    since = now_utc() - timedelta(days=7)
    for ev in db.scalars(select(StockEvent).where(StockEvent.verified_at.is_(None), StockEvent.created_at >= since)):
        direction = InventoryChange.delta < 0 if ev.kind == "write_off" else InventoryChange.delta > 0
        chg = db.scalar(select(InventoryChange).where(InventoryChange.location_id == ev.location_id, InventoryChange.product_id == ev.product_id,
                                                      InventoryChange.observed_at >= ev.created_at, direction,
                                                      func.abs(InventoryChange.delta) >= ev.qty * 0.5)
                        .order_by(InventoryChange.observed_at).limit(1))
        if chg is None:
            continue
        ev.verified_at = now_utc()
        verified += 1
        traced = db.scalar(select(iev.InventoryEvent).where(iev.InventoryEvent.event_uid == f"stockevt:{ev.id}"))
        if traced is not None:
            iev.resolve(db, traced, "matched", "StoreHub stock moved as recorded")
        issue = db.get(Issue, ev.verify_issue_id) if ev.verify_issue_id else None
        if issue is not None and issue.status in issues.OPEN_STATES:
            before = issue.status
            issue.status, issue.closed_at = "closed", now_utc()
            db.add(IssueEvent(issue_id=issue.id, user_id=None, kind="status", text="Verified: the stock change was seen in StoreHub."))
            audit.log(db, actor_type="system", actor_id="sync", action="issue.close", entity_type="issue", entity_id=issue.id,
                      before={"status": before}, after={"status": "closed"}, reason="stock event verified by sync")
        audit.log(db, actor_type="system", actor_id="sync", action="stock_event.verify", entity_type="stock_event", entity_id=ev.id,
                  after={"inventory_change_id": chg.id, "delta": chg.delta})
    return {"verified": verified}
