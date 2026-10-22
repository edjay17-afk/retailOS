"""Administration: settings, locations, suppliers, product set-up, users."""
from __future__ import annotations

import csv
import io
import json
import math
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from .. import audit
from .. import settings_service as cfg
from ..models import Employee, Location, Product, Supplier, User, WebSession
from ..security import ROLES, ROLE_LABELS, hash_password, password_problem
from ..timeutil import now_utc
from .deps import Ctx, need, parse_float, redirect, render

router = APIRouter()
PAGE = 50


# ======================================================================================= settings and locations
@router.get("/settings")
def settings_page(c: Ctx = Depends(need("settings.manage"))):
    db = c.db
    locs = list(db.scalars(select(Location).order_by(Location.monitored.desc(), Location.name)))
    managers = list(db.scalars(select(User).where(User.active.is_(True), User.deleted_at.is_(None), User.role.in_(["store_manager", "management", "admin"]))))
    current = cfg.all_settings(db)
    rows = [(k, current[k], cfg.DEFAULTS[k][1], isinstance(current[k], (list, dict))) for k in cfg.DEFAULTS]
    return render(c, "settings.html", locs=locs, managers=managers, rows=rows)


@router.post("/settings/locations/{lid}")
def location_update(lid: int, monitored: str = Form(""), type: str = Form("store"), manager: int = Form(0), lead: str = Form(""), opens: str = Form("06:00"),
                    closes: str = Form("22:00"), csrf: str = Form(""), c: Ctx = Depends(need("settings.manage"))):
    c.check_csrf(csrf)
    loc = c.db.get(Location, lid)
    if loc is None:
        raise HTTPException(404)
    if type not in ("store", "warehouse"):
        raise HTTPException(400, "Bad type")
    before = {"monitored": loc.monitored, "type": loc.type, "manager": loc.manager_user_id, "lead": loc.lead_time_days}
    loc.monitored, loc.type, loc.manager_user_id = bool(monitored), type, manager or None
    loc.lead_time_days = parse_float(lead)
    if re.fullmatch(r"\d\d:\d\d", opens) and re.fullmatch(r"\d\d:\d\d", closes):
        loc.opens_at, loc.closes_at = opens, closes
    audit.log(c.db, actor_type="human", actor_id=c.user.id, action="location.update", entity_type="location", entity_id=lid, before=before,
              after={"monitored": loc.monitored, "type": loc.type, "manager": loc.manager_user_id, "lead": loc.lead_time_days})
    return redirect("/settings", ok=f"Saved {loc.name}")


@router.post("/settings/value")
def setting_update(key: str = Form(...), value: str = Form(""), csrf: str = Form(""), c: Ctx = Depends(need("settings.manage"))):
    c.check_csrf(csrf)
    if key not in cfg.DEFAULTS:
        raise HTTPException(400, "Unknown setting")
    default = cfg.DEFAULTS[key][0]
    try:
        if isinstance(default, bool):
            new = value.strip().lower() in ("1", "true", "yes", "on")
        elif isinstance(default, int):
            new = int(float(value))
        elif isinstance(default, float):
            new = float(value)
        elif isinstance(default, (list, dict)):
            new = json.loads(value)
            if type(new) is not type(default):
                raise ValueError("wrong JSON type")
        else:
            new = value.strip()
    except (ValueError, json.JSONDecodeError) as e:
        return redirect("/settings", err=f"{key}: invalid value ({e})")
    before = cfg.get(c.db, key)
    cfg.set_(c.db, key, new)
    audit.log(c.db, actor_type="human", actor_id=c.user.id, action="setting.update", entity_type="setting", entity_id=key, before={"value": before}, after={"value": new})
    return redirect("/settings", ok=f"Saved {key}")


# ======================================================================================= suppliers
def _supplier_code(supplier: Supplier) -> str:
    return supplier.storehub_id[-6:] if supplier.storehub_id else f"LOCAL-{supplier.id}"


