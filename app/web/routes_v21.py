"""V2.1: the traceability / operating metrics page (spec section 9). The repair queue page was removed in November 2026; its calculations stay in v21_metrics."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..models import InventoryEvent, User
from ..services import inventory_events as iev, v21_metrics
from .deps import Ctx, need, render

router = APIRouter()


@router.get("/traceability")
def traceability(days: int = 30, status: str = "open", c: Ctx = Depends(need("traceability.view"))):
    days = days if days in (7, 30, 90) else 30
    ids = c.location_ids or None
    m = v21_metrics.operating_metrics(c.db, days, ids)
    q = select(InventoryEvent).options(selectinload(InventoryEvent.product), selectinload(InventoryEvent.location))
    if ids is not None:
        q = q.where(InventoryEvent.location_id.in_(ids or [0]))
    if status == "open":
        q = q.where(InventoryEvent.reconciliation_status.in_(iev.UNRESOLVED))
    events = list(c.db.scalars(q.order_by(InventoryEvent.recorded_at.desc()).limit(150)))
    users = {u.id: (u.full_name or u.username) for u in c.db.scalars(select(User))}
    return render(c, "traceability.html", m=m, events=events, users=users, days=days, status=status)
