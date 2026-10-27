"""Builds the structured context the AI is allowed to see. The AI never receives a raw database dump.

Everything in the context is a numbered FACT written by code from the database. The model must cite fact ids, and every number it states
is checked against these facts afterwards (see validate.py). Numbers therefore always come from systems and code, never from the model.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import settings_service as cfg
from ..models import (DqEvent, ExpiryBatch, Inspection, InventoryCurrent, Issue, Location, Product, Recommendation, SalesDay, SalesDayProduct, User)
from ..services import expiry, freshness, purchasing, replenishment
from ..timeutil import fmt_clock, fmt_local, now_utc, today_local

CONTEXT_VERSION = "ctx-1.0"
NUM = re.compile(r"-?\d[\d,]*\.?\d*")
_NOISE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}|#\d+|\[F\d+\]|(?<![A-Za-z0-9])F\d+(?![A-Za-z0-9])|(?<![A-Za-z0-9])P[0-3](?![A-Za-z0-9])")  # dates, times, ids, fact ids, severities are labels, not claims


def numbers_in(text: str) -> set[str]:
    """Normalised numeric tokens: '6,760.28' -> '6760.28', '12.0' -> '12'."""
    out = set()
    for tok in NUM.findall(_NOISE.sub(" ", text)):
        t = tok.replace(",", "").rstrip(".")
        if not t or t in ("-",):
            continue
        try:
            v = float(t)
        except ValueError:
            continue
        out.add(f"{v:g}")
        out.add(f"{round(v, 1):g}")
        out.add(f"{round(v):g}")
    return out


@dataclass
class Fact:
    id: str
    section: str
    text: str
    link: str | None = None

    @property
    def nums(self) -> set[str]:
        return numbers_in(self.text)


@dataclass
class Context:
    facts: list[Fact] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def add(self, section: str, text: str, link: str | None = None) -> str:
        fid = f"F{len(self.facts) + 1}"
        self.facts.append(Fact(fid, section, text, link))
        return fid

    def as_prompt(self) -> str:
        lines = [f"[{f.id}] ({f.section}) {f.text}" for f in self.facts]
        return "\n".join(lines)

    def as_json(self) -> dict:
        return {"version": CONTEXT_VERSION, "meta": self.meta, "facts": [{"id": f.id, "section": f.section, "text": f.text, "link": f.link} for f in self.facts]}

    @property
    def hash(self) -> str:
        return hashlib.sha1(json.dumps(self.as_json(), sort_keys=True, default=str).encode()).hexdigest()

    def fact(self, fid: str) -> Fact | None:
        return next((f for f in self.facts if f.id == fid), None)

    def all_numbers(self) -> set[str]:
        s: set[str] = set()
        for f in self.facts:
            s |= f.nums
        return s


def _money(v: float) -> str:
    return f"{v:,.2f}"


def build(db: Session, user: User | None, location_ids: list[int]) -> Context:
    """Store, sales, inventory, replenishment, operations, decision history and policy context, scoped to the user's locations."""
    ctx = Context()
    today = today_local()
    locs = {l.id: l for l in db.scalars(select(Location).where(Location.id.in_(location_ids or [0])))}
    ctx.meta = {"business_date": str(today), "generated_at_utc": now_utc().isoformat(timespec="seconds"), "currency": "PHP",
                "locations": [l.name for l in locs.values()], "timezone": "Asia/Manila"}

    # ---- data freshness by selected source/account
    selected_locs = list(db.scalars(select(Location).where(Location.id.in_(location_ids or [0]))))
    accounts = {loc.source_account for loc in selected_locs if loc.source == 'loyverse'}
    if any(loc.source == 'storehub' for loc in selected_locs) or not accounts:
        for f in freshness.freshness(db):
            ctx.add('freshness', f"StoreHub {f['label']} data is {f['status']}", '/sync')
    if accounts:
        from ..models import LoyverseAccount
        for st in db.scalars(select(LoyverseAccount).where(LoyverseAccount.key.in_(accounts))):
            ctx.add('freshness', f"Loyverse {st.key}: last successful reporting sync {fmt_local(st.reporting_success_at) if st.reporting_success_at else 'never'}; history starts {st.reporting_history_from}; error {st.reporting_error or 'none'}", '/sync')
        ctx.add('source_rules', 'Loyverse purchase orders, incoming transfers and staff attendance are unavailable. Do not interpret unavailable data as zero. Loyverse replenishment uses sold items and sales only. Local counts, wastage and expiry records do not update the POS.', '/replenishment/loyverse')

    # ---- store and sales context
    y = today - timedelta(days=1)
    for lid, loc in locs.items():
        if loc.type != "store":
            continue
        day = db.get(SalesDay, (lid, y))
        hist = [r.net_sales for r in db.scalars(select(SalesDay).where(SalesDay.location_id == lid, SalesDay.business_date >= y - timedelta(days=14),
                                                                         SalesDay.business_date < y))]
        if day:
            avg = sum(hist) / len(hist) if hist else None
            rel = f"; the 14-day average is {_money(avg)}" if avg else ""
            ctx.add("sales", f"{loc.name}: net sales yesterday ({y}) were {_money(day.net_sales)} from {day.tx_count} transactions, "
                             f"{day.cancelled_count} cancelled{rel}", "/sales")
        now_day = db.get(SalesDay, (lid, today))
        ctx.add("sales", f"{loc.name}: net sales TODAY ({today}) so far are {_money(now_day.net_sales)} from {now_day.tx_count} transactions (the day is not finished)" if now_day
                else f"{loc.name}: no sales recorded yet TODAY ({today})", "/sales")
        top = db.execute(select(Product.name, func.sum(SalesDayProduct.units), func.sum(SalesDayProduct.net_sales)).join(Product, Product.id == SalesDayProduct.product_id)
                         .where(SalesDayProduct.location_id == lid, SalesDayProduct.business_date >= y - timedelta(days=6), SalesDayProduct.business_date <= y)
                         .group_by(Product.name).order_by(func.sum(SalesDayProduct.net_sales).desc()).limit(5)).all()
        if top:
            ctx.add("sales", f"{loc.name}: best sellers in the 7 days to {y}: " + "; ".join(f"{n} {u:,.0f} units, {_money(s)}" for n, u, s in top), "/sales/products")

    # ---- inventory risk
    neg = dict(db.execute(select(InventoryCurrent.location_id, func.count()).where(InventoryCurrent.on_hand < 0, InventoryCurrent.location_id.in_(location_ids or [0]))
                          .group_by(InventoryCurrent.location_id)).all())
    for lid, n in neg.items():
        ctx.add("inventory", f"{locs[lid].name}: {n} items have negative stock on hand (usually unrecorded deliveries or transfers)", "/inventory?status=negative")

    # ---- replenishment context
    sales_only = cfg.get(db, "repl.mode") != "stock_and_sales"
    ctx.meta["replenishment_policy"] = {"mode": cfg.get(db, "repl.mode"), "cover_days": cfg.get(db, "repl.sales_only_cover_days")}
    ctx.add("policy", (f"StoreHub replenishment is sales-only: max(0, average daily sales x {cfg.get(db, 'repl.sales_only_cover_days')} cover days - incoming), then pack rounding, MOQ and caps. Stock on hand, safety stock and lead-time cover do not determine suggestions. Days of stock cover is unavailable, not zero. Recently ordered products are suppressed during the cover period."
                       if sales_only else "StoreHub replenishment uses sales and stock: target = average daily sales x (lead time + review period) + safety stock; order = max(0, target - on hand - incoming), then pack rounding, MOQ and caps."), "/replenishment")
    recs = list(db.scalars(select(Recommendation).where(Recommendation.business_date == today, Recommendation.location_id.in_(location_ids or [0]),
                                                        Recommendation.status.in_(("pending", "needs_review")))))
    pend = [r for r in recs if r.status == "pending"]
    nr = [r for r in recs if r.status == "needs_review"]
    if recs:
        ctx.add("replenishment", f"{len(pend)} order suggestions are ready for a decision, worth {_money(sum(r.value for r in pend))} in total; "
                                 f"{len(nr)} more need review because of data problems", "/replenishment")
        reasons: dict[str, int] = {}
        for r in nr:
            for x in r.needs_review_reasons or []:
                reasons[x] = reasons.get(x, 0) + 1
        for k, n in sorted(reasons.items(), key=lambda kv: -kv[1])[:4]:
            ctx.add("replenishment", f"{n} suggestions need review because of: {k.replace('_', ' ')}", "/replenishment?status=needs_review")
        for r in sorted(pend, key=lambda r: -r.value)[:8]:
            ctx.add("replenishment", f"Suggestion #{r.id}: {r.product.name} at {r.location.name} ({'transfer from warehouse' if r.source_kind == 'transfer' else 'supplier order'}): "
                                     f"on hand {r.on_hand:g}, incoming {r.incoming:g}, average daily sales {r.ads:g}, target stock {r.target_stock:g}, "
                                     f"suggested quantity {r.final_qty:g} worth {_money(r.value)}, {('stock cover unavailable' if sales_only or r.days_cover is None else str(r.days_cover) + ' days of stock cover')}, "
                                     f"confidence {replenishment.confidence_label(r.confidence)}", "/replenishment")
        low = [] if sales_only else sorted([r for r in recs if r.days_cover is not None and r.days_cover < max(r.lead_time, 1) and r.final_qty > 0], key=lambda r: r.days_cover)[:6]
        for r in low:
            ctx.add("stockout_risk", f"Stockout risk: {r.product.name} at {r.location.name} has {r.days_cover:.1f} days of cover but lead time is {r.lead_time:g} days "
                                     f"(suggestion #{r.id}, status {r.status})", "/replenishment")

    if accounts:
        from ..loyverse.replenishment import report as loyverse_report
        allowed = {(loc.source_account, loc.external_id) for loc in selected_locs if loc.source == 'loyverse'}
        data = loyverse_report(db, allowed_pairs=allowed)
        ctx.add('replenishment', f"Loyverse has {len(data['rows'])} sales-only demand suggestions. Formula: net sold / store days with sales times cover days, rounded up. No stock or incoming deductions, no automatic orders.", '/replenishment/loyverse')
        for row in data['rows'][:12]:
            ctx.add('replenishment', f"Loyverse {row['store']}: {row['name']}, net units sold {row['units']}, sales days {row['days']}, suggested {row['quantity'] if row['quantity'] is not None else 'unavailable'}; {row['reason']}", '/replenishment/loyverse')

    # ---- purchase orders
    _, stale = purchasing.incoming_map(db)
    stale = [p for p in stale if p.location_id in location_ids]
    if stale:
        units = sum(max(0, l.ordered_qty - l.received_qty) for p in stale for l in p.lines)
        ctx.add("purchase_orders", f"{len(stale)} open purchase orders are more than {cfg.get(db, 'po.stale_days')} days past their expected date and are not counted as incoming stock "
                                   f"({units:g} units)", "/purchase-orders")
        for p in stale[:4]:
            late = (now_utc() - p.expected_at).days if p.expected_at else None
            ctx.add("purchase_orders", f"PO #{p.sh_po_id} ({p.supplier.name if p.supplier else 'no supplier'}) is {p.status}" + (f", {late} days past its date" if late is not None else ""),
                    f"/purchase-orders/{p.id}")

    # ---- operations
    opn = list(db.scalars(select(Issue).where(Issue.status.in_(issues_open()), (Issue.location_id.in_(location_ids or [0])) | Issue.location_id.is_(None))))
    if opn:
        by = {}
        for i in opn:
            by[i.severity] = by.get(i.severity, 0) + 1
        ctx.add("issues", "Open issues by severity: " + ", ".join(f"{k} {v}" for k, v in sorted(by.items())), "/issues")
        for i in sorted(opn, key=lambda i: (i.severity, i.due_at))[:8]:
            over = " (overdue)" if i.due_at < now_utc() else ""
            ctx.add("issues", f"Issue #{i.id} [{i.severity}] {i.title}: status {i.status}, due {fmt_local(i.due_at)}{over}, escalation level {i.escalation_level}", f"/issues/{i.id}")
    late = list(db.scalars(select(Inspection).where(Inspection.business_date == today, Inspection.location_id.in_(location_ids or [0]),
                                                    Inspection.status.in_(("missed", "pending", "in_progress")), Inspection.due_at < now_utc())))
    for i in late[:6]:
        ctx.add("inspections", f"{i.template.name} at {i.location.name} was due at {fmt_clock(i.due_at)} and is {i.status}", f"/inspections/{i.id}")
    warn = cfg.get(db, "expiry.warn_days")
    for b in [b for b in expiry.active_batches(db, location_ids) if (b.expiry_date - today).days <= min(warn)][:6]:
        ctx.add("expiry", f"{b.product.name} at {b.location.name}: {b.qty:g} units expire on {b.expiry_date} ({(b.expiry_date - today).days} days)", "/expiry")

    # ---- data quality
    for rule, n in db.execute(select(DqEvent.rule, func.count()).where(DqEvent.resolved_at.is_(None)).group_by(DqEvent.rule).order_by(func.count().desc()).limit(6)):
        ctx.add("data_quality", f"{n} open data-quality findings of type {rule.replace('_', ' ')}", f"/data-quality?rule={rule}")

    # ---- decision history
    m = replenishment.metrics(db)
    if m["decided"]:
        ctx.add("decision_history", f"In the last 60 days managers decided {m['decided']} suggestions: {m['accepted']} accepted as suggested, {m['modified']} modified, {m['rejected']} rejected", "/replenishment")
    for r in db.scalars(select(Recommendation).where(Recommendation.decided_at.is_not(None), Recommendation.status.in_(("modified", "rejected"))).order_by(Recommendation.decided_at.desc()).limit(5)):
        # a manager's written reason is the most valuable context we own: feed it back into the next answer
        note = f"; the manager wrote: {r.decision_note[:150]}" if r.decision_note else ""
        ctx.add("decision_history", f"{r.product.name} at {r.location.name}: suggested {r.final_qty:g}, manager {r.status} to {r.approved_qty:g}, "
                                    f"reason: {r.reason_code or 'none'}{note}", "/replenishment")

    # ---- policy
    ctx.add("policy", f"A store manager may approve an order line up to {_money(float(cfg.get(db, 'repl.approval_limit_manager')))}; above that management must approve", None)
    sla = cfg.get(db, "issue.sla")
    ctx.add("policy", "Issue SLA (acknowledge within minutes, due within hours): " + ", ".join(f"{k} {v[0]} min / {v[1]} h" for k, v in sla.items()), None)
    return ctx


def issues_open() -> tuple[str, ...]:
    return ("open", "acknowledged", "in_progress", "resolved")
