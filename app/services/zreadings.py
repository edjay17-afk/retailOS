"""Official StoreHub Z-reading reports, copied with read-only back-office GET requests.

StoreHub's Partner API has no Z-reading endpoint. The back-office report list and each official detail page are read as they are,
then saved locally by StoreHub report ID. Nothing is calculated here and nothing is ever written back to StoreHub.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Setting, ZReading
from ..timeutil import now_utc, today_local
from .sales_shifts import money, stores, when

log = logging.getLogger("retailos.zreadings")
EVERY_MINUTES = 60
RETRY_MINUTES = 20
HISTORY_DAYS = 180
RECENT_DAYS = 3
PER_RUN = 40


def apply(db: Session, rows: list[dict], details: dict[str, dict]) -> dict:
    """Upsert StoreHub list rows and any official details that were read. Returns local change counts."""
    locs = stores(db)
    have = {z.sh_id: z for z in db.scalars(select(ZReading))}
    new = updated = detailed = 0
    for r in rows:
        sid, loc, closed = r.get("DT_RowId"), locs.get(str(r.get("2", "")).strip()), when(r.get("0"))
        if not sid or loc is None or closed is None:
            continue
        z = have.get(sid)
        if z is None:
            z = ZReading(sh_id=sid, location_id=loc.id, close_at=closed)
            db.add(z)
            have[sid] = z
            new += 1
        else:
            updated += 1
        z.location_id, z.close_at = loc.id, closed
        z.z_count = int(money(r.get("1")))
        z.register = str(r.get("3", "")).strip()
        z.tx_count = int(money(r.get("4")))
        z.gross_sales = money(r.get("5"))
        if sid in details:
            payload = details[sid]
            sections = payload.get("sections") or []
            if sections:  # never mark an empty/error page as a completed official report
                z.detail_json = sections
                z.source_text = payload.get("source_text") or ""
                z.detail_ok = True
                detailed += 1
        z.updated_at = now_utc()
    db.flush()
    return {"new": new, "updated": updated, "detailed": detailed}


def pending(db: Session) -> int:
    """Reports whose official detail page has not been read yet."""
    return db.scalar(select(func.count()).select_from(ZReading).where(ZReading.detail_ok.is_(False))) or 0


def _mark(db: Session, key: str) -> None:
    row = db.get(Setting, key) or Setting(key=key, value="")
    row.value = now_utc().isoformat()
    db.add(row)
    db.flush()


def due(db: Session) -> bool:
    now = now_utc()
    gap_ok = 10 if pending(db) else EVERY_MINUTES
    for key, gap in (("zreadings.last_ok", gap_ok), ("zreadings.last_attempt", RETRY_MINUTES)):
        row = db.get(Setting, key)
        if row and row.value:
            try:
                if now - datetime.fromisoformat(str(row.value)) < timedelta(minutes=gap):
                    return False
            except ValueError:
                pass
    return True


def window(db: Session) -> tuple[date, date]:
    """First run reads 180 days; later runs refresh recent reports and catch up missing details."""
    today = today_local()
    if (db.scalar(select(func.count()).select_from(ZReading)) or 0) == 0:
        return today - timedelta(days=HISTORY_DAYS), today
    oldest = db.scalar(select(func.min(ZReading.close_at)).where(ZReading.detail_ok.is_(False)))
    start = today - timedelta(days=RECENT_DAYS)
    if oldest:
        start = min(start, oldest.date())
    return max(start, today - timedelta(days=HISTORY_DAYS)), today


def run(db: Session, fetch=None) -> dict:
    """Read official reports through the GET-only robot and save them locally. Failures are retried by the scheduler."""
    from .. import robot
    if fetch is None and not robot.configured():
        return {"ran": False}
    fetch = fetch or robot.fetch_zreadings
    _mark(db, "zreadings.last_attempt")
    db.commit()
    start, end = window(db)
    skip = {z.sh_id for z in db.scalars(select(ZReading).where(ZReading.detail_ok.is_(True)))
            if z.close_at.date() < today_local() - timedelta(days=RECENT_DAYS)}
    try:
        rows, details = fetch(start, end, skip, PER_RUN)
    except (robot.RobotLoginFailed, robot.RobotNotConfigured, robot.RobotError) as e:
        log.error("Z-reading sync failed: %s", e)
        return {"ran": True, "ok": False, "error": str(e)}
    out = apply(db, rows, details)
    _mark(db, "zreadings.last_ok")
    db.commit()
    return {"ran": True, "ok": True, "listed": len(rows), "still_unread": pending(db), **out}


def readings(db: Session, start: date, end: date, loc_ids: list[int] | None = None) -> list[ZReading]:
    q = select(ZReading).where(ZReading.close_at >= datetime.combine(start, datetime.min.time()),
                               ZReading.close_at < datetime.combine(end + timedelta(days=1), datetime.min.time()))
    if loc_ids is not None:
        q = q.where(ZReading.location_id.in_(loc_ids or [0]))
    return list(db.scalars(q.order_by(ZReading.close_at.desc(), ZReading.id.desc())))


def _begin_si(z: ZReading) -> str | None:
    m = re.search(r"Beg\. SI #:\s*(\d+)", z.source_text or "")
    return m.group(1) if m else None


def interim(db: Session, rows: list[ZReading]) -> dict[int, ZReading]:
    """{report id: the report that replaces it}. StoreHub also prints a Z-report when a shift closes in the middle of a day; it keeps the day's Z-count and
    starts at the same invoice number, so the later report of the same store, register and Z-count already contains it. Only the later one is counted."""
    if not rows:
        return {}
    others: dict[tuple, list[ZReading]] = {}
    for z in db.scalars(select(ZReading).where(ZReading.z_count.in_({r.z_count for r in rows}), ZReading.location_id.in_({r.location_id for r in rows}))):
        others.setdefault((z.location_id, z.register, z.z_count), []).append(z)
    out: dict[int, ZReading] = {}
    for r in rows:
        later = [z for z in others.get((r.location_id, r.register, r.z_count), []) if z.close_at > r.close_at]
        if not later:
            continue
        final = max(later, key=lambda z: z.close_at)
        a, b = _begin_si(r), _begin_si(final)
        if a is None or b is None or a == b:        # the same first invoice number: the later report contains this one
            out[r.id] = final
    return out


def totals(rows: list[ZReading], interim_ids: set[int] | frozenset[int] = frozenset()) -> dict:
    """Totals of the official reports, leaving out interim ones that a later report of the same day already contains."""
    counted = [r for r in rows if r.id not in interim_ids]
    return {"reports": len(counted), "tx": sum(r.tx_count for r in counted), "sales": round(sum(r.gross_sales for r in counted), 2), "interim": len(rows) - len(counted)}
