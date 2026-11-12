"""Daily operations: inspections, issues, cycle counts, expiry, receiving."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .. import audit
from .. import settings_service as cfg
from ..config import get_config
from ..models import (CycleCount, ExpiryBatch, Inspection, Issue, Location, Product, Receiving, SalesDay, StockEvent, Supplier, User)
from ..security import ROLE_LABELS
from ..services import counts as countsvc
from ..services import expiry as expirysvc
from ..services import compliance as compsvc
from ..services import freshness, inspections, issues, receiving, performance
from ..services import stock_events as stockevents
from ..timeutil import now_utc, to_local, today_local
from .deps import Ctx, need, parse_date, parse_float, redirect, render, save_upload

router = APIRouter()


# ======================================================================================= inspections
@router.get("/inspections")
def inspections_page(on: str = "", loc: int = 0, c: Ctx = Depends(need("inspection.do"))):
    d = parse_date(on) or today_local()
    if d == today_local():
        inspections.generate_tasks(c.db, d)
        c.db.flush()
    locations = [l for l in c.locations() if l.name.strip().casefold() != "zenwok warehouse" and not l.closed]
    ids = [l.id for l in locations if not loc or l.id == loc]
    if loc:
        c.check_loc(loc)
    rows = inspections.todays(c.db, d, ids)
    return render(c, "inspections.html", rows=rows, day=d, loc=loc, locations=locations, prev=d - timedelta(days=1), nxt=d + timedelta(days=1), today=today_local())


@router.get("/inspections/lark-reports")
def inspection_lark_reports(on: str = "", loc: int = 0, c: Ctx = Depends(need("inspection.do"))):
    from ..services import lark_inspections
    d = parse_date(on) or today_local()
    if loc:
        c.check_loc(loc)
    locations = [l for l in c.locations() if l.name.strip().casefold() != "zenwok warehouse" and not l.closed and (not loc or l.id == loc)]
    try:
        result = lark_inspections.reports_for_day(d)
    except Exception:
        return {"available":False,"message":"Could not check Lark. Please retry.","reports":{}}
    grouped = {str(l.id):[r for r in result["reports"] if r["store"]==lark_inspections.store_key(l.name)] for l in locations}
    return {**result,"reports":grouped,"day":d.isoformat()}


@router.post("/inspections/lark-reports")
def inspection_lark_sync(on: str = "", loc: int = 0, csrf: str = Form(""), c: Ctx = Depends(need("inspection.do"))):
    c.check_csrf(csrf)
    result = inspection_lark_reports(on, loc, c)
    if result.get("available"):
        from ..services import lark_inspections
        d = parse_date(on) or today_local()
        lark_inspections.mark_submitted(c.db, d, result["reports"], c.user.id)
        result["submitted"] = [i.id for i in inspections.todays(c.db, d, [int(lid) for lid in result["reports"]]) if i.status == "submitted"]
    return result


@router.get("/inspections/{iid}")
def inspection_form(iid: int, c: Ctx = Depends(need("inspection.do"))):
    insp = c.db.scalar(select(Inspection).where(Inspection.id == iid).options(
        selectinload(Inspection.template), selectinload(Inspection.location), selectinload(Inspection.results)))
    if insp is None:
        raise HTTPException(404)
    c.check_loc(insp.location_id)
    results = {r.item_id: r for r in insp.results}
    soon = []
    if insp.template.slot in ("opening", "daily"):
        soon = [b for b in expirysvc.active_batches(c.db, [insp.location_id]) if (b.expiry_date - today_local()).days <= 3]
    names = {u.id: (u.full_name or u.username) for u in c.db.scalars(select(User).where(User.id.in_([x for x in (insp.completed_by, insp.assigned_to) if x] or [0])))}
    return render(c, "inspection_form.html", insp=insp, results=results, soon=soon, names=names, today=today_local())


@router.post("/inspections/{iid}/start")
def inspection_start(iid: int, csrf: str = Form(""), c: Ctx = Depends(need("inspection.do"))):
    c.check_csrf(csrf)
    insp = c.db.get(Inspection, iid)
    if insp is None:
        raise HTTPException(404)
    c.check_loc(insp.location_id)
    try:
        inspections.start(c.db, insp, c.user)
    except inspections.InspectionError as e:
        return redirect(f"/inspections/{iid}", err=str(e))
    return redirect(f"/inspections/{iid}")


MAX_VIDEOS = 10  # per walkthrough


@router.post("/inspections/{iid}/submit")
async def inspection_submit(iid: int, request: Request, c: Ctx = Depends(need("inspection.do"))):
    form = await request.form()
    c.check_csrf(str(form.get("csrf", "")))
    insp = c.db.scalar(select(Inspection).where(Inspection.id == iid).options(selectinload(Inspection.template), selectinload(Inspection.location)))
    if insp is None:
        raise HTTPException(404)
    c.check_loc(insp.location_id)
    if insp.status in inspections.DONE:
        return redirect(f"/inspections/{iid}", err="This inspection is already completed")
    answers, photos, videos, saved_paths = {}, {}, {}, []
    try:
        for item in insp.template.items:
            p = form.get(f"p_{item.id}")
            answers[item.id] = {"value": form.get(f"v_{item.id}"), "passed": None if p is None else (p == "pass"), "note": form.get(f"n_{item.id}")}
            photos[item.id] = []
            photo_field = f"photos_{item.id}" if item.kind == "video" else f"f_{item.id}"
            for up in form.getlist(photo_field):
                if hasattr(up, "filename") and up.filename:
                    path = await save_upload(up)
                    if path:
                        saved_paths.append(path)
                        photos[item.id].append(path)
            if item.kind == "video":
                ups = [u for u in form.getlist(f"f_{item.id}") if hasattr(u, "filename") and u.filename]
                if len(ups) > MAX_VIDEOS:
                    raise HTTPException(400, f"Attach at most {MAX_VIDEOS} videos for the walkthrough.")
                videos[item.id] = []
                for up in ups:  # one or more videos, each checked and stored on its own
                    path = await save_upload(up, allow_video=True)
                    if path:
                        saved_paths.append(path)
                        if Path(path).suffix not in (".mp4", ".mov", ".webm"):
                            raise HTTPException(400, "Choose an MP4, MOV or WebM video for the walkthrough.")
                        videos[item.id].append(path)
        if insp.status in ("pending", "missed") and insp.started_at is None:
            inspections.start(c.db, insp, c.user)
        inspections.complete(c.db, insp, c.user, answers, photos, videos=videos)
    except Exception as e:
        c.db.rollback()
        upload_root = get_config().upload_dir.resolve()
        for path in saved_paths:
            target = (upload_root / path).resolve()
            if target.is_relative_to(upload_root):
                target.unlink(missing_ok=True)
        if isinstance(e, inspections.InspectionError):
            return redirect(f"/inspections/{iid}", err=str(e))
        raise
    return redirect("/inspections", ok="Inspection submitted" + (f" with {insp.fail_count} failed item(s). Issues were created." if insp.fail_count else ""))


# ======================================================================================= issues
def _issue_q(c: Ctx):
    ids = c.location_ids
    return select(Issue).where((Issue.location_id.in_(ids or [0])) | Issue.location_id.is_(None))


@router.get("/issues")
def issues_page(view: str = "open", sev: str = "", loc: int = 0, mine: int = 0, page: int = 1, c: Ctx = Depends(need("issue.view"))):
    db = c.db
    q = _issue_q(c).options(selectinload(Issue.location), selectinload(Issue.owner), selectinload(Issue.product))
    if view == "open":
        q = q.where(Issue.status.in_(issues.OPEN_STATES))
    elif view == "closed":
        q = q.where(Issue.status == "closed")
    if sev:
        q = q.where(Issue.severity == sev)
    if loc:
        c.check_loc(loc)
        q = q.where(Issue.location_id == loc)
    if mine:
        q = q.where(Issue.owner_id == c.user.id)
    q = q.order_by(Issue.severity, Issue.due_at)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    page = max(page, 1)
    rows = list(db.scalars(q.limit(50).offset((page - 1) * 50)))
    return render(c, "issues.html", rows=rows, view=view, sev=sev, loc=loc, mine=mine, page=page, total=total, locations=c.locations(), now=now_utc(),
                  labels=issues.STATUS_LABEL)


@router.get("/issues/new")
def issue_new(c: Ctx = Depends(need("issue.manage"))):
    owners = list(c.db.scalars(select(User).where(User.active.is_(True)).order_by(User.full_name)))
    return render(c, "issue_new.html", locations=c.locations(), owners=owners, types=issues.ISSUE_TYPES, sevs=issues.SEVERITIES, sev_label=issues.SEV_LABEL)


@router.post("/issues/new")
def issue_create(title: str = Form(...), description: str = Form(""), severity: str = Form("P2"), type: str = Form("other"), location_id: int = Form(0),
                 owner_id: int = Form(0), csrf: str = Form(""), c: Ctx = Depends(need("issue.manage"))):
    c.check_csrf(csrf)
    if location_id:
        c.check_loc(location_id)
    try:
        i = issues.create_issue(c.db, type=type, severity=severity, title=title.strip(), description=description.strip() or None,
                                location_id=location_id or None, owner_id=owner_id or None, created_by=c.user.id, source="manual")
    except ValueError as e:
        return redirect("/issues/new", err=str(e))
    return redirect(f"/issues/{i.id}", ok="Issue created")


@router.get("/issues/{iid}")
def issue_detail(iid: int, c: Ctx = Depends(need("issue.view"))):
    i = c.db.scalar(select(Issue).where(Issue.id == iid).options(selectinload(Issue.events), selectinload(Issue.location), selectinload(Issue.owner), selectinload(Issue.product)))
    if i is None:
        raise HTTPException(404)
    c.check_loc(i.location_id)
    owners = list(c.db.scalars(select(User).where(User.active.is_(True)).order_by(User.full_name)))
    return render(c, "issue_detail.html", i=i, owners=owners, labels=issues.STATUS_LABEL, sev_label=issues.SEV_LABEL, now=now_utc())


@router.post("/issues/{iid}/act")
async def issue_act(iid: int, request: Request, c: Ctx = Depends(need("issue.update"))):
    form = await request.form()
    c.check_csrf(str(form.get("csrf", "")))
    i = c.db.get(Issue, iid)
    if i is None:
        raise HTTPException(404)
    c.check_loc(i.location_id)
    photo = await save_upload(form.get("photo")) if hasattr(form.get("photo"), "filename") and form.get("photo").filename else None
    try:
        issues.act(c.db, i, c.user, str(form.get("action", "")), (str(form.get("note", "")).strip() or None), photo, int(form.get("assignee") or 0) or None)
    except issues.IssueError as e:
        c.db.rollback()
        return redirect(f"/issues/{iid}", err=str(e))
    return redirect(f"/issues/{iid}", ok="Saved")


# ======================================================================================= cycle counts
@router.get("/counts")
def counts_page(c: Ctx = Depends(need("count.enter"))):
    rows = list(c.db.scalars(select(CycleCount).where(CycleCount.location_id.in_(c.location_ids or [0])).options(selectinload(CycleCount.location), selectinload(CycleCount.lines))
                             .order_by(CycleCount.id.desc()).limit(30)))
    today = today_local()
    stats = {r.id: {"done": sum(1 for l in r.lines if l.counted_qty is not None), "total": len(r.lines), "diffs": sum(1 for l in r.lines if l.variance)} for r in rows}
    for s in stats.values():
        s["pct"] = round(s["done"] / s["total"] * 100) if s["total"] else 0
    # one card per store for today: its open count if there is one, otherwise the latest count it already sent
    current: dict[int, CycleCount] = {}
    for r in rows:
        if r.business_date == today and (r.location_id not in current or (current[r.location_id].status != "open" and r.status == "open")):
            current[r.location_id] = r
    locations = c.locations()
    tiles = [{"loc": l, "name": l.name.replace("The Daily Centro - ", ""), "count": current.get(l.id)} for l in locations]
    shown = {t["count"].id for t in tiles if t["count"]}
    return render(c, "counts.html", rows=rows, locations=locations, stats=stats, tiles=tiles, earlier=[r for r in rows if r.id not in shown], today=today)


def _import_message(c, what: str, r: dict, extra: str = "") -> tuple[str, bool]:
    msg = what + extra
    if r.get("duplicates"):
        msg += f"; {r['duplicates']} already recorded"
    ok = True
    if r["errors"]:
        msg += f"; skipped {len(r['errors'])}: " + "; ".join(f"row {n} {why}" for n, why in r["errors"][:5]) + (" ..." if len(r["errors"]) > 5 else "")
        ok = bool(r.get("lines") or r.get("added"))
    return msg, ok


@router.get("/counts/template.csv")
def counts_template(c: Ctx = Depends(need("count.enter"))):
    return Response(countsvc.COUNT_TEMPLATE_CSV, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="stock-count-template.csv"'})


@router.post("/counts/import")
async def counts_import(file: UploadFile = File(...), location_id: int = Form(0), submit: str = Form(""), csrf: str = Form(""), c: Ctx = Depends(need("count.enter"))):
    """Record counted quantities from a CSV or Excel (.xlsx) file."""
    c.check_csrf(csrf)
    if location_id:
        c.check_loc(location_id)
    data = await file.read(5_000_001)
    if len(data) > 5_000_000:
        return redirect("/counts", err=c.t("The file is larger than 5 MB"))
    try:
        r = countsvc.import_counts(c.db, c.user, file.filename or "", data, c.locations(), location_id or None, submit=bool(submit))
    except (ValueError, countsvc.CountError) as e:
        c.db.rollback()
        return redirect("/counts", err=str(e))
    msg, ok = _import_message(c, f"Recorded {r['lines']} counted item(s) in {r['counts']} count(s)", r, "; sent for approval" if r["submitted"] else "")
    return redirect("/counts", ok=msg) if ok else redirect("/counts", err=msg)


@router.post("/counts/create")
def counts_create(location_id: int = Form(...), size: int = Form(30), mode: str = Form(""), csrf: str = Form(""), c: Ctx = Depends(need("count.enter"))):
    c.check_csrf(csrf)
    c.check_loc(location_id)
    loc = c.db.get(Location, location_id)
    cc = countsvc.create_targeted(c.db, c.user, loc, []) if mode == "scan" else countsvc.create(c.db, c.user, loc, min(max(size, 5), 100))
    return redirect(f"/counts/{cc.id}")


def _scan_count(c, cid):
    cc = c.db.get(CycleCount, cid)
    if cc is None:
        raise HTTPException(404)
    c.check_loc(cc.location_id)
    if cc.status != "open":
        raise HTTPException(409, "This count is already submitted")
    return cc


@router.get("/counts/{cid}/products")
def count_products(cid: int, q: str = "", c: Ctx = Depends(need("count.enter"))):
    from ..services.product_lookup import search
    cc = _scan_count(c, cid)
    return search(c.db, q, cc.location)


@router.post("/counts/{cid}/scan")
def count_scan(cid: int, code: str = Form(""), product_id: int = Form(0), csrf: str = Form(""), c: Ctx = Depends(need("count.enter"))):
    from ..services.transfer_sheets import find
    from ..storehub.client import StoreHubError
    c.check_csrf(csrf)
    cc = _scan_count(c, cid)
    if product_id:
        product = c.db.get(Product, product_id)
    else:
        from ..services.product_lookup import only_one, search
        matches = search(c.db, code, cc.location)
        one = only_one(matches, code)   # the full barcode, a SKU, or the last digits of a barcode
        product = c.db.get(Product, one['id']) if one else None
    if product is None or not product.in_api:
        raise HTTPException(404, "Product not found. Scan its barcode or enter its SKU.")
    try:
        qty, synced_at = countsvc.quantity_for_count(c.db, cc.location, product)
    except (StoreHubError, countsvc.CountError, ValueError):
        raise HTTPException(503, "Could not fetch current stock from the POS, and there is no earlier sync of this product to use. Please retry.")
    line = next((l for l in cc.lines if l.product_id == product.id), None)
    out = {"id":product.id,"name":product.name,"system":qty,"counted":line.counted_qty if line else None,"note":line.note if line else "","stale":synced_at is not None}
    if synced_at is not None:
        from ..timeutil import fmt_clock
        out["notice"] = c.t("The POS could not be reached, so this is the stock from the last sync at {time}. Your count is still saved.").replace("{time}", fmt_clock(synced_at))
    return out


@router.post("/counts/{cid}/scan-save")
def count_scan_save(cid: int, product_id: int = Form(...), counted: float = Form(...), note: str = Form(""), csrf: str = Form(""), c: Ctx = Depends(need("count.enter"))):
    from ..storehub.client import StoreHubError
    c.check_csrf(csrf)
    cc = _scan_count(c, cid)
    product = c.db.get(Product, product_id)
    if product is None or not product.in_api:
        raise HTTPException(404, "Product not found")
    try:
        line = countsvc.save_scanned(c.db, cc, c.user, product, counted, note)
    except countsvc.CountError as e:
        raise HTTPException(400, str(e))
    except (StoreHubError, ValueError):
        raise HTTPException(503, "Could not fetch current stock from the POS. Your count was not saved; please retry.")
    out = {"name":product.name,"counted":line.counted_qty,"system":line.system_qty,"difference":line.variance,"stale":line.reason.startswith("scanned (")}
    if out["stale"]:
        out["notice"] = c.t("The stock shown with it is from the last sync, not live.")
    return out


@router.get("/counts/{cid}")
def count_detail(cid: int, c: Ctx = Depends(need("count.enter"))):
    cc = c.db.scalar(select(CycleCount).where(CycleCount.id == cid).options(selectinload(CycleCount.lines).selectinload(countsvc.CycleCountLine.product), selectinload(CycleCount.location)))
    if cc is None:
        raise HTTPException(404)
    c.check_loc(cc.location_id)
    return render(c, "count_detail.html", cc=cc)


@router.post("/counts/{cid}/save")
async def count_save(cid: int, request: Request, c: Ctx = Depends(need("count.enter"))):
    form = await request.form()
    c.check_csrf(str(form.get("csrf", "")))
    cc = c.db.scalar(select(CycleCount).where(CycleCount.id == cid).options(selectinload(CycleCount.lines)))
    if cc is None:
        raise HTTPException(404)
    c.check_loc(cc.location_id)
    rows = [{"line_id": l.id, "counted": parse_float(form.get(f"c_{l.id}")), "note": form.get(f"n_{l.id}")} for l in cc.lines]
    try:
        countsvc.enter(c.db, cc, c.user, rows)
        if form.get("submit"):
            countsvc.submit(c.db, cc, c.user)
    except countsvc.CountError as e:
        c.db.rollback()
        return redirect(f"/counts/{cid}", err=str(e))
    if form.get("submit"):
        return redirect(f"/counts/{cid}", ok="Count submitted for approval")
    if any(l.counted_qty is not None and l.counted_qty > 0 for l in cc.lines):
        return count_transfer_csv(cid, c)
    return redirect(f"/counts/{cid}", ok="Saved")


@router.post("/counts/{cid}/approve")
def count_approve(cid: int, csrf: str = Form(""), c: Ctx = Depends(need("count.approve"))):
    c.check_csrf(csrf)
    cc = c.db.scalar(select(CycleCount).where(CycleCount.id == cid).options(selectinload(CycleCount.lines).selectinload(countsvc.CycleCountLine.product), selectinload(CycleCount.location)))
    if cc is None:
        raise HTTPException(404)
    c.check_loc(cc.location_id)
    try:
        countsvc.approve(c.db, cc, c.user)
    except countsvc.CountError as e:
        c.db.rollback()
        return redirect(f"/counts/{cid}", err=str(e))
    return redirect(f"/counts/{cid}", ok="Approved. Download the stock take file and import it in StoreHub.")


@router.post("/counts/{cid}/delete")
def count_delete(cid: int, csrf: str = Form(""), c: Ctx = Depends(need("count.enter"))):
    c.check_csrf(csrf)
    cc = c.db.scalar(select(CycleCount).where(CycleCount.id == cid).options(selectinload(CycleCount.lines)))
    if cc is None:
        raise HTTPException(404)
    c.check_loc(cc.location_id)
    if cc.status == "approved" and not c.can("count.approve"):  # an approved count may already have been entered in StoreHub
        raise HTTPException(403)
    countsvc.delete(c.db, cc, c.user)
    return redirect("/counts", ok=c.t("Count deleted"))


@router.get("/counts/{cid}/transfer.csv")
def count_transfer_csv(cid: int, c: Ctx = Depends(need("count.enter"))):
    """The counted products as a StoreHub stock-transfer import file: available right after Save."""
    cc = c.db.scalar(select(CycleCount).where(CycleCount.id == cid).options(selectinload(CycleCount.lines).selectinload(countsvc.CycleCountLine.product), selectinload(CycleCount.location)))
    if cc is None:
        raise HTTPException(404)
    c.check_loc(cc.location_id)
    body = countsvc.transfer_csv(cc)
    if body.count("\r\n") <= 2:
        return redirect(f"/counts/{cid}", err=c.t("Nothing above zero has been counted yet."))
    audit.log(c.db, actor_type="user", actor_id=c.user.id, action="export_count_transfer", entity_type="cycle_count", entity_id=cid, after={"status": cc.status, "lines": body.count("\r\n") - 2})
    c.db.commit()
    import re
    store = re.sub(r"[^A-Za-z0-9]+", "-", cc.location.name.replace("The Daily Centro", "").strip()).strip("-") or "store"
    return Response("\ufeff" + body, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="StockTransfer-count-{cid}-{store}-{cc.business_date}.csv"'})


@router.get("/counts/{cid}/counted.csv")
def count_counted_csv(cid: int, c: Ctx = Depends(need("count.enter"))):
    """What has been counted so far: available right after Save, before the count is submitted or approved."""
    cc = c.db.scalar(select(CycleCount).where(CycleCount.id == cid).options(selectinload(CycleCount.lines).selectinload(countsvc.CycleCountLine.product), selectinload(CycleCount.location)))
    if cc is None:
        raise HTTPException(404)
    c.check_loc(cc.location_id)
    if not any(l.counted_qty is not None for l in cc.lines):
        return redirect(f"/counts/{cid}", err=c.t("Nothing has been counted yet."))
    audit.log(c.db, actor_type="user", actor_id=c.user.id, action="export_count", entity_type="cycle_count", entity_id=cid, after={"status": cc.status, "lines": len(cc.lines)})
    c.db.commit()
    return Response("\ufeff" + countsvc.counted_csv(cc), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="stock-count-{cid}-{cc.business_date}.csv"'})


@router.get("/counts/{cid}.csv")
def count_csv(cid: int, c: Ctx = Depends(need("count.enter"))):
    cc = c.db.scalar(select(CycleCount).where(CycleCount.id == cid).options(selectinload(CycleCount.lines).selectinload(countsvc.CycleCountLine.product)))
    if cc is None:
        raise HTTPException(404)
    c.check_loc(cc.location_id)
    return Response(countsvc.stock_take_csv(cc), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="stock-take-{cid}.csv"'})


@router.post("/counts/targeted")
def counts_targeted(location_id: int = Form(...), product_id: int = Form(...), csrf: str = Form(""), c: Ctx = Depends(need("count.enter"))):
    c.check_csrf(csrf)
    c.check_loc(location_id)
    loc = c.db.get(Location, location_id)
    if loc is None:
        raise HTTPException(404)
    from ..services.sources import matches
    if not matches(c.db.get(Product, product_id), loc):
        raise HTTPException(400, "Choose a product from the same POS account as the store")
    cc = countsvc.create_targeted(c.db, c.user, loc, [product_id])
    return redirect(f"/counts/{cc.id}")


# ======================================================================================= stock events
HISTORY_PAGE = 100


@router.get("/stock-events")
def stock_events_page(tab: str = "history", loc: int = 0, q: str = "", action: str = "", frm: str = "", to: str = "", page: int = 1, export: str = "", unchanged: str = "", c: Ctx = Depends(need("count.enter"))):
    db = c.db
    if tab == "history":
        return _stock_history(c, loc, q, action, frm, to, page, export, unchanged == "1")
    ids = c.location_ids or [0]
    q = select(StockEvent).where(StockEvent.location_id.in_(ids)).options(selectinload(StockEvent.product), selectinload(StockEvent.location))
    if tab in stockevents.KINDS:
        q = q.where(StockEvent.kind == tab)
    else:
        tab = "all"
    rows = list(db.scalars(q.order_by(StockEvent.id.desc()).limit(100)))
    counts = {k: n for k, n in db.execute(select(StockEvent.kind, func.count()).where(StockEvent.location_id.in_(ids)).group_by(StockEvent.kind))}
    verified = db.scalar(select(func.count()).select_from(StockEvent).where(StockEvent.location_id.in_(ids), StockEvent.verified_at.isnot(None))) or 0
    users = {u.id: (u.full_name or u.username) for u in db.scalars(select(User))}
    today = today_local()
    groups: list[dict] = []   # the list is shown a day at a time, newest first
    for e in rows:
        d = to_local(e.created_at).date()
        if not groups or groups[-1]["date"] != d:
            groups.append({"date": d, "rows": [], "rel": "Today" if d == today else "Yesterday" if d == today - timedelta(days=1) else ""})
        groups[-1]["rows"].append(e)
    total = sum(counts.values())
    return render(c, "stock_events.html", rows=rows, tab=tab, counts=counts, verified=verified, waiting=max(total - verified, 0), total=total, groups=groups, today=today,
                  users=users, locations=c.locations())


@router.get("/stock-events/template.csv")
def stock_events_template(c: Ctx = Depends(need("count.enter"))):
    return Response(stockevents.EVENT_TEMPLATE_CSV, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="stock-changes-template.csv"'})


@router.post("/stock-events/import")
async def stock_events_import(file: UploadFile = File(...), location_id: int = Form(0), kind: str = Form(""), in_storehub: str = Form(""), csrf: str = Form(""),
                              c: Ctx = Depends(need("count.enter"))):
    """Record write-offs and unplanned receipts from a CSV or Excel (.xlsx) file."""
    c.check_csrf(csrf)
    if location_id:
        c.check_loc(location_id)
    data = await file.read(5_000_001)
    if len(data) > 5_000_000:
        return redirect("/stock-events?tab=all", err=c.t("The file is larger than 5 MB"))
    try:
        r = stockevents.import_events(c.db, c.user, file.filename or "", data, c.locations(), location_id or None,
                                      default_kind=kind if kind in stockevents.KINDS else None, already_in_storehub=bool(in_storehub))
    except (ValueError, stockevents.StockEventError) as e:
        c.db.rollback()
        return redirect("/stock-events?tab=all", err=str(e))
    msg, ok = _import_message(c, f"Recorded {r['added']} stock change(s)", r, "; marked as already in StoreHub" if in_storehub else "")
    return redirect("/stock-events?tab=all", ok=msg) if ok else redirect("/stock-events?tab=all", err=msg)


@router.get("/stock-events/new")
def stock_event_new(kind: str = "write_off", location_id: int = 0, product_id: int = 0, c: Ctx = Depends(need("count.enter"))):
    if kind not in stockevents.KINDS:
        kind = "write_off"
    if location_id and location_id not in c.location_ids:
        location_id = 0  # a preselect is a hint, not an authorization; the POST checks access
    product = c.db.get(Product, product_id) if product_id else None
    suppliers = list(c.db.scalars(select(Supplier).where(Supplier.active.is_(True)).order_by(Supplier.name))) if kind == "unplanned_receipt" else []
    return render(c, "stock_event_new.html", kind=kind, reasons=stockevents.REASONS[kind], locations=c.locations(),
                  location_id=location_id, product=product, suppliers=suppliers)


@router.post("/stock-events/new")
async def stock_event_create(request: Request, c: Ctx = Depends(need("count.enter"))):
    form = await request.form()
    c.check_csrf(str(form.get("csrf", "")))
    kind = str(form.get("kind", ""))
    location_id = int(form.get("location_id") or 0)
    c.check_loc(location_id)
    back = f"/stock-events/new?kind={kind}&location_id={location_id}"
    p = c.db.get(Product, int(form.get("product_id") or 0))
    if p is None:  # no preselect: barcode/SKU first, then a unique name match (same pattern as the expiry form)
        s = str(form.get("search", "")).strip()
        p = c.db.scalar(select(Product).where(Product.barcode == s)) or c.db.scalar(select(Product).where(Product.sku == s))
        if p is None:
            found = list(c.db.scalars(select(Product).where(Product.name.like(f"%{s}%"), Product.in_api.is_(True)).limit(6)))
            if len(found) != 1:
                return redirect(back, err=f"{len(found)} products match '{s}'. Scan the barcode or type more of the name." if found else f"No product matches '{s}'")
            p = found[0]
    up = form.get("photo")
    photo = await save_upload(up) if hasattr(up, "filename") and up.filename else None
    try:
        stockevents.create(c.db, c.user, kind=kind, location_id=location_id, product_id=p.id, qty=parse_float(str(form.get("qty", ""))) or 0,
                           reason=str(form.get("reason", "")), note=str(form.get("note", "")).strip() or None, photo_path=photo,
                           supplier_id=int(form.get("supplier_id") or 0) or None)
    except stockevents.StockEventError as e:
        c.db.rollback()
        return redirect(back, err=str(e))
    return redirect("/stock-events?tab=all", ok="Recorded. Now key the same change into the StoreHub back office so stock updates.")


# ======================================================================================= expiry
@router.get("/expiry")
def expiry_page(loc: int = 0, c: Ctx = Depends(need("expiry.view"))):
    ids = [loc] if loc else c.location_ids
    if loc:
        c.check_loc(loc)
    batches = expirysvc.active_batches(c.db, ids)
    warn = cfg.get(c.db, "expiry.warn_days")
    today = today_local()
    rows = [(b, (b.expiry_date - today).days, expirysvc.stage_for((b.expiry_date - today).days, warn)) for b in batches]
    return render(c, "expiry.html", rows=rows, locations=c.locations(), loc=loc)


@router.get("/expiry/products")
def expiry_products(q: str = "", loc: int = 0, c: Ctx = Depends(need("expiry.view"))):
    from ..services.product_lookup import search
    if loc: c.check_loc(loc)
    return search(c.db, q, c.db.get(Location, loc) if loc else None)


@router.post("/expiry/add")
def expiry_add(product_id: int = Form(0), location_id: int = Form(...), search: str = Form(...), qty: str = Form(...), expiry: str = Form(...), batch_no: str = Form(""), csrf: str = Form(""),
               c: Ctx = Depends(need("expiry.view"))):
    c.check_csrf(csrf)
    c.check_loc(location_id)
    from fastapi.responses import JSONResponse
    ajax = c.request.headers.get("x-requested-with") == "fetch"
    def result(message, error=False):
        if ajax:
            return JSONResponse({"ok":not error,"message":message}, status_code=400 if error else 200)
        return redirect("/expiry", **{"err" if error else "ok":message})
    s = search.strip()
    p = c.db.get(Product, product_id) if product_id else (c.db.scalar(select(Product).where(Product.barcode == s)) or c.db.scalar(select(Product).where(Product.sku == s)))
    if product_id and (p is None or not p.in_api):
        return result("Choose a product currently in StoreHub", True)
    if p is None:
        found = list(c.db.scalars(select(Product).where(Product.name.like(f"%{s}%"), Product.in_api.is_(True)).limit(6)))
        if len(found) != 1:
            return result(f"{len(found)} products match '{s}'. Scan the barcode or type more of the name." if found else f"No product matches '{s}'", True)
        p = found[0]
    try:
        expirysvc.add_batch(c.db, c.user, location_id, p.id, parse_float(qty) or 0, parse_date(expiry), batch_no.strip() or None)
    except ValueError as e:
        return result(str(e), True)
    return result(f"Added {p.name}")


@router.get("/expiry/template.csv")
def expiry_template(c: Ctx = Depends(need("expiry.view"))):
    return Response(expirysvc.TEMPLATE_CSV, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="expiry-template.csv"'})


@router.post("/expiry/import")
async def expiry_import(file: UploadFile = File(...), location_id: int = Form(0), csrf: str = Form(""), c: Ctx = Depends(need("expiry.view"))):
    """Record many expiry batches from a CSV or Excel (.xlsx) file."""
    c.check_csrf(csrf)
    if location_id:
        c.check_loc(location_id)
    data = await file.read(5_000_001)
    if len(data) > 5_000_000:
        return redirect("/expiry", err=c.t("The file is larger than 5 MB"))
    try:
        r = expirysvc.import_batches(c.db, c.user, file.filename or "", data, c.locations(), location_id or None)
    except ValueError as e:
        c.db.rollback()
        return redirect("/expiry", err=str(e))
    msg = f"Imported {r['added']} batch(es)"
    if r["duplicates"]:
        msg += f"; {r['duplicates']} already recorded"
    if r["errors"]:
        msg += f"; skipped {len(r['errors'])}: " + "; ".join(f"row {n} {why}" for n, why in r["errors"][:5]) + (" ..." if len(r["errors"]) > 5 else "")
        return redirect("/expiry", err=msg) if not r["added"] else redirect("/expiry", ok=msg)
    return redirect("/expiry", ok=msg)


@router.post("/expiry/{bid}/clear")
def expiry_clear(bid: int, status: str = Form(...), note: str = Form(""), csrf: str = Form(""), c: Ctx = Depends(need("expiry.view"))):
    c.check_csrf(csrf)
    b = c.db.get(ExpiryBatch, bid)
    if b is None:
        raise HTTPException(404)
    c.check_loc(b.location_id)
    expirysvc.clear(c.db, c.user, b, status, note.strip() or None)
    return redirect("/expiry", ok="Updated" + (". Remember to enter the write-off in StoreHub." if status == "written_off" else ""))


# ======================================================================================= receiving
@router.get("/receiving/{rid}")
def receiving_page(rid: int, c: Ctx = Depends(need("receiving.count"))):
    rec = c.db.scalar(select(Receiving).where(Receiving.id == rid).options(selectinload(Receiving.lines).selectinload(receiving.ReceivingLine.product), selectinload(Receiving.po)))
    if rec is None:
        raise HTTPException(404)
    c.check_loc(rec.location_id)
    return render(c, "receiving.html", rec=rec)


@router.post("/receiving/{rid}/save")
async def receiving_save(rid: int, request: Request, c: Ctx = Depends(need("receiving.count"))):
    form = await request.form()
    c.check_csrf(str(form.get("csrf", "")))
    rec = c.db.scalar(select(Receiving).where(Receiving.id == rid).options(selectinload(Receiving.lines), selectinload(Receiving.po)))
    if rec is None:
        raise HTTPException(404)
    c.check_loc(rec.location_id)
    rows = [{"line_id": l.id, "counted": parse_float(form.get(f"c_{l.id}")), "damaged": parse_float(form.get(f"d_{l.id}")), "expiry": parse_date(form.get(f"e_{l.id}")),
             "batch": form.get(f"b_{l.id}"), "note": form.get(f"n_{l.id}")} for l in rec.lines]
    up = form.get("photo")
    if hasattr(up, "filename") and up.filename:
        rec.photo_path = await save_upload(up)
    try:
        receiving.save_counts(c.db, rec, c.user, rows)
        if form.get("complete"):
            receiving.complete(c.db, rec, c.user)
    except receiving.ReceivingError as e:
        c.db.rollback()
        return redirect(f"/receiving/{rid}", err=str(e))
    if form.get("complete"):
        return redirect(f"/purchase-orders/{rec.po_id}", ok="Receiving completed" + (". A difference was found and an issue was opened." if rec.status == "mismatch" else "."))
    return redirect(f"/receiving/{rid}", ok="Saved")


# ======================================================================================= permits and maintenance (proposal flow G)
@router.get("/compliance")
def compliance_page(c: Ctx = Depends(need("compliance.view"))):
    items = compsvc.active(c.db, c.location_ids)
    today = today_local()
    return render(c, "compliance.html", items=[(i, (i.due_date - today).days if i.due_date else None) for i in items], locations=c.locations(), today=today)


@router.post("/compliance/add")
def compliance_add(name: str = Form(...), processed: str = Form(""), expiry: str = Form(""), location_id: int = Form(0), reference: str = Form(""),
                   notes: str = Form(""), csrf: str = Form(""), c: Ctx = Depends(need("compliance.manage"))):
    c.check_csrf(csrf)
    if location_id:
        c.check_loc(location_id)
    try:
        compsvc.add(c.db, c.user, kind="permit", name=name, due=parse_date(expiry), processed=parse_date(processed), location_id=location_id or None, reference=reference.strip(), notes=notes.strip())
    except ValueError as e:
        return redirect("/compliance", err=str(e))
    compsvc.check(c.db)
    return redirect("/compliance", ok="Added")


@router.post("/compliance/{iid}/renew")
def compliance_renew(iid: int, new_due: str = Form(...), processed: str = Form(""), note: str = Form(""), csrf: str = Form(""), c: Ctx = Depends(need("compliance.manage"))):
    c.check_csrf(csrf)
    from ..models import ComplianceItem
    it = c.db.get(ComplianceItem, iid)
    if it is None:
        raise HTTPException(404)
    c.check_loc(it.location_id)
    try:
        compsvc.renew(c.db, c.user, it, parse_date(new_due), note.strip() or None, processed=parse_date(processed))
    except ValueError as e:
        return redirect("/compliance", err=str(e))
    return redirect("/compliance", ok="New date saved")


@router.post("/compliance/{iid}/retire")
def compliance_retire(iid: int, csrf: str = Form(""), c: Ctx = Depends(need("compliance.manage"))):
    c.check_csrf(csrf)
    from ..models import ComplianceItem
    it = c.db.get(ComplianceItem, iid)
    if it is None:
        raise HTTPException(404)
    c.check_loc(it.location_id)
    it.status = "retired"
    audit.log(c.db, actor_type="human", actor_id=c.user.id, action="compliance.retire", entity_type="compliance_item", entity_id=iid)
    return redirect("/compliance", ok="Removed from the list")


# ======================================================================================= performance
@router.get("/performance")
def performance_page(days: str = "60", frm: str = "", to: str = "", c: Ctx = Depends(need("performance.view"))):
    today = today_local()
    custom = days == "custom"
    until = None
    if custom:
        try:
            f_, t_ = date.fromisoformat(frm), date.fromisoformat(to)
            if f_ > t_ or not (7 <= (t_ - f_).days + 1 <= 365) or t_ > today:
                raise ValueError
            n_days = (t_ - f_).days + 1
            until = datetime.combine(t_, time(23, 59, 59)) - timedelta(hours=8)  # local end-of-day as UTC
        except ValueError:
            custom, frm, to, n_days = False, "", "", 60
    else:
        n_days = min(max(int(days) if days.isdigit() else 60, 7), 180)
    return render(c, "performance.html", m=performance.compute(c.db, c.location_ids, n_days, until=until), days=n_days, custom=custom, frm=frm, to=to)


def _iso(value):
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _stock_history(c: Ctx, loc: int, q: str, action: str, frm: str, to: str, page: int, export: str, show_unchanged: bool = False):
    """Every stock change StoreHub's back-office Audit Trail has recorded (read only), newest first."""
    import csv
    import io
    from ..models import Setting
    from ..services import stock_movements as sm
    db = c.db
    today = today_local()
    try:
        start, end = date.fromisoformat(frm), date.fromisoformat(to)
        if start > end or (end - start).days > 400:
            raise ValueError
    except ValueError:
        start, end = today - timedelta(days=13), today
    stores = [l for l in c.locations() if l.source == "storehub"]
    if loc:
        c.check_loc(loc)
    ids = [loc] if loc and any(l.id == loc for l in stores) else [l.id for l in stores]
    names = {l.id: l.name.replace("The Daily Centro - ", "") for l in stores}
    page = max(page, 1)
    if export == "csv":
        rows, _total = sm.movements(db, ids, start=start, end=end, search=q, action=action, limit=50000, offset=0, hide_unchanged=not show_unchanged)
        out = io.StringIO(); w = csv.writer(out)
        w.writerow(["Time", "Store", "Product", "Barcode", "Employee", "Action", "Qty before", "Change", "Qty after"])
        for m, p, l in rows:
            cell = lambda v: "'" + v if str(v).lstrip().startswith(("=", "+", "-", "@")) else v
            w.writerow([m.occurred_at.strftime("%Y-%m-%d %H:%M"), names.get(l.id, l.name), cell(p.name), p.barcode or "", cell(m.employee), cell(m.action), f"{m.qty_before:g}", f"{m.change:g}", f"{m.qty_after:g}"])
        audit.log(db, actor_type="user", actor_id=c.user.id, action="export_stock_changes", entity_type="report", entity_id="stock_changes", after={"rows": len(rows), "from": str(start), "to": str(end), "location": loc})
        db.commit()
        return Response("\ufeff" + out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="stock-changes-{start}-to-{end}.csv"'})
    rows, total = sm.movements(db, ids, start=start, end=end, search=q, action=action, limit=HISTORY_PAGE, offset=(page - 1) * HISTORY_PAGE, hide_unchanged=not show_unchanged)
    last_ok = db.get(Setting, "stock_movements.last_ok")
    scope = dict(start=start, end=end, search=q, action=action, hide_unchanged=not show_unchanged)
    units_in, units_out = sm.totals(db, ids, **scope)
    per_day = sm.day_totals(db, ids, **scope)
    groups: list[dict] = []   # the page is shown a day at a time, newest first
    for row in rows:
        d = row[0].occurred_at.date()
        if not groups or groups[-1]["date"] != d:
            got_in, got_out = per_day.get(d.isoformat(), (0.0, 0.0))
            groups.append({"date": d, "rows": [], "in": got_in, "out": got_out, "rel": "Today" if d == today else "Yesterday" if d == today - timedelta(days=1) else ""})
        groups[-1]["rows"].append(row)
    return render(c, "stock_history.html", rows=rows, groups=groups, units_in=units_in, units_out=units_out, today=today, total=total, page=page, pages=max(1, (total + HISTORY_PAGE - 1) // HISTORY_PAGE), stores=stores, names=names, loc=loc, q=q, action=action, unchanged=show_unchanged,
                  actions=sm.actions(db, ids), frm=start.isoformat(), to=end.isoformat(), waiting=len(sm.pending(db)), last_ok=_iso(last_ok.value if last_ok else None), tab="history")
