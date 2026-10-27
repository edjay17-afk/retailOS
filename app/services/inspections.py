"""Inspections: daily tasks per location, checklist completion, failures become issues, missed tasks raise alerts."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select, func
from sqlalchemy.orm import Session, selectinload

from .. import audit
from ..models import (Inspection, InspectionResult, InspectionTemplate, Location, TemplateItem, Timesheet, User, Employee)
from ..timeutil import now_utc, tz
from . import issues, notifications


class InspectionError(Exception):
    pass


DONE = ("completed", "late")  # submitted on time / submitted after the due time


def enabled_location():
    """Zenwok Warehouse no longer participates in inspection tasks."""
    return func.lower(func.trim(Location.name)) != "zenwok warehouse"


def active_scope():
    return Inspection.location.has(enabled_location())


def _due_utc(d: date, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    local = datetime(d.year, d.month, d.day, h, m, tzinfo=tz())
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def generate_tasks(db: Session, d: date) -> int:
    """Create today's tasks for every monitored location. Safe to run repeatedly."""
    n = 0
    have = {(i.template_id, i.location_id) for i in db.scalars(select(Inspection).where(Inspection.business_date == d))}
    templates = list(db.scalars(select(InspectionTemplate).where(InspectionTemplate.active.is_(True))))
    for loc in db.scalars(select(Location).where(Location.monitored.is_(True), enabled_location())):
        for t in templates:
            if t.location_type != loc.type or (t.id, loc.id) in have:
                continue
            db.add(Inspection(template_id=t.id, location_id=loc.id, business_date=d, due_at=_due_utc(d, t.due_time)))
            n += 1
    return n


def clocked_in(db: Session, user: User, now: datetime | None = None) -> bool | None:
    """True/False from StoreHub timesheets, or None when the user is not linked to a StoreHub employee."""
    employee = db.scalar(select(Employee).where(Employee.storehub_id == user.storehub_employee_id)) if user.storehub_employee_id else None
    if employee and employee.source == "loyverse": return None
    if not user.storehub_employee_id:
        return None
    now = now or now_utc()
    q = select(Timesheet).where(Timesheet.employee_sh_id == user.storehub_employee_id, Timesheet.clock_in <= now,
                                (Timesheet.clock_out.is_(None)) | (Timesheet.clock_out >= now - timedelta(minutes=15)))
    return db.scalar(q) is not None


def start(db: Session, insp: Inspection, user: User) -> Inspection:
    if insp.status in DONE:
        raise InspectionError("This inspection is already completed")
    ok = clocked_in(db, user)  # only recorded for reference: being clocked in at the POS is not required to do an inspection
    if insp.status in ("pending", "missed"):
        insp.status = "in_progress" if insp.status == "pending" else "missed"
        insp.started_at = insp.started_at or now_utc()
        insp.assigned_to = insp.assigned_to or user.id
        insp.clocked_in_ok = ok
    return insp


def evaluate(item: TemplateItem, value: str | None, passed: bool | None, photo_only: bool = False) -> tuple[bool | None, str | None]:
    """Decide pass/fail for one checklist line. Returns (passed, stored value). In a photo-only checklist a plain check has no verdict: the photos are reviewed by a person."""
    if item.kind == "number":
        try:
            v = float((value or "").strip())
        except ValueError:
            raise InspectionError(f"'{item.label}': enter a number")
        ok = (item.min_value is None or v >= item.min_value) and (item.max_value is None or v <= item.max_value)
        return ok, f"{v:g}"
    if item.kind == "text":
        return None, (value or "").strip() or None
    if item.kind == "video":
        return None, "video"
    if photo_only:
        return None, "photo"
    if passed is None:
        raise InspectionError(f"'{item.label}': choose Pass or Fail")
    return passed, "pass" if passed else "fail"


