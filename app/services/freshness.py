"""Data freshness: tells every screen and every rule whether a domain can be trusted right now."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import settings_service as cfg
from ..models import SyncState
from ..timeutil import now_utc

DOMAINS = [
    ("sales", "Sales", "fresh.sales_min", True),
    ("inventory", "Inventory", "fresh.inventory_min", True),
    ("purchase_orders", "Purchase orders", "fresh.purchase_orders_min", True),
    ("products", "Products", "fresh.products_min", False),
    ("people", "Staff and timesheets", "fresh.people_min", False),
]


def freshness(db: Session) -> list[dict]:
    states = {s.domain: s for s in db.scalars(select(SyncState))}
    now = now_utc()
    out = []
    for key, label, limit_key, critical in DOMAINS:
        st = states.get(key)
        limit = int(cfg.get(db, limit_key))
        last = st.last_success_at if st else None
        if last is None:
            status, age = "never", None
        else:
            age = (now - last).total_seconds() / 60
            status = "fresh" if age <= limit else "stale"
        out.append({"domain": key, "label": label, "critical": critical, "status": status, "last_success": last,
                    "age_min": age, "limit_min": limit, "failures": st.consecutive_failures if st else 0,
                    "last_error": st.last_error if st else None})
    return out


def blocking_domains(db: Session, needed: tuple[str, ...] = ("sales", "inventory", "purchase_orders")) -> list[dict]:
    """Domains the replenishment engine needs that are stale or never synced."""
    return [f for f in freshness(db) if f["domain"] in needed and f["status"] != "fresh"]


def is_fresh(db: Session, domain: str) -> bool:
    return any(f["domain"] == domain and f["status"] == "fresh" for f in freshness(db))
