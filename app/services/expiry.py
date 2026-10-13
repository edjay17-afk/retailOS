"""Expiry tracking. StoreHub has no expiry data, so dates are captured at receiving or during the expiry sweep."""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import audit
from .. import settings_service as cfg
from ..models import ExpiryBatch, Location, Product, User
from ..timeutil import today_local
from . import issues, notifications
from .tabular import find_product as _find_product, norm as _norm, read_table as _read_table, to_date as _to_date, to_qty as _to_qty  # shared file helpers


def stage_for(days_left: int, warn_days: list[int]) -> str | None:
    """expired | 3 | 7 | 14 | 30 (the tightest stage reached), or None if still comfortable."""
    if days_left < 0:
        return "expired"
    for w in sorted(warn_days):
        if days_left <= w:
            return str(w)
    return None


SEVERITY = {"expired": "P1", "3": "P1", "7": "P2", "14": "P3", "30": "P3"}


def active_batches(db: Session, location_ids: list[int] | None = None) -> list[ExpiryBatch]:
    q = select(ExpiryBatch).where(ExpiryBatch.status == "active").options(selectinload(ExpiryBatch.product), selectinload(ExpiryBatch.location)) \
        .order_by(ExpiryBatch.expiry_date)
    if location_ids is not None:
        q = q.where(ExpiryBatch.location_id.in_(location_ids or [0]))
    return list(db.scalars(q))


def check(db: Session) -> int:
    """Daily: raise one issue per batch per stage (30, 14, 7, 3 days, expired). Idempotent."""
    warn = cfg.get(db, "expiry.warn_days")
    today = today_local()
    n = 0
    for b in active_batches(db):
        days_left = (b.expiry_date - today).days
        stage = stage_for(days_left, warn)
        if stage is None:
            continue
        label = "has expired" if stage == "expired" else f"expires in {days_left} day(s)"
        issues.create_issue(db, type="expiry", severity=SEVERITY[stage], location_id=b.location_id, product_id=b.product_id, source="system",
                            source_ref=f"expiry:{b.id}", title=f"{b.product.name} at {b.location.name} {label} ({b.qty:g} units)",
                            description=f"Batch {b.batch_no or '-'} expiry {b.expiry_date}. Markdown, pull from shelf or return to warehouse; "
                                        f"write-offs must be entered in StoreHub.", dedupe_key=f"expiry:{b.id}:{stage}")
        n += 1
    return n


def add_batch(db: Session, user: User, location_id: int, product_id: int, qty: float, expiry: date, batch_no: str | None = None, note: str | None = None) -> ExpiryBatch:
    if qty <= 0:
        raise ValueError("Quantity must be above zero")
    b = ExpiryBatch(location_id=location_id, product_id=product_id, qty=qty, expiry_date=expiry, batch_no=batch_no, received_date=today_local(),
                    source="sweep", note=note)
    db.add(b)
    db.flush()
    audit.log(db, actor_type="human", actor_id=user.id, action="expiry.add", entity_type="expiry_batch", entity_id=b.id,
              after={"product_id": product_id, "qty": qty, "expiry": str(expiry)})
    return b


def clear(db: Session, user: User, b: ExpiryBatch, status: str, note: str | None) -> ExpiryBatch:
    if status not in ("cleared", "written_off"):
        raise ValueError("bad status")
    b.status, b.note = status, note
    audit.log(db, actor_type="human", actor_id=user.id, action=f"expiry.{status}", entity_type="expiry_batch", entity_id=b.id, reason=note)
    return b


# ------------------------------------------------------------------------------------------------ CSV / Excel import
MAX_IMPORT_ROWS = 5000
_HEADERS = {
    "product": ("product", "barcode", "sku", "item", "name", "product name", "product barcode", "item name", "description"),
    "qty": ("qty", "quantity", "qty on hand", "units", "count"),
    "expiry": ("expiry", "expiry date", "expiration", "expiration date", "exp", "exp date", "best before", "use by"),
    "batch": ("batch", "batch no", "batch number", "lot", "lot no"),
    "location": ("location", "store", "branch", "outlet"),
}
TEMPLATE_CSV = "Product,Qty,Expiry date,Batch,Location\n4800016644801,24,2026-12-31,B-102,\n"


def import_batches(db: Session, user: User, filename: str, data: bytes, locations: list[Location], default_location_id: int | None) -> dict:
    """Record expiry batches from a CSV or Excel file. Valid rows are saved, the rest are listed with the reason. Safe to upload twice."""
    table = [r for r in _read_table(filename, data) if r and any(str(x or "").strip() for x in r)]
    if not table:
        raise ValueError("The file is empty")
    head = [_norm(x) for x in table[0]]
    col: dict[str, int] = {}
    for field, names in _HEADERS.items():
        for i, h in enumerate(head):
            if h in names and i not in col.values():
                col[field] = i
                break
    missing = [f for f in ("product", "qty", "expiry") if f not in col]
    if missing:
        raise ValueError("The first row must name the columns. Missing: " + ", ".join(missing) + ". Download the template to see the layout")
    body = table[1:]
    if len(body) > MAX_IMPORT_ROWS:
        raise ValueError(f"At most {MAX_IMPORT_ROWS} rows per file")
    by_name = {_norm(l.name): l for l in locations}
    default = next((l for l in locations if l.id == default_location_id), None)
    added, duplicates, errors = 0, 0, []
    seen: set = set()
    for n, row in enumerate(body, start=2):
        def cell(f):
            i = col.get(f)
            return row[i] if i is not None and i < len(row) else None
        why = None
        loc, prod, qty, exp = default, None, _to_qty(cell("qty")), _to_date(cell("expiry"))
        if str(cell("location") or "").strip():
            k = _norm(cell("location"))
            loc = by_name.get(k) or next((l for nm, l in by_name.items() if k in nm or nm.replace("the daily centro", "").strip() == k), None)
            why = None if loc else f"location '{cell('location')}' not found or not allowed"
        elif loc is None:
            why = "no location chosen"
        if not why:
            key = str(cell("product") if cell("product") is not None else "").strip()
            prod, why = _find_product(db, key) if key else (None, "product is empty")
        if not why and (qty is None or qty <= 0):
            why = "quantity must be a number above zero"
        if not why and exp is None:
            why = "expiry date is missing or not a valid date"
        if why:
            errors.append((n, why))
            continue
        batch = str(cell("batch") or "").strip()[:40] or None
        sig = (loc.id, prod.id, exp, batch)
        dup = sig in seen or db.scalar(select(ExpiryBatch.id).where(ExpiryBatch.location_id == loc.id, ExpiryBatch.product_id == prod.id, ExpiryBatch.expiry_date == exp,
                                                                    ExpiryBatch.batch_no.is_(None) if batch is None else ExpiryBatch.batch_no == batch,
                                                                    ExpiryBatch.status == "active").limit(1))
        seen.add(sig)
        if dup:
            duplicates += 1
            continue
        b = add_batch(db, user, loc.id, prod.id, qty, exp, batch)
        b.source = "import"
        added += 1
    audit.log(db, actor_type="human", actor_id=user.id, action="expiry.import", entity_type="expiry_batch", entity_id=None, after={"file": filename, "added": added, "duplicates": duplicates, "errors": len(errors)})
    return {"added": added, "duplicates": duplicates, "errors": errors}
