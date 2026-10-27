"""Login, dashboard, notifications, sync status, data quality, audit, health, uploads."""
from __future__ import annotations

import threading
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .. import audit, jobs
from ..config import get_config
from ..models import (ApiErrorLog, AuditEvent, DqEvent, Inspection, Issue, Location, Notification, Recommendation, SalesDay, SyncRun, User)
from ..security import can_open, create_session, hash_password, load_session, password_problem, verify_password
from ..services import inspections, backup, brief, dq, freshness, notifications, replenishment
from ..timeutil import now_utc, today_local
from .deps import COOKIE, Ctx, ctx_dep, get_db, need, redirect, render, render_public

router = APIRouter()


# ----------------------------------------------------------------------------------------- auth
@router.get("/lang/{code}")
def set_language(code: str, request: Request, back: str = "/"):
    """Language preference toggle (English / 中文). Public: the cookie holds no business data."""
    from .. import i18n
    dest = back if back.startswith("/") and not back.startswith("//") else "/"
    resp = RedirectResponse(dest, status_code=303)
    resp.set_cookie(i18n.COOKIE_NAME, i18n.norm(code), max_age=365 * 24 * 3600, samesite="lax")
    return resp


@router.get("/login")
def login_page(request: Request, next: str = "/"):
    return render_public(request, "login.html", next=next)


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/"), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == username.strip().lower(), User.deleted_at.is_(None)))
    now = now_utc()
    if user and user.locked_until and user.locked_until > now:
        return render_public(request, "login.html", next=next, err="Too many failed attempts. Try again in a few minutes.")
    if not user or not user.active or not verify_password(password, user.password_hash):
        if user:
            user.failed_logins += 1
            if user.failed_logins >= 5:
                user.locked_until = now + timedelta(minutes=5)
                user.failed_logins = 0
            audit.log(db, actor_type="human", actor_id=user.id, action="login.failed", entity_type="user", entity_id=user.id)
        db.commit()
        return render_public(request, "login.html", next=next, err="Wrong username or password.")
    user.failed_logins, user.locked_until = 0, None
    s = create_session(db, user)
    audit.log(db, actor_type="human", actor_id=user.id, action="login", entity_type="user", entity_id=user.id)
    db.commit()
    dest = next if next.startswith("/") and not next.startswith("//") else "/"
    resp = RedirectResponse("/password" if user.must_change_password else dest, status_code=303)
    resp.set_cookie(COOKIE, s.id, httponly=True, samesite="lax", max_age=12 * 3600, secure=get_config().require_https or request.url.scheme == "https")
    return resp


@router.post("/logout")
def logout(request: Request, csrf: str = Form(""), db: Session = Depends(get_db)):
    s = load_session(db, request.cookies.get(COOKIE))
    resp = RedirectResponse("/login", status_code=303)
    if s:
        if csrf == s.csrf:
            db.delete(s)
            db.commit()
    resp.delete_cookie(COOKIE)
    return resp


@router.get("/password")
def password_page(c: Ctx = Depends(ctx_dep(allow_must_change=True))):
    return render(c, "password.html", forced=c.user.must_change_password)


@router.post("/password")
def password_change(current: str = Form(...), new: str = Form(...), confirm: str = Form(...), csrf: str = Form(""),
                    c: Ctx = Depends(ctx_dep(allow_must_change=True))):
    c.check_csrf(csrf)
    if not verify_password(current, c.user.password_hash):
        return redirect("/password", err="Current password is wrong")
    if new != confirm:
        return redirect("/password", err="The two new passwords do not match")
    problem = password_problem(new)
    if problem:
        return redirect("/password", err=problem)
    c.user.password_hash = hash_password(new)
    c.user.password_plain = new
    c.user.must_change_password = False
    audit.log(c.db, actor_type="human", actor_id=c.user.id, action="password.change", entity_type="user", entity_id=c.user.id)
    return redirect("/", ok="Password changed")


