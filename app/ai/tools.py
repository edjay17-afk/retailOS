"""The AI tool layer. Tools are the only things the AI can ask the system to do.

  read  : safe, run automatically to fetch more facts while answering a question
  low   : low-risk writes (create issue, assign, remind, draft a count list), run only through the policy gate
  high  : approve an order, supplier orders, write-offs, price changes: always need a person; some cannot be done by this system at all

The AI never writes SQL and never changes data directly. Every call is checked by the policy gate and recorded in the audit log.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..models import (Inspection, InventoryCurrent, Issue, Location, Product, PurchaseOrder, Recommendation, SalesDay, SalesDayProduct, User)
from ..security import allowed_location_ids, has_perm
from ..services import counts as countsvc
from ..services import issues, notifications, purchasing, replenishment
from ..timeutil import fmt_local, now_utc, today_local


class ToolError(Exception):
    pass


@dataclass
class Tool:
    name: str
    risk: str  # read | low | high
    perm: str  # permission the acting person must hold
    doc: str
    required: tuple[str, ...]
    run: Callable[[Session, User, dict], dict]
    executable: bool = True  # False: this system cannot do it (StoreHub has no write API); approval records the decision for a person to carry out


def _loc(db: Session, user: User, ref) -> Location:
    ids = allowed_location_ids(db, user)
    q = select(Location).where(Location.id.in_(ids or [0]))
    if isinstance(ref, int) or str(ref).isdigit():
        loc = db.scalar(q.where(Location.id == int(ref)))
    else:
        loc = db.scalar(q.where(func.lower(Location.name).like(f"%{str(ref).lower()}%")))
    if loc is None:
        raise ToolError(f"unknown location or no access: {ref}")
    return loc


# ---------------------------------------------------------------------------------------------------- read tools (return fact lines)
def t_get_inventory(db: Session, user: User, a: dict) -> dict:
    loc = _loc(db, user, a.get("location"))
    status = a.get("status", "all")
    limit = min(int(a.get("limit", 12)), 15)
    q = (select(InventoryCurrent, Product).join(Product, Product.id == InventoryCurrent.product_id).where(InventoryCurrent.location_id == loc.id))
    if a.get("query"):
        q = q.where(Product.name.like(f"%{a['query']}%"))
    if status == "negative":
        q = q.where(InventoryCurrent.on_hand < 0)
    elif status == "zero":
        q = q.where(InventoryCurrent.on_hand == 0)
    start = today_local() - timedelta(days=14)
    sold = {pid: u for pid, u in db.execute(select(SalesDayProduct.product_id, func.sum(SalesDayProduct.units)).where(
        SalesDayProduct.location_id == loc.id, SalesDayProduct.business_date >= start, SalesDayProduct.business_date < today_local()).group_by(SalesDayProduct.product_id))}
    days = db.scalar(select(func.count()).select_from(SalesDay).where(SalesDay.location_id == loc.id, SalesDay.business_date >= start, SalesDay.tx_count > 0)) or 1
    rows = db.execute(q.order_by(InventoryCurrent.on_hand).limit(limit)).all()
    inc, _ = purchasing.incoming_map(db)
    facts = []
    for inv, p in rows:
        ads = sold.get(p.id, 0) / days
        cover = f", {max(inv.on_hand, 0) / ads:.1f} days of cover" if ads > 0 else ""
        incoming = f"{inc.get((loc.id, p.id), 0):g}" if loc.source == 'storehub' else 'unavailable from the Loyverse API'
        facts.append(f"{p.name} at {loc.name}: on hand {inv.on_hand:g}, incoming {incoming}, average daily sales {ads:.2f}{cover}; stock synced {fmt_local(inv.synced_at)}")
    return {"facts": facts or [f"No products at {loc.name} match that search"]}


def t_get_sales_history(db: Session, user: User, a: dict) -> dict:
    loc = _loc(db, user, a.get("location"))
    days = min(int(a.get("days", 7)), 30)
    rows = db.scalars(select(SalesDay).where(SalesDay.location_id == loc.id, SalesDay.business_date >= today_local() - timedelta(days=days)).order_by(SalesDay.business_date.desc()))
    return {"facts": [f"{loc.name} on {r.business_date}: net sales {r.net_sales:,.2f}, {r.tx_count} transactions, cash {r.cash_sales:,.2f}" for r in rows]}


def t_get_product_sales(db: Session, user: User, a: dict) -> dict:
    """Sales per product at one location: the top sellers, or the products whose name matches `query`."""
    loc = _loc(db, user, a.get("location"))
    days = min(max(int(a.get("days", 7)), 1), 60)
    start = today_local() - timedelta(days=days - 1)
    q = (a.get("query") or "").strip()
    stmt = (select(Product.name, func.sum(SalesDayProduct.units), func.sum(SalesDayProduct.net_sales)).join(Product, Product.id == SalesDayProduct.product_id)
            .where(SalesDayProduct.location_id == loc.id, SalesDayProduct.business_date >= start))
    if q:
        stmt = stmt.where(Product.name.ilike(f"%{q}%"))
    rows = db.execute(stmt.group_by(Product.name).order_by(func.sum(SalesDayProduct.net_sales).desc()).limit(min(int(a.get("limit", 10)), 15))).all()
    if not rows:
        return {"facts": [f"No product sales at {loc.name} in the last {days} days" + (f" matching '{q}'" if q else "")]}
    return {"facts": [f"{loc.name}, last {days} days: {n} sold {u:,.0f} units for net sales {s:,.2f}" for n, u, s in rows]}


def t_get_open_purchase_orders(db: Session, user: User, a: dict) -> dict:
    ids = allowed_location_ids(db, user)
    if db.info.get('source_pairs') and all(source == 'loyverse' for source, _ in db.info['source_pairs']):
        return {'facts':['Purchase orders and incoming transfer quantities are unavailable from the Loyverse API.']}
    stale_days = 7
    out = []
    for po in db.scalars(select(PurchaseOrder).where(PurchaseOrder.status.in_(purchasing.OPEN_STATUSES), PurchaseOrder.missing_from_api.is_(False),
                                                     (PurchaseOrder.location_id.in_(ids or [0])) | PurchaseOrder.location_id.is_(None)).order_by(PurchaseOrder.expected_at).limit(12)):
        kind = purchasing.classify_po(po, stale_days)
        rem = sum(max(0, l.ordered_qty - l.received_qty) for l in po.lines)
        out.append(f"PO #{po.sh_po_id} ({po.supplier.name if po.supplier else 'no supplier'}) is {po.status}, {kind}, {rem:g} units still to arrive, expected {fmt_local(po.expected_at, '%Y-%m-%d') if po.expected_at else 'unknown'}")
    return {"facts": out or ["There are no open purchase orders"]}


def t_get_issue_status(db: Session, user: User, a: dict) -> dict:
    ids = allowed_location_ids(db, user)
    i = db.get(Issue, int(a.get("issue_id", 0)))
    if i is None or (i.location_id and i.location_id not in ids):
        raise ToolError("issue not found")
    return {"facts": [f"Issue #{i.id} [{i.severity}] {i.title}: status {i.status}, owner {i.owner.full_name or i.owner.username if i.owner else 'unassigned'}, due {fmt_local(i.due_at)}, escalation level {i.escalation_level}"]}


def t_get_inspection_history(db: Session, user: User, a: dict) -> dict:
    loc = _loc(db, user, a.get("location"))
    days = min(int(a.get("days", 7)), 14)
    rows = db.scalars(select(Inspection).where(Inspection.location_id == loc.id, Inspection.business_date >= today_local() - timedelta(days=days)).order_by(Inspection.business_date.desc()).limit(30))
    return {"facts": [f"{r.template.name} at {loc.name} on {r.business_date}: {r.status}, {r.fail_count} failed items" for r in rows]}


def t_get_store_context(db: Session, user: User, a: dict) -> dict:
    loc = _loc(db, user, a.get("location"))
    neg = db.scalar(select(func.count()).select_from(InventoryCurrent).where(InventoryCurrent.location_id == loc.id, InventoryCurrent.on_hand < 0)) or 0
    opn = db.scalar(select(func.count()).select_from(Issue).where(Issue.location_id == loc.id, Issue.status.in_(issues.OPEN_STATES))) or 0
    return {"facts": [f"{loc.name} ({loc.type}) has {neg} items with negative stock and {opn} open issues"]}


# ---------------------------------------------------------------------------------------------------- low-risk write tools
def t_create_issue(db: Session, user: User, a: dict) -> dict:
    loc = _loc(db, user, a["location"]) if a.get("location") else None
    sev = a.get("severity", "P2")
    i = issues.create_issue(db, type=a.get("type", "other"), severity=sev, title=str(a["title"])[:200], description=a.get("description"),
                            location_id=loc.id if loc else None, source="ai", created_by=user.id)
    return {"issue_id": i.id, "summary": f"Created issue #{i.id} [{i.severity}] {i.title}", "link": f"/issues/{i.id}"}


def t_assign_issue(db: Session, user: User, a: dict) -> dict:
    i = db.get(Issue, int(a["issue_id"]))
    if i is None:
        raise ToolError("issue not found")
    target = a.get("user")
    u = db.get(User, int(target)) if str(target).isdigit() else db.scalar(select(User).where(func.lower(User.username) == str(target).lower()))
    if u is None:
        raise ToolError(f"unknown user {target}")
    issues.act(db, i, user, "assign", note="Assigned on AI suggestion", assignee_id=u.id)
    return {"summary": f"Assigned issue #{i.id} to {u.full_name or u.username}", "link": f"/issues/{i.id}"}


def t_send_reminder(db: Session, user: User, a: dict) -> dict:
    msg = str(a["message"])[:250]
    role, target = a.get("role"), a.get("user")
    if target:
        u = db.get(User, int(target)) if str(target).isdigit() else db.scalar(select(User).where(func.lower(User.username) == str(target).lower()))
        if u is None:
            raise ToolError("unknown user")
        notifications.notify(db, user_id=u.id, title=msg, level="warning", link=a.get("link"))
        who = u.full_name or u.username
    elif role:
        from ..security import ROLES
        r = str(role).strip().lower().replace(" ", "_").replace("-", "_")
        if r not in ROLES:
            raise ToolError(f"unknown role {role}")
        notifications.notify(db, role=r, title=msg, level="warning", link=a.get("link"))
        who = f"everyone with role {r.replace('_', ' ')}"
    else:
        raise ToolError("say who the reminder is for")
    return {"summary": f"Sent a reminder to {who}"}


def t_create_count_list(db: Session, user: User, a: dict) -> dict:
    loc = _loc(db, user, a["location"])
    cc = countsvc.create(db, user, loc, int(a.get("size", 30)))
    return {"summary": f"Prepared today's cycle count list for {loc.name} ({len(cc.lines)} items)", "link": f"/counts/{cc.id}"}


# ---------------------------------------------------------------------------------------------------- high-risk tools (always a person decides)
def t_approve_replenishment(db: Session, user: User, a: dict) -> dict:
    rec = db.get(Recommendation, int(a["rec_id"]))
    if rec is None:
        raise ToolError("suggestion not found")
    # an item that needs review can only be approved when the person writes what they checked; the AI never fills that in
    replenishment.decide(db, rec, user, "approve", note=a.get("note"))
    return {"summary": f"Approved suggestion #{rec.id}: {rec.product.name} x {rec.approved_qty:g}", "link": "/replenishment"}


def t_manual_in_storehub(db: Session, user: User, a: dict) -> dict:
    return {"summary": "Approved. This system cannot do it in StoreHub, so a person must carry it out in the StoreHub back office: " + ", ".join(f"{k} {v}" for k, v in a.items())}


from . import report_tools

TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("get_filtered_sales", "read", "sales.view", "Daily sales and previous equal-period totals. args: location?, frm?, to?, days?", (), report_tools.sales_history),
    Tool("get_filtered_product_sales", "read", "sales.view", "Exact filtered sales, including individual product daily sales. args: location? (0=all accessible), frm?, to? (ISO dates), days?, supplier? (ID), category?, query?, product_id?, sort? (sales|units|name|price). Totals cover all matches; at most 20 products shown.", (), report_tools.product_sales),
    Tool("get_supplier_sales", "read", "sales.view", "Sales grouped by supplier. args: location?, frm?, to?, days?", (), report_tools.supplier_sales),
    Tool("get_transfer_sheets", "read", "transfer.sheet", "Recent transfer sheets or one sheet's lines. args: location? (destination), sheet_id?", (), report_tools.transfers),
    Tool("get_expiry_batches", "read", "expiry.view", "Active expired or expiring batches. args: location?, days? (ahead, default 30)", (), report_tools.expiry),
    Tool("get_stock_counts", "read", "count.enter", "Recent counts or one count's product differences. args: location?, count_id?", (), report_tools.counts),
    Tool("get_lark_inspections", "read", "inspection.do", "Read Lark daily inspection reports, not pass/fail outcomes. args: location?, date? (ISO, default today)", (), report_tools.lark_reports),
    Tool("prepare_transfer_sheet", "high", "transfer.sheet", "Prepare a warehouse-to-store draft, only after approval. An existing draft for the destination may be reused and quantities added. args: location (destination ID), lines (list of {product_id, qty}, whole units). Never infer quantities without evidence or user instruction.", ("location", "lines"), report_tools.prepare_transfer),
    Tool("get_store_context", "read", "dashboard.view", "Summary of one location. args: location", ("location",), t_get_store_context),
    Tool("get_inventory", "read", "inventory.view", "Stock of a location. args: location, query?, status? (all|negative|zero), limit?", ("location",), t_get_inventory),
    Tool("get_sales_history", "read", "sales.view", "Daily sales of a location. args: location, days?", ("location",), t_get_sales_history),
    Tool("get_product_sales", "read", "sales.view", "Sales per product of a location. args: location, query? (part of a product name), days? (default 7), limit?", ("location",), t_get_product_sales),
    Tool("get_open_purchase_orders", "read", "po.view", "Open purchase orders. no args", (), t_get_open_purchase_orders),
    Tool("get_issue_status", "read", "issue.view", "One issue. args: issue_id", ("issue_id",), t_get_issue_status),
    Tool("get_inspection_history", "read", "inspection.do", "Recent checklists of a location. args: location, days?", ("location",), t_get_inspection_history),
    Tool("create_issue", "low", "issue.manage", "Open an issue. args: title, severity (P0-P3), location?, description?", ("title", "severity"), t_create_issue),
    Tool("assign_issue", "low", "issue.manage", "Assign an issue to a person. args: issue_id, user", ("issue_id", "user"), t_assign_issue),
    Tool("send_reminder", "low", "issue.manage", "Send an in-app reminder. args: message, user? or role?", ("message",), t_send_reminder),
    Tool("create_count_list", "low", "count.enter", "Prepare today's cycle count list. args: location, size?", ("location",), t_create_count_list),
    Tool("approve_replenishment", "high", "repl.decide", "Approve an order suggestion as suggested. args: rec_id", ("rec_id",), t_approve_replenishment),
    Tool("submit_supplier_order", "high", "repl.decide", "Send an order to a supplier. args: batch_id, supplier", ("batch_id",), t_manual_in_storehub, executable=False),
    Tool("write_off_stock", "high", "repl.decide", "Write off stock. args: location, product, qty, reason", ("location", "product", "qty"), t_manual_in_storehub, executable=False),
    Tool("change_price", "high", "settings.manage", "Change a selling price. args: product, new_price", ("product", "new_price"), t_manual_in_storehub, executable=False),
]}


def prompt_catalog(kinds=("read", "low", "high")) -> str:
    return "\n".join(f"- {t.name} ({t.risk}): {t.doc}" for t in TOOLS.values() if t.risk in kinds)


def check_args(tool: Tool, args: dict) -> str | None:
    missing = [k for k in tool.required if args.get(k) in (None, "")]
    return f"missing {', '.join(missing)}" if missing else None