def complete(db: Session, insp: Inspection, user: User, answers: dict[int, dict],
             photos: dict[int, str | list[str] | None], videos: dict[int, str | list[str] | None] | None = None) -> Inspection:
    """Save all photos per item. Older callers may pass a single photo or walkthrough path."""
    if insp.status in DONE:
        raise InspectionError("This inspection is already completed")
    items = {i.id: i for i in insp.template.items}
    insp.results.clear()
    db.flush()
    fails = 0
    failed: list[tuple[TemplateItem, str | None, str | None]] = []
    for iid, item in items.items():
        a = answers.get(iid, {})
        photo_only = insp.template.photo_only
        passed, stored = evaluate(item, a.get("value"), a.get("passed"), photo_only)
        if photo_only and item.kind == "number":
            passed = None  # a reading is recorded, not judged
        note = None if photo_only else ((a.get("note") or "").strip() or None)
        supplied = photos.get(iid)
        paths = list(supplied) if isinstance(supplied, list) else ([supplied] if supplied else [])
        clips: list[str] = []
        if item.kind == "video":
            if videos is None:  # compatibility with the original single-video service calls
                clips, paths = (paths[:1] if paths else []), []
            else:
                got = videos.get(iid)
                clips = [v for v in (got if isinstance(got, list) else [got]) if v]
        if item.kind == "video" and not clips:
            raise InspectionError(f"'{item.label}': attach at least one video before sending the checklist")
        if passed is False:
            fails += 1
            if not note:
                raise InspectionError(f"'{item.label}' failed: write a short note about what is wrong")
            if item.photo_on_fail and not paths:
                raise InspectionError(f"'{item.label}' failed: attach a photo as evidence")
            failed.append((item, stored, note))
        insp.results.append(InspectionResult(item_id=iid, value=stored, passed=passed, note=note,
                                             photo_path=(clips[0] if clips else None) or (paths[0] if paths else None), photo_paths=paths,
                                             video_paths=clips or None))
    now = now_utc()
    was_late = now > insp.due_at
    insp.status, insp.completed_at, insp.completed_by, insp.fail_count = ("late" if was_late else "completed"), now, user.id, fails
    insp.assigned_to = insp.assigned_to or user.id
    for item, stored, note in failed:
        issues.create_issue(
            db, type="inspection", severity=item.fail_severity, location_id=insp.location_id, source="inspection",
            source_ref=f"inspection:{insp.id}", created_by=user.id,
            title=f"{insp.template.name}: {item.label} failed at {insp.location.name}",
            description=f"Reading/result: {stored}. Note: {note}", dedupe_key=f"insp:{insp.location_id}:{item.id}:{insp.business_date}")
    audit.log(db, actor_type="human", actor_id=user.id, action="inspection.complete", entity_type="inspection", entity_id=insp.id,
              after={"fails": fails, "late": was_late, "clocked_in_ok": insp.clocked_in_ok})
    return insp


def flag_missed(db: Session, grace_minutes: int = 30) -> int:
    """Remind the shift lead about checklists not submitted after their due time."""
    now = now_utc()
    n = 0
    # only tasks that existed before their due time can be "missed"; a task created late (first start-up) is not an alert
    q = select(Inspection).where(active_scope(), Inspection.status.in_(("pending", "in_progress")), Inspection.due_at < now - timedelta(minutes=grace_minutes),
                                 Inspection.missed_alerted.is_(False), Inspection.created_at <= Inspection.due_at).options(selectinload(Inspection.template), selectinload(Inspection.location))
    for i in db.scalars(q):
        i.missed_alerted = True
        i.status = "missed"
        notifications.notify(db, role="store_manager", location_id=i.location_id, level="warning",
                             title=f"Checklist not submitted: {i.template.name} at {i.location.name}", link=f"/inspections/{i.id}",
                             dedupe_key=f"missed:{i.id}")
        issues.create_issue(db, type="inspection", severity="P2", location_id=i.location_id, source="inspection", source_ref=f"inspection:{i.id}",
                            title=f"Missed checklist: {i.template.name} at {i.location.name} ({i.business_date})",
                            description="The checklist was not submitted by its due time.", dedupe_key=f"missed:{i.id}")
        n += 1
    return n


def todays(db: Session, d: date, location_ids: list[int]) -> list[Inspection]:
    q = select(Inspection).where(Inspection.business_date == d, Inspection.location_id.in_(location_ids or [0]), active_scope()) \
        .options(selectinload(Inspection.template), selectinload(Inspection.location)).order_by(Inspection.due_at)
    return list(db.scalars(q))