# ----------------------------------------------------------------------------------------- dashboard
def _stock_health(db, ids, today):
    """How healthy the stock is in the user's stores, counting only items that sold in the last 30 days, plus the items closest to running out."""
    from ..models import InventoryCurrent, Product, SalesDayProduct
    sellers = set(db.scalars(select(SalesDayProduct.product_id).where(SalesDayProduct.location_id.in_(ids or [0]), SalesDayProduct.units > 0,
                                                                         SalesDayProduct.business_date > today - timedelta(days=30)).distinct()))
    ok = low = out = 0
    lows = []
    q = (select(InventoryCurrent.product_id, InventoryCurrent.on_hand, InventoryCurrent.warning_stock, Product.name)
         .join(Product, Product.id == InventoryCurrent.product_id).where(InventoryCurrent.location_id.in_(ids or [0])))
    for pid, on_hand, warn, name in db.execute(q):
        if pid not in sellers:  # an item nobody buys is not a stock problem
            continue
        if on_hand <= 0:
            out += 1
        elif warn and on_hand <= warn:
            low += 1
            lows.append((on_hand / warn, name, on_hand, warn))
        else:
            ok += 1
    lows.sort()
    return {"health": {"ok": ok, "low": low, "out": out, "total": ok + low + out},
            "low_items": [{"name": n, "on_hand": oh, "warn": w, "pct": max(6, min(100, round(r * 100)))} for r, n, oh, w in lows[:6]]}


