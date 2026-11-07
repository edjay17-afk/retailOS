"""7-Eleven order suggestions, from the OR Sales Monitor history (units sold per product per day) and the Bad Merchandise slips.

There is no stock-on-hand report for 7-Eleven yet, so this is a SALES-ONLY suggestion (the same idea as the Loyverse one):
    per day  = units sold in the look-back window / trading days of the store in that window (days with any sales; a product that sold nothing on a trading day counts as 0)
    expected = per day x cover days
    order    = expected rounded up, or nothing when the expected sales are under half a unit (a slow mover)
Nothing is hidden or adjusted silently: products that need a human look (rising or falling sales, a lot of waste, very few sales) are flagged with the reason."""
from __future__ import annotations

import math
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import SevenBadItem, SevenBadSlip, SevenSaleLine, Setting

DEFAULT_COVER_DAYS = 3
DEFAULT_LOOKBACK_DAYS = 28
RECENT_DAYS = 7
WASTE_DAYS = 60
EXCLUDED = ("EX-", "30-Deposit")        # expense items (stickers, supplies) and bottle deposits are not ordered stock
RISING, FALLING = 1.5, 0.5             # the last 7 days sell this much more / less than the window average
HIGH_WASTE = 0.15                       # waste units are at least this share of units sold
MIN_TREND_PER_DAY = 1.0                 # the trend flags are for products selling at least one a day
FEW_DAYS = 3                            # sold on fewer days than this in the window


def _int_setting(db: Session, key: str, default: int, low: int, high: int) -> int:
    row = db.get(Setting, key)
    try:
        return min(max(int(str(row.value)), low), high) if row and row.value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def settings(db: Session) -> dict:
    return {"cover_days": _int_setting(db, "seven.cover_days", DEFAULT_COVER_DAYS, 1, 14), "lookback_days": _int_setting(db, "seven.lookback_days", DEFAULT_LOOKBACK_DAYS, 7, 120)}


def save_settings(db: Session, cover_days: int, lookback_days: int) -> None:
    for key, value in (("seven.cover_days", min(max(cover_days, 1), 14)), ("seven.lookback_days", min(max(lookback_days, 7), 120))):
        row = db.get(Setting, key) or Setting(key=key, value="")
        row.value = str(value)
        db.add(row)


def last_day(db: Session) -> date | None:
    return db.scalar(select(func.max(SevenSaleLine.sale_date)))


def suggest(db: Session, *, cover_days: int | None = None, lookback_days: int | None = None, as_of: date | None = None) -> dict:
    """The suggestion for every product that sold in the window. Returns {rows, meta}; rows are sorted by what to order, biggest first."""
    cfg = settings(db)
    cover = cover_days or cfg["cover_days"]
    look = lookback_days or cfg["lookback_days"]
    as_of = as_of or last_day(db)
    if as_of is None:
        return {"rows": [], "meta": {"as_of": None, "cover_days": cover, "lookback_days": look, "trading_days": 0, "warnings": ["No sales have been uploaded yet."]}}
    start = as_of - timedelta(days=look - 1)
    recent_start = as_of - timedelta(days=RECENT_DAYS - 1)
    trading = db.scalar(select(func.count(func.distinct(SevenSaleLine.sale_date))).where(SevenSaleLine.sale_date >= start, SevenSaleLine.sale_date <= as_of)) or 0
    recent_trading = db.scalar(select(func.count(func.distinct(SevenSaleLine.sale_date))).where(SevenSaleLine.sale_date >= recent_start, SevenSaleLine.sale_date <= as_of)) or 0
    warnings = []
    if trading < min(look, 7):
        warnings.append(f"Only {trading} trading days of sales are in the last {look} days; upload the missing days for a reliable suggestion.")
    qty_recent = func.sum(func.iif(SevenSaleLine.sale_date >= recent_start, SevenSaleLine.qty, 0))
    agg = db.execute(select(SevenSaleLine.item_code, func.max(SevenSaleLine.barcode), func.max(SevenSaleLine.name), func.max(SevenSaleLine.category), func.sum(SevenSaleLine.qty),
                            qty_recent, func.count(func.distinct(SevenSaleLine.sale_date)), func.max(SevenSaleLine.sale_date), func.sum(SevenSaleLine.amount))
                     .where(SevenSaleLine.sale_date >= start, SevenSaleLine.sale_date <= as_of).group_by(SevenSaleLine.item_code)).all()
    waste_since = as_of - timedelta(days=WASTE_DAYS - 1)
    waste = {code: -(q or 0) for code, q in db.execute(select(SevenBadItem.item_code, func.sum(SevenBadItem.qty)).join(SevenBadSlip, SevenBadSlip.id == SevenBadItem.slip_id)
                                                          .where(SevenBadSlip.slip_date >= waste_since, SevenBadSlip.slip_date <= as_of).group_by(SevenBadItem.item_code))}
    rows = []
    for code, barcode, name, category, qty, qty7, days_sold, last_sold, amount in agg:
        if any((category or "").startswith(x) for x in EXCLUDED) or (qty or 0) <= 0:
            continue
        per_day = qty / trading if trading else 0.0
        per_day7 = (qty7 or 0) / recent_trading if recent_trading else 0.0
        expected = per_day * cover
        order = 0 if expected < 0.5 else math.ceil(expected - 1e-9)
        w = waste.get(code, 0.0)
        flags = []
        if per_day >= MIN_TREND_PER_DAY and per_day7 >= per_day * RISING and recent_trading >= 3:     # slower products bounce around by chance, so they are not flagged
            flags.append("Selling faster lately")
        if per_day >= MIN_TREND_PER_DAY and per_day7 <= per_day * FALLING and recent_trading >= 3:
            flags.append("Selling slower lately")
        if w > 0 and qty and w / qty >= HIGH_WASTE:
            flags.append("High waste")
        if days_sold < FEW_DAYS and order > 0:
            flags.append("Few sales")
        if order == 0:
            flags.append("Slow mover")
        rows.append({"code": code, "barcode": barcode, "name": name, "category": category, "units": qty, "units7": qty7 or 0, "per_day": per_day, "per_day7": per_day7, "days_sold": days_sold,
                     "last_sold": last_sold, "amount": amount or 0, "waste": w, "expected": expected, "order": order, "flags": flags, "review": any(f in ("Selling faster lately", "Selling slower lately", "High waste", "Few sales") for f in flags)})
    rows.sort(key=lambda r: (-r["order"], -r["per_day"], r["name"]))
    return {"rows": rows, "meta": {"as_of": as_of, "start": start, "cover_days": cover, "lookback_days": look, "trading_days": trading, "warnings": warnings,
                                   "to_order": sum(1 for r in rows if r["order"]), "units": sum(r["order"] for r in rows), "review": sum(1 for r in rows if r["review"] and r["order"])}}