def _supplier_clues(db) -> dict[int, dict]:
    """Facts that help someone recognise an unnamed supplier: how many POs, the last one, and what it usually supplies."""
    from ..models import PoLine, PurchaseOrder
    clues: dict[int, dict] = {}
    for sid, n, last, total in db.execute(select(PurchaseOrder.supplier_id, func.count(), func.max(PurchaseOrder.created_sh), func.sum(PurchaseOrder.total))
                                          .where(PurchaseOrder.supplier_id.is_not(None)).group_by(PurchaseOrder.supplier_id)):
        clues[sid] = {"pos": n, "last": last, "total": total or 0, "items": []}
    for sid, name, qty in db.execute(select(PurchaseOrder.supplier_id, Product.name, func.sum(PoLine.ordered_qty)).join(PoLine, PoLine.po_id == PurchaseOrder.id)
                                     .join(Product, Product.id == PoLine.product_id).where(PurchaseOrder.supplier_id.is_not(None))
                                     .group_by(PurchaseOrder.supplier_id, Product.name).order_by(func.sum(PoLine.ordered_qty).desc())):
        if sid in clues and len(clues[sid]["items"]) < 4:
            clues[sid]["items"].append(name)
    return clues


@router.get("/suppliers")
def suppliers_page(export: str = "", c: Ctx = Depends(need("settings.manage"))):
    db = c.db
    n_prod = dict(db.execute(select(Product.supplier_id, func.count()).where(Product.supplier_id.is_not(None)).group_by(Product.supplier_id)).all())
    sups = list(db.scalars(select(Supplier).order_by(Supplier.name)))
    clues = _supplier_clues(db)
    items: dict[int, list[str]] = {}  # every product assigned to each supplier (set in Product set-up)
    for sid_, pname in db.execute(select(Product.supplier_id, Product.name).where(Product.supplier_id.is_not(None), Product.in_api.is_(True)).order_by(func.lower(Product.name))):
        items.setdefault(sid_, []).append(pname)
    if export == "csv":
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Code", "Current name", "POs", "Last PO", "Usually supplies", "Real supplier name (fill in)"])
        for s_ in sups:
            k = clues.get(s_.id, {})
            w.writerow([_supplier_code(s_), s_.name, k.get("pos", 0), k["last"].strftime("%Y-%m-%d") if k.get("last") else "", "; ".join(k.get("items", [])),
                        s_.name if s_.name_source == "manual" else ""])
        return Response("﻿" + out.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="supplier-names.csv"'})
    return render(c, "suppliers.html", sups=sups, n_prod=n_prod, clues=clues, items=items, unnamed=sum(1 for s_ in sups if s_.name_source == "auto"))


@router.post("/suppliers/new")
def supplier_create(name: str = Form(...), contact: str = Form(""), lead: str = Form(""), review: str = Form(""),
                    min_value: str = Form(""), order_days: str = Form(""), csrf: str = Form(""),
                    c: Ctx = Depends(need("settings.manage"))):
    c.check_csrf(csrf)
    name = " ".join(name.split())
    if not name or len(name) > 160:
        return redirect("/suppliers", err="Supplier name must be between 1 and 160 characters.")
    if any(" ".join(s.name.split()).casefold() == name.casefold() for s in c.db.scalars(select(Supplier))):
        return redirect("/suppliers", err="That supplier already exists. Edit the existing supplier instead.")
    contact, order_days = contact.strip(), order_days.strip()
    if len(contact) > 200 or len(order_days) > 60:
        return redirect("/suppliers", err="Contact must be at most 200 characters and order days at most 60 characters.")
    try:
        values = [float(v.strip().replace(",", "")) if v.strip() else None for v in (lead, review, min_value)]
    except ValueError:
        return redirect("/suppliers", err="Lead time, review period and minimum order value must be non-negative numbers.")
    if any(v is not None and (not math.isfinite(v) or v < 0) for v in values):
        return redirect("/suppliers", err="Lead time, review period and minimum order value must be non-negative numbers.")
    s = Supplier(name=name, name_source="manual", contact=contact or None, order_days=order_days or None,
                 lead_time_days=values[0], review_period_days=values[1], min_order_value=values[2], active=True)
    c.db.add(s)
    c.db.flush()
    audit.log(c.db, actor_type="human", actor_id=c.user.id, action="supplier.create", entity_type="supplier", entity_id=s.id,
              after={"name": s.name, "lead": s.lead_time_days, "review": s.review_period_days, "min": s.min_order_value,
                     "contact": s.contact, "order_days": s.order_days, "active": s.active})
    return redirect("/suppliers", ok=c.t("Added supplier {name}", name=s.name))


@router.post("/suppliers/import-po-export")
async def suppliers_import_po_export(file: UploadFile = File(...), csrf: str = Form(""), c: Ctx = Depends(need("settings.manage"))):
    """Name suppliers from the StoreHub back-office Purchase Orders export, matching on the PO number."""
    from ..services import supplier_names
    c.check_csrf(csrf)
    text = (await file.read(30_000_000)).decode("utf-8-sig", errors="replace")
    try:
        r = supplier_names.apply_po_export(c.db, text, c.user.id)
    except ValueError as e:
        return redirect("/suppliers", err=str(e))
    msg = f"Named {r['suppliers_named']} supplier(s) from {r['pos_matched']} matching purchase orders"
    if r["still_unnamed"]:
        msg += f"; {r['still_unnamed']} still unnamed (their POs are not in this file)"
    if r["conflicts"]:
        msg += f"; skipped {len(r['conflicts'])} with conflicting names"
    return redirect("/suppliers", ok=msg)


@router.post("/suppliers/import")
async def suppliers_import(file: UploadFile = File(...), csrf: str = Form(""), c: Ctx = Depends(need("settings.manage"))):
    """Fill in supplier names by their exported code (StoreHub suffix or LOCAL-id)."""
    c.check_csrf(csrf)
    raw = (await file.read(1_000_000)).decode("utf-8-sig", errors="replace")
    rows = [r for r in csv.reader(io.StringIO(raw)) if r and any(x.strip() for x in r)]
    if rows and rows[0] and rows[0][0].strip().lower() in ("code", "id", "supplier id"):
        rows = rows[1:]
    by_code = {_supplier_code(s_).lower(): s_ for s_ in c.db.scalars(select(Supplier))}
    done, skipped = 0, []
    for r in rows:
        code = r[0].strip().lower()
        name = (r[5] if len(r) >= 6 else r[1] if len(r) >= 2 else "").strip()
        s_ = by_code.get(code) or (by_code.get(code[-6:]) if code and not code.startswith("local-") else None)
        if s_ is None or not name:
            if name or code:
                skipped.append(code or "?")
            continue
        if s_.name != name or s_.name_source != "manual":
            audit.log(c.db, actor_type="human", actor_id=c.user.id, action="supplier.rename", entity_type="supplier", entity_id=s_.id,
                      before={"name": s_.name}, after={"name": name})
            s_.name, s_.name_source = name[:160], "manual"
            done += 1
    msg = f"Named {done} supplier(s)" + (f"; skipped {len(skipped)} row(s) with an unknown code or no name" if skipped else "")
    return redirect("/suppliers", ok=msg)


@router.post("/suppliers/{sid}")
def supplier_update(sid: int, name: str = Form(...), lead: str | None = Form(None), review: str | None = Form(None), min_value: str | None = Form(None),
                    order_days: str | None = Form(None), contact: str = Form(""), active: str = Form(""), csrf: str = Form(""),
                    c: Ctx = Depends(need("settings.manage"))):
    c.check_csrf(csrf)
    s = c.db.get(Supplier, sid)
    if s is None:
        raise HTTPException(404)
    before = {"name": s.name, "lead": s.lead_time_days, "review": s.review_period_days, "min": s.min_order_value}
    s.name, s.name_source = name.strip() or s.name, "manual"
    if lead is not None:  # only a form that sends these fields changes them (this page no longer does)
        s.lead_time_days = parse_float(lead)
    if review is not None:
        s.review_period_days = parse_float(review)
    if min_value is not None:
        s.min_order_value = parse_float(min_value)
    if order_days is not None:
        s.order_days = order_days.strip() or None
    s.contact, s.active = contact.strip() or None, bool(active)
    audit.log(c.db, actor_type="human", actor_id=c.user.id, action="supplier.update", entity_type="supplier", entity_id=sid, before=before,
              after={"name": s.name, "lead": s.lead_time_days, "review": s.review_period_days, "min": s.min_order_value})
    return redirect("/suppliers", ok=f"Saved {s.name}")


# ======================================================================================= product set-up
@router.get("/products")
def products_page(q: str = "", filt: str = "", page: int = 1, export: str = "", c: Ctx = Depends(need("settings.manage"))):
    db = c.db
    stmt = select(Product).where(Product.in_api.is_(True), Product.track_stock.is_(True)).options(selectinload(Product.supplier)).order_by(Product.name)
    if q:
        stmt = stmt.where(Product.name.like(f"%{q}%") | Product.barcode.like(f"%{q}%") | Product.sku.like(f"%{q}%"))
    if filt == "no_supplier":
        stmt = stmt.where(Product.supplier_id.is_(None))
    elif filt == "no_case_pack":
        stmt = stmt.where(Product.case_pack.is_(None))
    elif filt == "inferred":
        stmt = stmt.where(Product.supplier_source == "po_history")
    if export == "csv":
        out = io.StringIO()
        w = csv.writer(out)
        vat = 1 + float(cfg.get(db, "product.vat_rate") or 0) / 100
        # the same columns as the table on the page; "StoreHub ID" stays first because the upload uses it to find each product
        w.writerow(["StoreHub ID", "Name", "Barcode", "Cost", "Retail price", "Supplier", "Case pack", "Perishable", "Replenish"])
        for p in db.scalars(stmt):
            w.writerow([p.storehub_id, p.name, p.barcode or "", f"{p.cost:.2f}" if p.cost is not None else "", f"{p.unit_price * vat:.2f}" if p.unit_price is not None else "",
                        p.supplier.name if p.supplier else "", f"{p.case_pack:g}" if p.case_pack else "", "yes" if p.perishable else "", "yes" if p.replenish_enabled else "no"])
        return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="product-setup.csv"'})
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = max(page, 1)
    prods = list(db.scalars(stmt.limit(PAGE).offset((page - 1) * PAGE)))
    sups = list(db.scalars(select(Supplier).where(Supplier.active.is_(True)).order_by(Supplier.name)))
    return render(c, "products.html", vat=1 + float(cfg.get(db, "product.vat_rate") or 0) / 100, prods=prods, sups=sups, q=q, filt=filt, page=page, total=total)


@router.post("/products/{pid:int}")
def product_update(pid: int, supplier: int = Form(0), case_pack: str = Form(""), order_multiple: str | None = Form(None), moq: str | None = Form(None), perishable: str = Form(""),
                   safety_days: str | None = Form(None), max_stock: str | None = Form(None), replenish: str = Form(""), back: str = Form("/products"), csrf: str = Form(""),
                   request: Request = None, c: Ctx = Depends(need("settings.manage"))):
    c.check_csrf(csrf)
    p = c.db.get(Product, pid)
    if p is None:
        raise HTTPException(404)
    before = {"supplier": p.supplier_id, "case_pack": p.case_pack, "order_multiple": p.order_multiple, "moq": p.moq, "max_stock": p.max_stock}
    if (supplier or None) != p.supplier_id:
        p.supplier_id, p.supplier_source = supplier or None, ("manual" if supplier else None)
    p.case_pack = parse_float(case_pack)
    if order_multiple is not None:  # only a form that sends these fields changes them (the Product settings page no longer shows them)
        p.order_multiple = parse_float(order_multiple)
    if moq is not None:
        p.moq = parse_float(moq)
    if safety_days is not None:
        p.safety_days = parse_float(safety_days)
    if max_stock is not None:
        p.max_stock = parse_float(max_stock)
    p.perishable, p.replenish_enabled = bool(perishable), bool(replenish)
    audit.log(c.db, actor_type="human", actor_id=c.user.id, action="product.setup", entity_type="product", entity_id=pid, before=before,
              after={"supplier": p.supplier_id, "case_pack": p.case_pack, "order_multiple": p.order_multiple, "moq": p.moq, "max_stock": p.max_stock})
    if request is not None and request.headers.get("x-requested-with") == "fetch":  # saved from the page without reloading it
        return JSONResponse({"ok": True, "name": p.name})
    return redirect(back, ok=f"Saved {p.name}")


@router.post("/products/import")
async def products_import(file: UploadFile = File(...), csrf: str = Form(""), c: Ctx = Depends(need("settings.manage"))):
    c.check_csrf(csrf)
    raw = (await file.read(5_000_000)).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    need_cols = {"StoreHub ID"}
    if not reader.fieldnames or not need_cols <= set(reader.fieldnames):
        return redirect("/products", err="The file must have a 'StoreHub ID' column. Export the current set-up first and edit that file.")
    sups = {s.name.lower(): s for s in c.db.scalars(select(Supplier))}
    changed = skipped = 0
    problems: list[str] = []
    for n, row in enumerate(reader, start=2):
        p = c.db.scalar(select(Product).where(Product.storehub_id == (row.get("StoreHub ID") or "").strip()))
        if p is None:
            skipped += 1
            continue
        try:
            for col, attr in (("Case pack", "case_pack"), ("Order multiple", "order_multiple"), ("MOQ", "moq"), ("Safety days", "safety_days"), ("Min stock", "min_stock"), ("Max stock", "max_stock")):
                if col in row and (row[col] or "").strip() != "":
                    v = float(row[col])
                    if v < 0:
                        raise ValueError(f"{col} cannot be negative")
                    setattr(p, attr, v)
            if "Perishable" in row and (row["Perishable"] or "").strip():
                p.perishable = row["Perishable"].strip().lower() in ("yes", "1", "true", "y")
            if "Replenish" in row and (row["Replenish"] or "").strip():
                p.replenish_enabled = row["Replenish"].strip().lower() in ("yes", "1", "true", "y")
            if "Supplier" in row and (row["Supplier"] or "").strip():
                s = sups.get(row["Supplier"].strip().lower())
                if s is None:
                    problems.append(f"line {n}: supplier '{row['Supplier']}' does not exist")
                else:
                    p.supplier_id, p.supplier_source = s.id, "manual"
            changed += 1
        except ValueError as e:
            problems.append(f"line {n}: {e}")
    audit.log(c.db, actor_type="human", actor_id=c.user.id, action="product.bulk_import", entity_type="product", after={"rows_changed": changed, "skipped": skipped, "problems": problems[:20]})
    msg = f"Imported {changed} product(s), skipped {skipped}."
    if problems:
        return redirect("/products", err=msg + " Problems: " + "; ".join(problems[:5]))
    return redirect("/products", ok=msg)


@router.get("/users")
def users_page(loc: str = "", q: str = "", c: Ctx = Depends(need("users.manage"))):
    db = c.db
    everyone = list(db.scalars(select(User).where(User.deleted_at.is_(None)).order_by(User.username)))
    active_admins = sum(u.role == "admin" and u.active for u in everyone)
    users = everyone
    if loc == "all":  # people who can see every location
        users = [u for u in users if not u.location_ids]
    elif loc.isdigit():
        users = [u for u in users if u.location_ids and int(loc) in u.location_ids]
    if q.strip():
        needle = q.strip().lower()
        users = [u for u in users if needle in u.username.lower() or needle in (u.full_name or "").lower()]
    return render(c, "users.html", users=users, active_admins=active_admins, total_users=len(everyone), loc=loc, q=q.strip(),
                  locations=list(db.scalars(select(Location).where(Location.monitored.is_(True)))),
                  employees=list(db.scalars(select(Employee).order_by(Employee.first_name))), roles=ROLES)


@router.post("/users/new")
def user_create(username: str = Form(...), full_name: str = Form(""), password: str = Form(""), role: str = Form(...), location_ids: list[int] = Form(default=[]),
                employee: str = Form(""), csrf: str = Form(""), c: Ctx = Depends(need("users.manage"))):
    c.check_csrf(csrf)
    uname = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9._-]{3,40}", uname):
        return redirect("/users", err="Username: 3 to 40 letters, numbers, dots, dashes or underscores")
    if role not in ROLES:
        raise HTTPException(400, "Bad role")
    problem = password_problem(password)
    if problem:
        return redirect("/users", err=problem)
    if c.db.scalar(select(User).where(User.username == uname)):
        return redirect("/users", err="That username already exists")
    u = User(username=uname, full_name=full_name.strip(), role=role, password_hash=hash_password(password), password_plain=password, must_change_password=False,
             location_ids=sorted(set(location_ids)) or None, storehub_employee_id=employee or None)
    c.db.add(u)
    c.db.flush()
    audit.log(c.db, actor_type="human", actor_id=c.user.id, action="user.create", entity_type="user", entity_id=u.id, after={"username": uname, "role": role})
    return redirect("/users", ok=f"Created {uname}")