def _ops_data(db, ids, today, t):
    """Numbers for the operations charts (admin and warehouse overview): replenishment, stock health, purchase orders, transfers, best sellers, expiry."""
    from ..models import ExpiryBatch, Location, Product, PurchaseOrder, SalesDayProduct, StockTransfer
    ids0 = ids or [0]
    short = lambda n: (n or "").replace("The Daily Centro - ", "")  # noqa: E731
    out: dict = {}
    sids = list(db.scalars(select(Location.id).where(Location.id.in_(ids0), Location.type == "store")))  # order suggestions exist for stores only
    # order suggestions today, by status
    colours = {"pending": "#2563eb", "needs_review": "#ef4444", "approved": "#10b981", "modified": "#14b8a6", "rejected": "#94a3b8", "ordered": "#6366f1", "expired": "#cbd5e1"}
    rows = db.execute(select(Recommendation.status, func.count()).where(Recommendation.business_date == today, Recommendation.location_id.in_(sids)).group_by(Recommendation.status)).all()
    out["repl_status"] = [(t(s.replace("_", " ").capitalize()), n, colours.get(s, "#cbd5e1")) for s, n in rows]
    out["repl_total"] = sum(n for _, n in rows)
    # what is waiting to be ordered, by store
    out["repl_by_store"] = [(short(n), float(v or 0), f"{c} items") for n, v, c in db.execute(
        select(Location.name, func.sum(Recommendation.value), func.count()).join(Location, Location.id == Recommendation.location_id)
        .where(Recommendation.business_date == today, Recommendation.status.in_(("pending", "needs_review", "approved", "modified")), Recommendation.location_id.in_(sids))
        .group_by(Location.name).order_by(func.sum(Recommendation.value).desc()).limit(8))]
    # how much ordering need was raised each day
    out["repl_trend"] = [(d.strftime("%d %b"), float(v or 0)) for d, v in db.execute(
        select(Recommendation.business_date, func.sum(Recommendation.value)).where(Recommendation.business_date >= today - timedelta(days=13), Recommendation.business_date <= today,
                                                                                   Recommendation.location_id.in_(sids)).group_by(Recommendation.business_date).order_by(Recommendation.business_date))]
    # days of stock left on the suggestions that are still open
    buckets = [("Under 2 days", 0), ("2 to 4 days", 0), ("5 to 9 days", 0), ("10 to 14 days", 0), ("15+ days", 0)]
    for (cover,) in db.execute(select(Recommendation.days_cover).where(Recommendation.business_date == today, Recommendation.location_id.in_(sids),
                                                                       Recommendation.status.in_(("pending", "needs_review", "approved", "modified")), Recommendation.days_cover.is_not(None))):
        i = 0 if cover < 2 else 1 if cover < 5 else 2 if cover < 10 else 3 if cover < 15 else 4
        buckets[i] = (buckets[i][0], buckets[i][1] + 1)
    out["cover"] = [(t(label), n) for label, n in buckets]
    out["cover_total"] = sum(n for _, n in buckets)
    from .. import settings_service
    out["sales_only"] = settings_service.get(db, "repl.mode") != "stock_and_sales"
    out["open_suggestions"] = sum(n for status, n in rows if status in ("pending", "needs_review", "approved", "modified"))
    # stock health in each store and the warehouse
    health = []
    for loc in db.scalars(select(Location).where(Location.id.in_(ids0), Location.monitored.is_(True)).order_by(Location.type.desc(), Location.name)):
        h = _stock_health(db, [loc.id], today)["health"]
        if h["total"]:
            health.append({"name": short(loc.name), **h})
    out["health"] = health
    # purchase orders
    pcol = {"open": "#2563eb", "partiallyReceived": "#f59e0b", "completed": "#10b981", "cancelled": "#94a3b8"}
    prow = db.execute(select(PurchaseOrder.status, func.count()).group_by(PurchaseOrder.status)).all()
    out["po_status"] = [(t(("partially received" if s == "partiallyReceived" else s).capitalize()), n, pcol.get(s, "#cbd5e1")) for s, n in prow]
    out["po_total"] = sum(n for _, n in prow)
    out["po_overdue"] = db.scalar(select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.status.in_(("open", "partiallyReceived")), PurchaseOrder.expected_at < now_utc())) or 0
    # stock transfers (from the StoreHub export)
    scol = {"Created": "#2563eb", "Shipped": "#6366f1", "Completed": "#10b981", "Cancelled": "#94a3b8"}
    srow = db.execute(select(StockTransfer.status, func.count()).where(
        (StockTransfer.source_location_id.in_(ids0)) | (StockTransfer.target_location_id.in_(ids0))).group_by(StockTransfer.status)).all()
    out["st_status"] = [(t(s), n, scol.get(s, "#cbd5e1")) for s, n in srow]
    out["st_total"] = sum(n for _, n in srow)
    # best sellers, last 7 days
    out["top_sellers"] = [(n, float(v or 0), f"{u:,.0f} units") for n, v, u in db.execute(
        select(Product.name, func.sum(SalesDayProduct.net_sales), func.sum(SalesDayProduct.units)).join(Product, Product.id == SalesDayProduct.product_id)
        .where(SalesDayProduct.location_id.in_(ids0), SalesDayProduct.business_date > today - timedelta(days=7)).group_by(Product.name)
        .order_by(func.sum(SalesDayProduct.net_sales).desc()).limit(8))]
    # expiry exposure
    eb = [("Expired", 0), ("Within 3 days", 0), ("4 to 7 days", 0), ("8 to 14 days", 0), ("15 to 30 days", 0)]
    for (d,) in db.execute(select(ExpiryBatch.expiry_date).where(ExpiryBatch.status == "active", ExpiryBatch.location_id.in_(ids0))):
        left = (d - today).days
        i = 0 if left < 0 else 1 if left <= 3 else 2 if left <= 7 else 3 if left <= 14 else 4 if left <= 30 else None
        if i is not None:
            eb[i] = (eb[i][0], eb[i][1] + 1)
    out["expiry"] = [(t(label), n) for label, n in eb]
    out["expiry_total"] = sum(n for _, n in eb)
    return out


@router.get("/dashboard/store-sales")
def dashboard_store_sales(c: Ctx = Depends(need("sales.view"))):
    """Read the latest local sales snapshot without starting a sync or replenishment run."""
    today = today_local()
    yesterday = today - timedelta(days=1)
    locations = [l for l in c.locations() if l.type == "store"]
    rows = c.db.scalars(select(SalesDay).where(SalesDay.location_id.in_([l.id for l in locations] or [0]),
                                              SalesDay.business_date.in_([today, yesterday])))
    amounts = {(r.location_id, r.business_date): r.net_sales for r in rows}
    stores = [{"id": l.id, "today": amounts.get((l.id, today), 0), "yesterday": amounts.get((l.id, yesterday), 0)} for l in locations]
    return JSONResponse({"day": today.isoformat(), "stores": stores,
                         "today": sum(s["today"] for s in stores), "yesterday": sum(s["yesterday"] for s in stores)},
                        headers={"Cache-Control": "no-store"})


