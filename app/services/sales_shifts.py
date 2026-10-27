"""Sales by shift, as StoreHub's own Shift Reports show them.

StoreHub's API has no shifts, but its back-office Shift Reports page does: when a register was opened and closed, how many sales, and (on each shift's own
page) the cashier, the opening fund, what was paid in cash and by GCash, card, and the withdrawals. The robot reads those pages (read only: it only looks, as a
person would) and the shifts are saved here. Nothing is ever written to StoreHub.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Employee, Location, SalesShift, Setting
from ..timeutil import now_utc, today_local

log = logging.getLogger("retailos.sales_shifts")
EVERY_MINUTES = 60      # when everything is read
CATCH_UP_MINUTES = 10   # while shifts still lack their own page (the first run reads a few dozen at a time)
RETRY_MINUTES = 20
HISTORY_DAYS = 180
RECENT_DAYS = 3         # a shift just closed can still change a little, so the last days are read again
PER_RUN = 40


def money(text: str | None) -> float:
    try:
        return round(float(str(text or "0").replace(",", "").strip() or 0), 2)
    except ValueError:
        return 0.0


def when(text: str | None) -> datetime | None:
    try:
        return datetime.strptime((text or "").strip(), "%m/%d/%Y %H:%M")
    except ValueError:
        return None


def _after(label: str, text: str) -> str:
    """The value printed under 'Label:' (the page prints each label and then its value on a later line)."""
    lines = [l.strip() for l in text.splitlines()]
    for i, l in enumerate(lines):
        if l == label:
            for v in lines[i + 1:i + 6]:
                if v:
                    return "" if v.endswith(":") else v
    return ""


def parse_detail(text: str) -> dict:
    """What a shift's own page says. Missing parts come back as zero / empty rather than failing."""
    out: dict = {"cashier": _after("Cashier:", text)}
    m = re.search(r"^Opening Fund\t([\d,]+(?:\.\d+)?)\s*$", text, re.M)
    out["opening_fund"] = money(m.group(1)) if m else None
    pay: dict[str, list] = {}
    a = text.find("Payments Received")
    b = text.find("Total Payments:", a if a >= 0 else 0)
    if a >= 0 and b > a:
        for line in text[a:b].splitlines():
            parts = [p.strip() for p in line.split("\t")]
            if len(parts) == 3 and re.fullmatch(r"\d+", parts[1]) and re.fullmatch(r"[\d,]+(?:\.\d+)?", parts[2]):
                pay[parts[0]] = [int(parts[1]), money(parts[2])]
    out["payments"] = pay
    for key, label in (("void_total", "Total Void:"), ("refund_total", "Total Refund:")):
        m = re.search(rf"^{re.escape(label)}\t(?:\d+\t)?([\d,]+(?:\.\d+)?)\s*$", text, re.M)
        out[key] = money(m.group(1)) if m else 0.0
    m = re.search(r"^Withdrawals\t(\d+)\t([\d,]+(?:\.\d+)?)\s*$", text, re.M)
    out["withdrawals_qty"], out["withdrawals"] = (int(m.group(1)), money(m.group(2))) if m else (0, 0.0)
    m = re.search(r"^Discount\t\d+\t([\d,]+(?:\.\d+)?)\s*$", text, re.M)
    out["discount_total"] = money(m.group(1)) if m else 0.0
    out["tax_total"] = round(sum(money(g) for g in re.findall(r"^[A-Za-z][\w ]*\t\d+(?:\.\d+)?%\t([\d,]+(?:\.\d+)?)\s*$", text, re.M)), 2)
    return out


def stores(db: Session) -> dict[str, Location]:
    return {l.name: l for l in db.scalars(select(Location).where(Location.type == "store", Location.source == "storehub", Location.monitored.is_(True)))}


def apply(db: Session, rows: list[dict], texts: dict[str, str]) -> dict:
    """Save the list rows (one per shift) and, where the page was read, the details. Returns counts."""
    locs = stores(db)
    have = {s.sh_id: s for s in db.scalars(select(SalesShift))}
    new = updated = detailed = 0
    for r in rows:
        sid, loc = r.get("DT_RowId"), locs.get(str(r.get("2", "")).strip())
        opened = when(r.get("0"))
        if not sid or loc is None or opened is None:
            continue
        s = have.get(sid)
        if s is None:
            s = SalesShift(sh_id=sid, location_id=loc.id, opened_at=opened)
            db.add(s)
            have[sid] = s
            new += 1
        else:
            updated += 1
        s.location_id, s.opened_at, s.closed_at = loc.id, opened, when(r.get("1"))
        s.register = str(r.get("3", "")).strip()
        s.tx_count = int(money(r.get("4")))
        s.total_sales, s.deposit = money(r.get("5")), money(r.get("6"))
        if sid in texts:
            d = parse_detail(texts[sid])
            for k, v in d.items():
                setattr(s, k, v)
            s.detail_ok = True
            detailed += 1
        s.updated_at = now_utc()
    db.flush()
    return {"new": new, "updated": updated, "detailed": detailed}


