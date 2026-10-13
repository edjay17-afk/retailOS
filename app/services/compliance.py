"""Permits, certificates and equipment servicing (proposal flow G): alert 30 days before a renewal or service date, then escalate."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import audit
from ..models import ComplianceItem, User
from ..timeutil import today_local
from . import issues

KINDS = {"permit": "Permit", "certificate": "Certificate", "equipment": "Equipment service"}
STAGES = [(0, "P1", "is due today or overdue"), (7, "P1", "is due within 7 days"), (14, "P2", "is due within 14 days"), (30, "P3", "is due within 30 days")]


def stage(days_left: int) -> tuple[int, str, str] | None:
    """The tightest stage reached, e.g. 5 days left -> the 7-day stage."""
    if days_left <= 0:
        return STAGES[0]
    for s in (STAGES[1], STAGES[2], STAGES[3]):
        if days_left <= s[0]:
            return s
    return None


def active(db: Session, location_ids: list[int] | None = None) -> list[ComplianceItem]:
    q = select(ComplianceItem).where(ComplianceItem.status == "active").options(selectinload(ComplianceItem.location), selectinload(ComplianceItem.owner)).order_by(ComplianceItem.due_date.is_(None), ComplianceItem.due_date)
    if location_ids is not None:
        q = q.where((ComplianceItem.location_id.in_(location_ids or [0])) | ComplianceItem.location_id.is_(None))
    return list(db.scalars(q))


def check(db: Session, today: date | None = None) -> int:
    """Weekly (and on start-up): one issue per item per stage. Safe to run repeatedly."""
    today = today or today_local()
    n = 0
    for it in active(db):
        if it.due_date is None:  # no expiry: nothing to warn about
            continue
        st = stage((it.due_date - today).days)
        if st is None:
            continue
        days = (it.due_date - today).days
        what = "is overdue by %d day(s)" % -days if days < 0 else st[2]
        issues.create_issue(db, type="permit" if it.kind != "equipment" else "equipment", severity=st[1], location_id=it.location_id, owner_id=it.owner_id, source="system",
                            source_ref=f"compliance:{it.id}", title=f"{KINDS[it.kind]} '{it.name}' {what} ({it.due_date})",
                            description=f"Renew it, then record the new dates in Permits. Reference: {it.reference or '-'}.", dedupe_key=f"compliance:{it.id}:{st[0]}")
        n += 1
    return n


def add(db: Session, user: User, *, kind: str = "permit", name: str, due: date | None, location_id: int | None, owner_id: int | None = None, reference: str | None = None, notes: str | None = None,
        processed: date | None = None) -> ComplianceItem:
    """`due` is the expiry date (None when the permit has none: it is shown as No Expiry); `processed` is when the permit was processed."""
    if kind not in KINDS:
        raise ValueError("Unknown kind")
    if not name.strip():
        raise ValueError("Give it a name")
    if processed and due and due <= processed:
        raise ValueError("The expiry date must be after the date the permit was processed")
    it = ComplianceItem(kind=kind, name=name.strip(), due_date=due, processed_date=processed, location_id=location_id, owner_id=owner_id, reference=reference or None, notes=notes or None)
    db.add(it)
    db.flush()
    audit.log(db, actor_type="human", actor_id=user.id, action="compliance.add", entity_type="compliance_item", entity_id=it.id, after={"kind": kind, "name": name, "processed": str(processed) if processed else None, "expiry": str(due) if due else None})
    return it


def renew(db: Session, user: User, it: ComplianceItem, new_due: date, note: str | None, processed: date | None = None) -> ComplianceItem:
    if it.due_date and new_due <= it.due_date:
        raise ValueError("The new expiry date must be later than the current one")
    if processed and new_due <= processed:
        raise ValueError("The expiry date must be after the date the permit was processed")
    before = {"due": str(it.due_date) if it.due_date else None, "processed": str(it.processed_date) if it.processed_date else None}
    it.due_date = new_due
    if processed:
        it.processed_date = processed
    audit.log(db, actor_type="human", actor_id=user.id, action="compliance.renew", entity_type="compliance_item", entity_id=it.id, before=before, after={"due": str(new_due)}, reason=note)
    return it