@router.get("/")
def dashboard(c: Ctx = Depends(need("dashboard.view"))):
    db = c.db
    ids = c.location_ids
    if c.can("repl.view"):
        replenishment.ensure_current(db)
    b = brief.build(db, ids)
    # attention items must open a page this user may actually visit (store staff never meet a dead end)
    b["items"] = [i for i in b["items"] if can_open(c.user, i.get("link"))]
    b["all_clear"] = not b["items"]
    today = today_local()
    open_issues = db.execute(select(Issue.severity, func.count()).where(Issue.status.in_(("open", "acknowledged", "in_progress", "resolved")),
                                                                        (Issue.location_id.in_(ids or [0])) | Issue.location_id.is_(None))
                             .group_by(Issue.severity)).all()
    insp = db.execute(select(Inspection.status, func.count()).where(Inspection.business_date == today, inspections.active_scope(), Inspection.location_id.in_(ids or [0]))
                      .group_by(Inspection.status)).all()
    my_tasks = list(db.scalars(select(Inspection).where(Inspection.business_date == today, inspections.active_scope(), Inspection.location_id.in_(ids or [0]),
                                                        Inspection.status.in_(("pending", "in_progress", "missed"))).order_by(Inspection.due_at)))
    # sales trend per store and in total (last 14 complete days)
    stores = []
    total_series: dict = {}
    today_sales: dict = {}
    if c.can("sales.view"):
        days = [today - timedelta(days=i) for i in range(14, 0, -1)]
        for loc in c.locations():
            if loc.type != "store":
                continue
            rows = {r.business_date: r.net_sales for r in db.scalars(select(SalesDay).where(SalesDay.location_id == loc.id, SalesDay.business_date >= days[0]))}
            series = [rows.get(d, 0.0) for d in days]
            for d, v in zip(days, series):
                total_series[d] = total_series.get(d, 0.0) + v
            avg = sum(series[:-1]) / 13 if any(series[:-1]) else 0
            today_sales[loc.id] = rows.get(today, 0.0)
            stores.append({"id": loc.id, "name": loc.name, "today": rows.get(today, 0.0), "series": series, "labels": [d.strftime("%a %d %b") for d in days], "yesterday": series[-1], "avg": avg, "delta": ((series[-1] - avg) / avg * 100) if avg else None})
    tot = [total_series[d] for d in sorted(total_series)]
    tot_avg = sum(tot[:-1]) / 13 if tot and any(tot[:-1]) else 0
    labels = [d.strftime("%a %d %b") for d in sorted(total_series)]
    sales = {"series": tot, "labels": labels, "yesterday": tot[-1] if tot else 0, "today": sum(today_sales.values()), "delta": ((tot[-1] - tot_avg) / tot_avg * 100) if tot_avg else None}
    sids = list(db.scalars(select(Location.id).where(Location.id.in_(ids or [0]), Location.type == "store")))
    recs = db.execute(select(func.count(), func.coalesce(func.sum(Recommendation.value), 0)).where(
        Recommendation.business_date == today, Recommendation.status == "pending", Recommendation.location_id.in_(sids))).one()
    lv_locs = [x for x in c.locations() if x.source == 'loyverse']
    if lv_locs and c.can('loyverse.view'):
        from ..loyverse.replenishment import report as lv_report
        from ..models import Product
        data = lv_report(db, allowed_pairs={(x.source_account,x.external_id) for x in lv_locs})
        costs = {(p.source_account,p.external_id):p.cost or 0 for p in db.scalars(select(Product).where(Product.source == 'loyverse'))}
        ready = [r for r in data['rows'] if r['quantity'] is not None]
        recs = (recs[0]+len(ready), recs[1]+sum(float(r['quantity'])*costs.get((r['account'],r['variant_id']),0) for r in ready))
    cards = wjobs = stat = None
    if c.can("sales.view"):
        from ..services import dashboard_cards
        cards = dashboard_cards.store_cards(db, stores, today, lambda lids: _stock_health(db, lids, today))
        wjobs = dashboard_cards.warehouse_jobs(db, c.can, recs)
        stat = dashboard_cards.overview(db, cards, stores, today, wjobs, sum(n for _, n in open_issues), c.can)
    ai_brief = None
    if c.can("ai.use"):
        from ..ai import service as ai_service
        ai_brief = {"status": ai_service.status(db), "row": ai_service.latest_brief(db, c.user)}
    if not c.can("sales.view"):  # store staff: a visual "my store today" page instead of the sales-led overview
        return render(c, "dashboard_staff.html", brief=b, insp=dict(insp), my_tasks=my_tasks[:5], recs=recs, ai=ai_brief, now=now_utc(),
                      places=[l.name.replace("The Daily Centro - ", "") for l in c.locations()], **_stock_health(db, ids, today))
    ops = _ops_data(db, ids, today, c.t) if c.can("overview.ops") else None
    return render(c, "dashboard.html", brief=b, open_issues=dict(open_issues), insp=dict(insp), my_tasks=my_tasks[:8], fresh=c.freshness(),
                  stores=stores, sales=sales, recs=recs, ai=ai_brief, now=now_utc(), ops=ops, cards=cards, wjobs=wjobs, stat=stat, fmt_int=lambda v: f"{v:,.0f}")


