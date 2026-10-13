"""Time helpers. All timestamps are stored as naive UTC; business dates use Asia/Manila (UTC+8)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .config import get_config


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def tz() -> timezone:
    return timezone(timedelta(hours=get_config().tz_offset_hours))


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 string into naive UTC."""
    if not value:
        return None
    s = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def to_local(dt: datetime) -> datetime:
    return (dt.replace(tzinfo=timezone.utc)).astimezone(tz()).replace(tzinfo=None)


def business_date(dt: datetime) -> date:
    """Business date (Asia/Manila) of a naive-UTC timestamp."""
    return to_local(dt).date()


def today_local() -> date:
    return business_date(now_utc())


def local_day_bounds_utc(d: date) -> tuple[datetime, datetime]:
    """Naive-UTC [start, end) for a local business date."""
    start_local = datetime(d.year, d.month, d.day, tzinfo=tz())
    start = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start, start + timedelta(days=1)


def local_iso(d: date, end: bool = False) -> str:
    """ISO string with explicit +08:00 offset, for StoreHub from/to parameters."""
    off = get_config().tz_offset_hours
    suffix = f"{'+' if off >= 0 else '-'}{abs(off):02d}:00"
    return f"{d.isoformat()}T{'23:59:59.999' if end else '00:00:00'}{suffix}"


def fmt_local(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return to_local(dt).strftime(fmt) if dt else ""


def fmt_clock(dt: datetime | None) -> str:
    """A clock time that cannot be misread: 12:00 PM is noon, 12:00 AM is midnight (never a bare 12:00)."""
    if not dt:
        return ""
    t = to_local(dt)
    return f"{(t.hour % 12) or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"