def pending(db: Session) -> int:
    """Shifts whose own page has not been read yet, or that are still open."""
    return db.scalar(select(func.count()).select_from(SalesShift).where((SalesShift.detail_ok.is_(False)) | (SalesShift.closed_at.is_(None)))) or 0


def _mark(db: Session, key: str) -> None:
    row = db.get(Setting, key) or Setting(key=key, value="")
    row.value = now_utc().isoformat()
    db.add(row)
    db.flush()


def due(db: Session) -> bool:
    now = now_utc()
    gap_ok = CATCH_UP_MINUTES if pending(db) else EVERY_MINUTES
    for key, gap in (("sales_shifts.last_ok", gap_ok), ("sales_shifts.last_attempt", RETRY_MINUTES)):
        row = db.get(Setting, key)
        if row and row.value:
            try:
                if now - datetime.fromisoformat(str(row.value)) < timedelta(minutes=gap):
                    return False
            except ValueError:
                pass
    return True


def window(db: Session) -> tuple[date, date]:
    """The first run reads the last six months; later runs only the last few days (plus anything not read yet, which stays in the list)."""
    today = today_local()
    if (db.scalar(select(func.count()).select_from(SalesShift)) or 0) == 0:
        return today - timedelta(days=HISTORY_DAYS), today
    oldest = db.scalar(select(func.min(SalesShift.opened_at)).where(SalesShift.detail_ok.is_(False)))
    start = today - timedelta(days=RECENT_DAYS)
    if oldest:
        start = min(start, oldest.date())
    return max(start, today - timedelta(days=HISTORY_DAYS)), today


def run(db: Session, fetch=None) -> dict:
    """Read StoreHub's Shift Reports through the robot and save them. A failure is logged and retried later; it never stops the other jobs."""
    from .. import robot
    if fetch is None and not robot.configured():
        return {"ran": False}
    fetch = fetch or robot.fetch_shifts
    _mark(db, "sales_shifts.last_attempt")
    db.commit()
    start, end = window(db)
    skip = {s.sh_id for s in db.scalars(select(SalesShift).where(SalesShift.detail_ok.is_(True), SalesShift.closed_at.is_not(None)))
            if s.closed_at and s.closed_at.date() < today_local() - timedelta(days=RECENT_DAYS)}
    try:
        rows, texts = fetch(start, end, skip, PER_RUN)
    except (robot.RobotLoginFailed, robot.RobotNotConfigured, robot.RobotError) as e:
        log.error("shift report failed: %s", e)
        return {"ran": True, "ok": False, "error": str(e)}
    out = apply(db, rows, texts)
    _mark(db, "sales_shifts.last_ok")
    db.commit()
    return {"ran": True, "ok": True, "listed": len(rows), "still_unread": pending(db), **out}


# ------------------------------------------------------------------------------------------------ reading them back
def shifts(db: Session, start: date, end: date, loc_ids: list[int] | None = None) -> list[SalesShift]:
    q = select(SalesShift).where(SalesShift.opened_at >= datetime.combine(start, datetime.min.time()),
                                 SalesShift.opened_at < datetime.combine(end + timedelta(days=1), datetime.min.time()))
    if loc_ids is not None:
        q = q.where(SalesShift.location_id.in_(loc_ids))
    return list(db.scalars(q.order_by(SalesShift.opened_at.desc(), SalesShift.id.desc())))


def totals(rows: list[SalesShift]) -> dict:
    pay: dict[str, float] = {}
    for s in rows:
        for k, v in (s.payments or {}).items():
            pay[k] = round(pay.get(k, 0) + float(v[1]), 2)
    return {"shifts": len(rows), "tx": sum(s.tx_count for s in rows), "sales": round(sum(s.total_sales for s in rows), 2),
            "withdrawals": round(sum(s.withdrawals for s in rows), 2), "payments": pay}


def cashier_employee_ids(db: Session, rows: list[SalesShift]) -> dict[int, str]:
    """Link name-only shift cashiers only when a StoreHub employee matches uniquely."""
    def normalized(value: str) -> str:
        return " ".join(value.replace(",", " ").split()).casefold()

    candidates: dict[str, set[str]] = {}
    for employee in db.scalars(select(Employee).where(Employee.source == "storehub")):
        for name in (employee.name, f"{employee.last_name} {employee.first_name}"):
            key = normalized(name)
            if key:
                candidates.setdefault(key, set()).add(employee.storehub_id)
    matches = {}
    for row in rows:
        ids = candidates.get(normalized(row.cashier or ""), set())
        if len(ids) == 1:
            matches[row.id] = next(iter(ids))
    return matches