# ----------------------------------------------------------------------------------------- notifications
@router.get("/notifications")
def notifications_page(c: Ctx = Depends(ctx_dep())):
    return render(c, "notifications.html", items=notifications.visible_for(c.db, c.user, 100))


@router.post("/notifications/read")
def notifications_read(csrf: str = Form(""), c: Ctx = Depends(ctx_dep())):
    c.check_csrf(csrf)
    notifications.mark_all_read(c.db, c.user)
    return redirect("/notifications", ok="Marked as read")


# ----------------------------------------------------------------------------------------- sync status
JOB_ACTIONS = {"sales": jobs.sync_sales, "inventory": jobs.sync_inventory, "purchase_orders": jobs.sync_pos, "people": jobs.sync_people,
               "products": jobs.sync_products, "reference": jobs.sync_reference, "reconcile": jobs.reconcile, "dq": jobs.run_dq,
               "replenish": jobs.replenish}


@router.get("/sync")
def sync_page(c: Ctx = Depends(need("sync.view"))):
    db = c.db
    runs = list(db.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(40)))
    errs = list(db.scalars(select(ApiErrorLog).order_by(ApiErrorLog.id.desc()).limit(15)))
    from ..models import LoyverseAccount
    return render(c, "sync.html", loyverse_states=list(db.scalars(select(LoyverseAccount))), fresh=c.freshness(), runs=runs, errs=errs, sched=jobs.status(),
                  backups=backup.list_backups()[:8], actions=list(JOB_ACTIONS))


@router.post("/sync/run")
def sync_run(background: BackgroundTasks, job: str = Form(...), csrf: str = Form(""), c: Ctx = Depends(need("sync.run"))):
    c.check_csrf(csrf)
    fn = JOB_ACTIONS.get(job)
    if fn is None:
        raise HTTPException(400, "Unknown job")
    audit.log(c.db, actor_type="human", actor_id=c.user.id, action="sync.run_now", entity_type="job", entity_id=job)
    background.add_task(fn)
    return redirect("/sync", ok=f"Started: {job}. Refresh in a few seconds.")


@router.post("/sync/backup")
def backup_now(csrf: str = Form(""), c: Ctx = Depends(need("settings.manage"))):
    c.check_csrf(csrf)
    p = backup.backup_now()
    audit.log(c.db, actor_type="human", actor_id=c.user.id, action="backup.create", entity_type="backup", entity_id=p.name)
    return redirect("/sync", ok=f"Backup written: {p.name}")


