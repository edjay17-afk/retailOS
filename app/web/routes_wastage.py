"""Wastage: record what was thrown away or lost; every row is mirrored to the Lark Wastage Record table."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ..models import User, WastageEntry, Product
from ..services import wastage as svc
from ..timeutil import today_local
from .deps import Ctx, need, parse_date, parse_float, redirect, render

router = APIRouter()


@router.get("/wastage")
def wastage_page(loc: int = 0, days: int = 30, c: Ctx = Depends(need("wastage.record"))):
    db = c.db
    ids = c.location_ids or [0]
    if loc:
        c.check_loc(loc)
        ids = [loc]
    days = days if days in (7, 30, 90) else 30
    since = today_local() - timedelta(days=days - 1)
    q = select(WastageEntry).where(WastageEntry.location_id.in_(ids), WastageEntry.entry_date >= since).options(selectinload(WastageEntry.product), selectinload(WastageEntry.location))
    rows = list(db.scalars(q.order_by(WastageEntry.entry_date.desc(), WastageEntry.id.desc()).limit(300)))
    users = {u.id: (u.full_name or u.username) for u in db.scalars(select(User))}
    total_qty = db.scalar(select(func.coalesce(func.sum(WastageEntry.qty), 0)).where(WastageEntry.location_id.in_(ids), WastageEntry.entry_date >= since)) or 0
    return render(c, "wastage.html", rows=rows, users=users, locations=c.locations(), loc=loc, days=days, reasons=svc.REASONS, today=today_local(), total_qty=total_qty)


@router.get("/wastage/products")
def wastage_products(q: str = "", c: Ctx = Depends(need("wastage.record"))):
    from ..services.product_lookup import search
    return search(c.db, q)


@router.post("/wastage/add")
def wastage_add(location_id: int = Form(...), product: str = Form(""), qty: str = Form(""), reason: str = Form(""), on: str = Form(""), csrf: str = Form(""),
                product_id: int = Form(0), c: Ctx = Depends(need("wastage.record"))):
    c.check_csrf(csrf)
    c.check_loc(location_id)
    try:
        p = c.db.get(Product, product_id) if product_id else svc.find_product(c.db, product)
        if p is None or not p.in_api:
            raise svc.WastageError("Choose a product currently in StoreHub")
        svc.add(c.db, c.user, location_id=location_id, product=p, qty=parse_float(qty) or 0, reason=reason, on=parse_date(on))
    except svc.WastageError as e:
        c.db.rollback()
        return redirect(f"/wastage?loc={location_id}", err=str(e))
    return redirect(f"/wastage?loc={location_id}", ok=f"Recorded: {p.name}")
