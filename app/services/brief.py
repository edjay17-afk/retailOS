"""Daily management brief: only what needs attention today, ranked, each with its evidence.

Every statement is either a FACT (read from the data), a CALCULATION (derived by code) or a RECOMMENDATION. Nothing is invented.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import settings_service as cfg
from ..models import (DqEvent, Inspection, InventoryCurrent, Issue, Location, Recommendation, SalesDay)
from ..timeutil import fmt_clock, fmt_local, now_utc, today_local
from . import expiry, freshness, purchasing

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def build(db: Session, location_ids: list[int]) -> dict:
    today = today_local()
    items: list[dict] = []
    locs = {l.id: l for l in db.scalars(select(Location).where(Location.id.in_(location_ids or [0])))}

    # 1. data freshness (nothing else can be trusted if this is red)
    bad = [f for f in freshness.freshness(db) if f["status"] != "fresh" and f["critical"]]
    if bad:
        items.append({"priority": "high", "title": "Some data is stale", "location": None,
                      "facts": [f"{f['label']}: " + ("never synced" if f['age_min'] is None else f"last synced {f['age_min']:.0f} min ago (limit {f['limit_min']})") for f in bad],
                      "calculations": [], "recommendation": "Check the Sync page. Replenishment and cash checks pause until data is fresh.", "link": "/sync"})

    # 2. open urgent and overdue issues
    q = select(Issue).where(Issue.status.in_(("open", "acknowledged", "in_progress", "resolved")))
    urgent = [i for i in db.scalars(q) if (i.location_id in locs or i.location_id is None) and (i.severity in ("P0", "P1") or i.due_at < now_utc())]
    if urgent:
        p0p1 = [i for i in urgent if i.severity in ("P0", "P1")]
        over = [i for i in urgent if i.due_at < now_utc()]
        items.append({"priority": "high" if p0p1 else "medium", "title": f"{len(urgent)} issue(s) need attention", "location": None,
                      "facts": [f"{len(p0p1)} are P0/P1, {len(over)} are past their due time"] + [f"#{i.id} [{i.severity}] {i.title}" for i in sorted(urgent, key=lambda x: (x.severity, x.due_at))[:5]],
                      "calculations": [], "recommendation": "Open the issue queue and assign or resolve the oldest first.", "link": "/issues"})

    # 3. replenishment waiting for a decision
    recs = list(db.scalars(select(Recommendation).where(Recommendation.business_date == today, Recommendation.status.in_(("pending", "needs_review")),
                                                        Recommendation.location_id.in_(location_ids or [0]))))
    if recs:
        pend = [r for r in recs if r.status == "pending"]
        nr = [r for r in recs if r.status == "needs_review"]
        at_risk = [r for r in recs if r.days_cover is not None and r.days_cover < max(r.lead_time, 1)]
        items.append({"priority": "high" if at_risk else "medium", "title": f"{len(pend)} order suggestion(s) ready, {len(nr)} need review", "location": None,
                      "facts": [f"Suggested value of ready items: {sum(r.value for r in pend):,.2f}",
                                f"{len(at_risk)} of these are for items with less stock than their lead time needs (including items whose stock count is negative)"],
                      "calculations": ["Suggestions use average daily sales x (lead time + review period) + safety stock, less stock on hand and incoming"],
                      "recommendation": "Review the suggestions; approve, modify or reject each with a reason.", "link": "/replenishment"})

    # 4. purchase orders far past their date
    _, stale = purchasing.incoming_map(db)
    if stale:
        items.append({"priority": "medium", "title": f"{len(stale)} open PO(s) are far past their date", "location": None,
                      "facts": [f"PO #{p.sh_po_id} ({p.supplier.name if p.supplier else 'no supplier'}) is {p.status}" for p in stale[:5]],
                      "calculations": [f"They are not counted as incoming stock (limit {cfg.get(db, 'po.stale_days')} days past the date)"],
                      "recommendation": "Receive them in StoreHub if the goods arrived, or cancel them.", "link": "/purchase-orders"})

    # 5. expiry
    warn = cfg.get(db, "expiry.warn_days")
    soon = [b for b in expiry.active_batches(db, location_ids) if (b.expiry_date - today).days <= min(warn)]
    if soon:
        items.append({"priority": "high", "title": f"{len(soon)} batch(es) expire within {min(warn)} days", "location": None,
                      "facts": [f"{b.product.name} at {b.location.name}: {b.qty:g} units, expiry {b.expiry_date}" for b in soon[:5]],
                      "calculations": [], "recommendation": "Markdown, pull from the shelf or return to the warehouse.", "link": "/expiry"})

    # 6. inspections missed or late today
    insp = list(db.scalars(select(Inspection).where(Inspection.business_date == today, Inspection.location_id.in_(location_ids or [0]),
                                                    Inspection.status.in_(("missed", "pending", "in_progress")), Inspection.due_at < now_utc())))
    if insp:
        items.append({"priority": "medium", "title": f"{len(insp)} checklist(s) are late or missed today", "location": None,
                      "facts": [f"{i.template.name} at {i.location.name}" for i in insp[:5]], "calculations": [],
                      "recommendation": "Ask the shift lead to complete them.", "link": "/inspections"})

    # 6b. checklists still to do today (not yet due) — the daily status store staff need up front
    todo = list(db.scalars(select(Inspection).where(Inspection.business_date == today, Inspection.location_id.in_(location_ids or [0]),
                                                    Inspection.status.in_(("pending", "in_progress")), Inspection.due_at >= now_utc()).order_by(Inspection.due_at)))
    if todo:
        items.append({"priority": "low", "title": f"{len(todo)} checklist(s) still to do today", "location": None,
                      "facts": [f"{i.template.name} at {i.location.name}, due {fmt_clock(i.due_at)}" for i in todo[:5]], "calculations": [],
                      "recommendation": "Complete them before their due time.", "link": "/inspections"})

    # 7. negative stock
    neg = db.execute(select(InventoryCurrent.location_id, func.count()).where(InventoryCurrent.on_hand < 0, InventoryCurrent.location_id.in_(location_ids or [0]))
                     .group_by(InventoryCurrent.location_id)).all()
    if neg:
        total = sum(n for _, n in neg)
        items.append({"priority": "medium" if total < 50 else "high", "title": f"{total} item(s) show negative stock", "location": None,
                      "facts": [f"{locs[l].name}: {n}" for l, n in neg if l in locs],
                      "calculations": ["Negative stock usually means deliveries or transfers were not recorded in StoreHub"],
                      "recommendation": "Start a targeted cycle count; the count list begins with the negative items.", "link": "/counts"})

    # yesterday's sales (facts and one calculation)
    y = today - timedelta(days=1)
    sales = []
    for lid, loc in locs.items():
        if loc.type != "store":
            continue
        day = db.get(SalesDay, (lid, y))
        hist = [r.net_sales for r in db.scalars(select(SalesDay).where(SalesDay.location_id == lid, SalesDay.business_date >= y - timedelta(days=14), SalesDay.business_date < y))]
        if day:
            avg = sum(hist) / len(hist) if hist else None
            sales.append((loc.name, day.net_sales, day.tx_count, avg))
    items.sort(key=lambda i: PRIORITY_ORDER[i["priority"]])
    return {"date": today, "items": items, "sales": sales, "all_clear": not items}