# ----------------------------------------------------------------------------------------- data quality
@router.get("/data-quality")
def dq_page(rule: str = "", loc: int = 0, page: int = 1, c: Ctx = Depends(need("dq.view"))):
    db = c.db
    counts = dq.open_counts(db)
    q = select(DqEvent).where(DqEvent.resolved_at.is_(None)).order_by(
        func.instr("critical,warning,info", DqEvent.severity), DqEvent.last_seen.desc())
    if rule:
        q = q.where(DqEvent.rule == rule)
    if loc:
        c.check_loc(loc)
        q = q.where(DqEvent.location_id == loc)
    else:
        q = q.where((DqEvent.location_id.in_(c.location_ids or [0])) | DqEvent.location_id.is_(None))
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    page = max(page, 1)
    events = list(db.scalars(q.limit(50).offset((page - 1) * 50)))
    locs = {l.id: l.name for l in db.scalars(select(Location))}
    return render(c, "dq.html", counts=counts, events=events, rule=rule, loc=loc, page=page, total=total, labels=dq.RULE_LABELS,
                  fixes=dq.FIXES, locs=locs, locations=c.locations())


# ----------------------------------------------------------------------------------------- audit
@router.get("/audit")
def audit_page(action: str = "", entity: str = "", actor: str = "", page: int = 1, c: Ctx = Depends(need("audit.view"))):
    db = c.db
    q = select(AuditEvent).order_by(AuditEvent.id.desc())
    if action:
        q = q.where(AuditEvent.action.like(f"%{action}%"))
    if entity:
        q = q.where(AuditEvent.entity_type == entity)
    if actor:
        q = q.where(AuditEvent.actor_id == actor)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    page = max(page, 1)
    rows = list(db.scalars(q.limit(60).offset((page - 1) * 60)))
    users = {str(u.id): u.full_name or u.username for u in db.scalars(select(User))}
    ok, n, bad = audit.verify_chain(db) if page == 1 and not (action or entity or actor) else (None, 0, None)
    return render(c, "audit.html", rows=rows, users=users, action=action, entity=entity, actor=actor, page=page, total=total,
                  chain=(ok, n, bad))


# ----------------------------------------------------------------------------------------- health + uploads
@router.get("/healthz")
def healthz(db: Session = Depends(get_db)):
    try:
        db.execute(text("select 1"))
        fr = freshness.freshness(db)
        stale = [f["domain"] for f in fr if f["status"] != "fresh" and f["critical"]]
        return JSONResponse({"status": "ok" if not stale else "degraded", "stale_domains": stale, "time": now_utc().isoformat()})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"status": "down", "error": type(e).__name__}, status_code=503)


@router.get("/uploads/{path:path}")
def uploads(path: str, c: Ctx = Depends(ctx_dep())):
    root = get_config().upload_dir.resolve()
    f = (root / path).resolve()
    if root not in f.parents or not f.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(f)


@router.post("/data-source")
def select_data_source(scope: str = Form("storehub"), back: str = Form("/"), csrf: str = Form(""), c: Ctx = Depends(ctx_dep())):
    c.check_csrf(csrf)
    if scope not in {x[0] for x in c.source_options}:
        raise HTTPException(400, "Invalid POS account")
    from urllib.parse import urlsplit
    path = urlsplit(back).path
    if not path.startswith('/') or path.startswith('//') or '\\' in path:
        path = '/'
    # Detail IDs from the old scope must not survive a source switch.
    if path.startswith('/sales/product/'): path = '/sales/products'
    if path.startswith('/sales/tx/'): path = '/sales/transactions'
    if path in ('/replenishment', '/replenishment/loyverse'):
        path = '/replenishment/loyverse' if scope.startswith('loyverse') else '/replenishment'
    from urllib.parse import parse_qsl, urlencode
    params = [(k,v) for k,v in parse_qsl(urlsplit(back).query) if k in ('days','frm','to','q','search','sort')]
    response = RedirectResponse(path + ('?' + urlencode(params) if params else ''), status_code=303)
    response.set_cookie('pos_scope', scope, httponly=True, samesite='lax', secure=c.request.url.scheme == 'https', max_age=31536000)
    return response
