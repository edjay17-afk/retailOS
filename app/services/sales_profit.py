"""Gross Profit per store per day, as StoreHub itself reports it.

StoreHub's API does not send the cost of an item on a sale, so Retail OS cannot work out the Gross Profit of a past day. StoreHub's back-office "Sales over time"
report has it, so the robot reads that report (read only: it only looks at the page, as a person would) and the Gross Profit is saved on each sales day.
The sales report workbook then shows StoreHub's own figure. Nothing is ever written to StoreHub.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Location, SalesDay, Setting
from ..timeutil import now_utc, to_local, today_local

log = logging.getLogger("retailos.sales_profit")
EVERY_MINUTES = 180
RETRY_MINUTES = 30
RECENT_DAYS = 14  # StoreHub can still change a recent day, so these are read again every time


def due(db: Session) -> bool:
    now = now_utc()
    for key, gap in (("sales_profit.last_ok", EVERY_MINUTES), ("sales_profit.last_attempt", RETRY_MINUTES)):
        row = db.get(Setting, key)
        if row and row.value:
            try:
                if now - datetime.fromisoformat(str(row.value)) < timedelta(minutes=gap):
                    return False
            except ValueError:
                pass
    return True


def _mark(db: Session, key: str) -> None:
    row = db.get(Setting, key) or Setting(key=key, value="")
    row.value = now_utc().isoformat()
    db.add(row)
    db.flush()


def stores(db: Session) -> list[Location]:
    return list(db.scalars(select(Location).where(Location.type == "store", Location.monitored.is_(True)).order_by(Location.name)))


def local_date(stamp: str) -> date:
    """StoreHub sends a day as its local midnight in UTC ('2026-10-09T16:00:00.000Z' is 10 Oct in Manila)."""
    return to_local(datetime.fromisoformat(stamp.replace("Z", "+00:00")).replace(tzinfo=None)).date()


def window(db: Session) -> tuple[date, date]:
    """From the oldest day still without a Gross Profit (or the last two weeks, whichever is older) to today."""
    today = today_local()
    oldest = db.scalar(select(func.min(SalesDay.business_date)).where(SalesDay.gross_profit.is_(None)))
    start = min(oldest, today - timedelta(days=RECENT_DAYS)) if oldest else today - timedelta(days=RECENT_DAYS)
    return max(start, today - timedelta(days=800)), today


def apply_rows(db: Session, loc: Location, rows: list[dict]) -> int:
    """Save StoreHub's Gross Profit on the sales days we have for this store. Returns how many days were set."""
    have = {d.business_date: d for d in db.scalars(select(SalesDay).where(SalesDay.location_id == loc.id))}
    n = 0
    for r in rows:
        try:
            day, gp = local_date(r["date"]), r.get("grossProfit")
        except (KeyError, ValueError, TypeError):
            continue
        sd = have.get(day)
        if sd is not None and isinstance(gp, (int, float)):
            if sd.gross_profit != round(float(gp), 2):
                sd.gross_profit = round(float(gp), 2)
            n += 1
    return n


def run(db: Session, fetch=None) -> dict:
    """Ask the robot for StoreHub's daily report of every store and save the Gross Profit. A failure is logged and retried later; it never stops the other jobs."""
    from .. import robot
    if not robot.configured():
        return {"ran": False}
    fetch = fetch or robot.fetch_daily_sales
    _mark(db, "sales_profit.last_attempt")
    db.commit()
    locs = stores(db)
    start, end = window(db)
    try:
        got = fetch([l.name for l in locs], start, end)
    except (robot.RobotLoginFailed, robot.RobotNotConfigured, robot.RobotError) as e:
        log.error("gross profit report failed: %s", e)
        return {"ran": True, "ok": False, "error": str(e)}
    days = 0
    for loc in locs:
        if loc.name in got:
            days += apply_rows(db, loc, got[loc.name])
    if len(got) == len(locs):
        _mark(db, "sales_profit.last_ok")
    db.commit()
    return {"ran": True, "ok": True, "stores": len(got), "days": days}