@router.post("/users/{uid}")
def user_update(uid: int, role: str = Form(...), full_name: str = Form(""), new_password: str = Form(""), location_ids: list[int] = Form(default=[]), employee: str = Form(""),
                active: str = Form(""), csrf: str = Form(""), c: Ctx = Depends(need("users.manage"))):
    c.check_csrf(csrf)
    u = c.db.get(User, uid)
    if u is None or u.deleted_at is not None:
        raise HTTPException(404)
    if role not in ROLES:
        raise HTTPException(400, "Bad role")
    is_active = bool(active)
    if (u.role == "admin" and (role != "admin" or not is_active)):
        others = c.db.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.active.is_(True), User.deleted_at.is_(None), User.id != uid)) or 0
        if others == 0:
            return redirect("/users", err="There must always be at least one active admin")
    before = {"role": u.role, "active": u.active, "locations": u.location_ids}
    u.role, u.full_name, u.active = role, full_name.strip(), is_active
    u.location_ids, u.storehub_employee_id = sorted(set(location_ids)) or None, employee or None
    pw_changed = bool(new_password.strip())
    if pw_changed:
        problem = password_problem(new_password)
        if problem:
            return redirect("/users", err=problem)
        u.password_hash, u.password_plain, u.must_change_password, u.failed_logins, u.locked_until = hash_password(new_password), new_password, False, 0, None
    audit.log(c.db, actor_type="human", actor_id=c.user.id, action="user.update" + (".set_password" if pw_changed else ""), entity_type="user", entity_id=uid,
              before=before, after={"role": role, "active": u.active, "locations": u.location_ids})
    return redirect("/users", ok=f"Saved {u.username}")


@router.post("/users/{uid}/delete")
def user_delete(uid: int, csrf: str = Form(""), c: Ctx = Depends(need("users.manage"))):
    c.check_csrf(csrf)
    u = c.db.get(User, uid)
    if u is None or u.deleted_at is not None:
        raise HTTPException(404)
    if u.role == "admin" and u.active:
        others = c.db.scalar(select(func.count()).select_from(User).where(
            User.role == "admin", User.active.is_(True), User.deleted_at.is_(None), User.id != uid)) or 0
        if not others:
            return redirect("/users", err=c.t("There must always be at least one active admin"))
    if uid == c.user.id:
        return redirect("/users", err=c.t("You cannot delete your own account."))
    before = {"username": u.username, "role": u.role, "active": u.active}
    u.active, u.deleted_at, u.password_plain = False, now_utc(), None
    c.db.execute(delete(WebSession).where(WebSession.user_id == uid))
    audit.log(c.db, actor_type="human", actor_id=c.user.id, action="user.delete", entity_type="user", entity_id=uid,
              before=before, after={"active": False, "deleted_at": u.deleted_at.isoformat()},
              reason="Account access removed; historical operational records retained")
    return redirect("/users", ok=c.t("Deleted user {name}", name=u.username))
