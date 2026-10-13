"""Replenishment engine V1: deterministic, explainable, reviewable. No black-box ordering.

    ADS            = valid units sold in the lookback / valid trading days
    Target stock   = ADS x (lead time + review period) + safety stock        (safety stock = ADS x safety days)
    Position       = on hand + incoming                                       (StoreHub gives no reserved quantity)
    Raw order      = max(0, target - position)
    Final order    = rounded to case pack and order multiple, raised to MOQ, capped at maximum stock

Stores are replenished by transfer from the warehouse. The warehouse is replenished from suppliers, using the combined
demand of all stores. A run is blocked when sales, inventory or purchase-order data is stale.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import timedelta, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import audit
from .. import settings_service as cfg
from ..models import (DqEvent, InventoryCurrent, InventorySnapshot, Location, OrderBatch, PoLine, Product, PurchaseOrder,
                      Recommendation, ReplRun, SalesDay, SalesDayProduct, Supplier, User)
from ..timeutil import now_utc, today_local
from . import freshness, purchasing

CALC_VERSION = "v1.0"
DECIDED = ("approved", "modified", "rejected", "ordered")

# Human labels for needs_review reason codes (fix links live in services/dq.FIXES)
REASON_LABELS = {
    "negative_stock": "stock on hand is negative",
    "no_supplier": "no supplier is set",
    "no_case_pack": "case pack is unknown",
    "conflicting_po": "a stale open PO exists",
    "short_history": "sales history is short",
    "data_quality": "a critical data-quality finding is open",
}


def _ceil_to(q: float, step: float) -> float:
    return math.ceil(round(q / step, 9)) * step if step and step > 0 else q


def _floor_to(q: float, step: float) -> float:
    return math.floor(round(q / step, 9)) * step if step and step > 0 else q


def confidence_label(c: float) -> str:
    return "High" if c >= 0.8 else "Medium" if c >= 0.6 else "Low"


# ======================================================================================= pure calculation
def calculate(*, ads: float, trading_days: int, lookback: int, on_hand: float, incoming: float, lead: float, review: float,
              safety_days: float, case_pack: float | None, order_multiple: float | None, moq: float | None,
              max_stock: float | None, max_cover_days: float, min_trading_days: int, use_stock: bool = True, cover_days: float = 7.0) -> dict:
    """The V1 formula as a pure function (unit-tested against hand calculations).
    With use_stock=False (sales-only mode) stock on hand is ignored: the order covers `cover_days` of sales, minus what is already on order."""
    steps: list[dict] = []
    if use_stock:
        safety = ads * safety_days
        target = ads * (lead + review) + safety
        usable_on_hand = max(on_hand, 0.0)
        position = usable_on_hand + incoming
    else:
        safety = 0.0
        target = ads * cover_days
        usable_on_hand = 0.0
        position = incoming
    raw = max(0.0, target - position)
    steps.append({"step": "raw", "qty": round(raw, 3)})
    qty = raw
    cp = case_pack if case_pack and case_pack > 0 else 1.0
    if qty > 0:
        new = _ceil_to(qty, cp)
        if new != qty:
            steps.append({"step": "case_pack", "case_pack": cp, "from": round(qty, 3), "to": new})
        qty = new
        if order_multiple and order_multiple > 1 and abs(order_multiple - cp) > 1e-9:
            new = _ceil_to(qty, order_multiple)
            if new != qty:
                steps.append({"step": "order_multiple", "multiple": order_multiple, "from": qty, "to": new})
            qty = new
        if moq and qty < moq:
            steps.append({"step": "moq", "moq": moq, "from": qty, "to": moq})
            qty = _ceil_to(moq, cp)
        cap = max_stock if max_stock else ads * max_cover_days
        if cap and position + qty > cap:
            new = max(0.0, _floor_to(cap - position, cp))
            steps.append({"step": "max_stock", "cap": round(cap, 3), "from": qty, "to": new})
            qty = new
    days_cover = (usable_on_hand / ads) if (ads > 0 and use_stock) else None
    return {"ads": ads, "safety_stock": safety, "target": target, "position": position, "raw": raw, "final": qty,
            "cases": (qty / cp) if cp and cp != 1.0 else None, "days_cover": days_cover, "steps": steps,
            "history_short": trading_days < min_trading_days, "history_partial": trading_days < lookback * 0.6}


# ======================================================================================= replay
TOL = 0.01  # stored values are rounded to 3-4 decimals


def replay(db: Session, rec: Recommendation) -> dict:
    """Recompute a stored suggestion from its own recorded inputs and run settings, and compare.

    A suggestion must be reproducible from its snapshot; the test suite replays every stored suggestion, so a
    formula change that silently alters past numbers fails before it reaches a manager. The only legitimate
    divergence of final_qty is the warehouse-availability cap, which is applied after calculate() and is
    recorded in the explanation notes.
    """
    if rec.calc_version != CALC_VERSION:
        return {"ok": False, "skipped": True, "mismatches": [f"calc version {rec.calc_version} is not {CALC_VERSION}"]}
    run = db.get(ReplRun, rec.run_id)
    if run is None:
        return {"ok": False, "skipped": True, "mismatches": ["run row is missing"]}
    e = rec.explanation or {}
    i = e.get("inputs") or {}
    params = run.params or {}
    try:
        res = calculate(ads=rec.ads, trading_days=int(i["trading_days"]), lookback=int(i["lookback_days"]),
                        on_hand=float(i["on_hand"]), incoming=float(i["incoming"]), lead=float(i["lead_time_days"]),
                        review=float(i["review_days"]), safety_days=float(i["safety_days"]),
                        case_pack=i.get("case_pack"), order_multiple=i.get("order_multiple"), moq=i.get("moq"),
                        max_stock=i.get("max_stock"), max_cover_days=float(params["repl.max_cover_days"]),
                        min_trading_days=int(params["repl.min_trading_days"]), use_stock=params.get("repl.mode", "stock_and_sales") != "sales_only",
                        cover_days=float(params.get("repl.sales_only_cover_days", 7)))
    except (KeyError, TypeError, ValueError) as ex:
        return {"ok": False, "skipped": False, "mismatches": [f"stored inputs incomplete: {ex}"]}
    mismatches: list[str] = []
    for field, stored in (("safety_stock", rec.safety_stock), ("target", rec.target_stock), ("position", rec.position), ("raw", rec.raw_qty)):
        if abs(res[field] - stored) > TOL:
            mismatches.append(f"{field}: replayed {res[field]:g} != stored {stored:g}")
    capped = any("Warehouse has only" in n for n in e.get("notes", []))
    if capped:
        if not (0 <= rec.final_qty <= res["final"] + TOL):
            mismatches.append(f"final: warehouse-capped {rec.final_qty:g} is outside 0..{res['final']:g}")
    elif abs(res["final"] - rec.final_qty) > TOL:
        mismatches.append(f"final: replayed {res['final']:g} != stored {rec.final_qty:g}")
    old_steps = [(s.get("step"), s.get("from", s.get("qty")), s.get("to", s.get("qty"))) for s in e.get("steps", [])]
    new_steps = [(s.get("step"), s.get("from", s.get("qty")), s.get("to", s.get("qty"))) for s in res["steps"]]
    if len(old_steps) != len(new_steps) or any(
            o[0] != n[0] or (o[1] is not None and abs(o[1] - n[1]) > TOL) or (o[2] is not None and abs(o[2] - n[2]) > TOL)
            for o, n in zip(old_steps, new_steps)):
        mismatches.append(f"steps differ: stored {old_steps} != replayed {new_steps}")
    return {"ok": not mismatches, "skipped": False, "mismatches": mismatches, "warehouse_capped": capped}


def replay_all(db: Session) -> dict:
    """Replay every stored suggestion of the current calculation version. Returns match counts and failures."""
    total = ok = skipped = 0
    failures: list[dict] = []
    for rec in db.scalars(select(Recommendation).where(Recommendation.calc_version == CALC_VERSION)):
        r = replay(db, rec)
        total += 1
        if r["skipped"]:
            skipped += 1
        elif r["ok"]:
            ok += 1
        else:
            failures.append({"rec_id": rec.id, "mismatches": r["mismatches"]})
    return {"total": total, "ok": ok, "skipped": skipped, "failed": len(failures), "failures": failures[:20]}


# ======================================================================================= engine run
def run(db: Session, *, force: bool = False, actor: User | None = None) -> ReplRun:
    """Generate recommendations for today. Idempotent per (location, product, day, calculation version)."""
    today = today_local()
    s = {k: cfg.get(db, k) for k in ("repl.lookback_days", "repl.min_trading_days", "repl.default_supplier_lead_days",
                                      "repl.default_review_days", "repl.default_safety_days", "repl.store_lead_days",
                                      "repl.max_cover_days", "repl.require_case_pack", "repl.default_pack", "repl.min_weekly_units", "po.stale_days")}
    mode = "stock_and_sales" if cfg.get(db, "repl.mode") == "stock_and_sales" else "sales_only"
    sales_only = mode == "sales_only"
    cover_days = float(cfg.get(db, "repl.sales_only_cover_days"))
    s["repl.mode"], s["repl.sales_only_cover_days"] = mode, cover_days
    run_row = ReplRun(business_date=today, calc_version=CALC_VERSION, params=s)
    db.add(run_row)
    db.flush()
    blockers = freshness.blocking_domains(db)
    if blockers and not force:
        run_row.status = "blocked"
        run_row.note = "Blocked: stale data: " + ", ".join(f"{b['label']} ({b['status']})" for b in blockers)
        run_row.counts = {"created": 0, "updated": 0}
        audit.log(db, actor_type="human" if actor else "system", actor_id=actor.id if actor else "scheduler", action="repl.run_blocked",
                  entity_type="repl_run", entity_id=run_row.id, reason=run_row.note)
        return run_row

    # yesterday's undecided suggestions are no longer valid
    for r in db.scalars(select(Recommendation).where(Recommendation.business_date < today, Recommendation.status.in_(("pending", "needs_review")))):
        r.status = "expired"

    lookback = int(s["repl.lookback_days"])
    start, end = today - timedelta(days=lookback), today - timedelta(days=1)
    locs = list(db.scalars(select(Location).where(Location.monitored.is_(True))))
    stores = [l for l in locs if l.type == "store"]
    warehouses = [l for l in locs if l.type == "warehouse"]

    trading: dict[int, int] = {l.id: (db.scalar(select(func.count()).select_from(SalesDay).where(
        SalesDay.location_id == l.id, SalesDay.business_date >= start, SalesDay.business_date <= end, SalesDay.tx_count > 0)) or 0) for l in locs}
    units: dict[tuple[int, int], float] = defaultdict(float)
    for lid, pid, u in db.execute(select(SalesDayProduct.location_id, SalesDayProduct.product_id, func.sum(SalesDayProduct.units))
                                  .where(SalesDayProduct.business_date >= start, SalesDayProduct.business_date <= end)
                                  .group_by(SalesDayProduct.location_id, SalesDayProduct.product_id)):
        units[(lid, pid)] = u or 0.0
    ads_store: dict[tuple[int, int], float] = {}
    for (lid, pid), u in units.items():
        td = trading.get(lid, 0)
        if td > 0 and u > 0:
            ads_store[(lid, pid)] = u / td

    inv = {(r.location_id, r.product_id): r for r in db.scalars(select(InventoryCurrent))}
    incoming, stale_pos = purchasing.incoming_map(db)
    stale_po_products: set[tuple[int, int]] = set()
    for po in stale_pos:
        for ln in po.lines:
            if ln.product_id and po.location_id:
                stale_po_products.add((po.location_id, ln.product_id))
    crit_loc = {e.location_id for e in db.scalars(select(DqEvent).where(
        DqEvent.severity == "critical", DqEvent.resolved_at.is_(None), DqEvent.location_id.is_not(None)))}
    products = {p.id: p for p in db.scalars(select(Product).options(selectinload(Product.supplier))) if p.in_api and p.track_stock and p.replenish_enabled}
    last_cost = _last_po_price(db)

    existing = {(r.location_id, r.product_id): r for r in db.scalars(select(Recommendation).where(
        Recommendation.business_date == today, Recommendation.calc_version == CALC_VERSION))}
    created = updated = 0
    default_lead = float(s["repl.default_supplier_lead_days"])
    default_review = float(s["repl.default_review_days"])
    default_safety = float(s["repl.default_safety_days"])
    default_pack = float(s["repl.default_pack"] or 1)
    min_weekly = float(s["repl.min_weekly_units"] or 0)

    # sales-only mode has no stock to tell it that something was already ordered, so what was approved in the last cover period is not suggested again
    recently_ordered: set[tuple[int, int]] = set()
    if sales_only:
        cut = today - timedelta(days=int(cover_days))
        recently_ordered = {(r.location_id, r.product_id) for r in db.scalars(select(Recommendation).where(
            Recommendation.business_date >= cut, Recommendation.business_date < today, Recommendation.status.in_(("approved", "modified", "ordered")),
            Recommendation.approved_qty > 0))}
    jobs: list[tuple[Location, str]] = [(l, "transfer") for l in stores] + [(l, "supplier") for l in warehouses]
    # warehouse stock available for transfers
    wh_on_hand = {pid: sum(max(inv[(w.id, pid)].on_hand, 0) for w in warehouses if (w.id, pid) in inv) for pid in products}

    for loc, kind in jobs:
        for pid, prod in products.items():
            if kind == "transfer":
                ads = ads_store.get((loc.id, pid), 0.0)
                td = trading.get(loc.id, 0)
            else:
                ads = sum(ads_store.get((st.id, pid), 0.0) for st in stores)
                td = max([trading.get(st.id, 0) for st in stores] or [0])
            if ads <= 0 or ads * 7 < min_weekly:  # slow sellers (a few units a week) are not worth an order line
                continue
            row = inv.get((loc.id, pid))
            if row is None:
                continue
            if sales_only and (loc.id, pid) in recently_ordered:
                continue
            sup = prod.supplier
            reasons: list[str] = []
            penalties: list[tuple[str, float]] = []
            if kind == "transfer":
                lead = float(loc.lead_time_days if loc.lead_time_days is not None else s["repl.store_lead_days"])
                review = default_review
            else:
                if sup is None:
                    reasons.append("no_supplier")
                    lead, review = default_lead, default_review
                else:
                    lead = sup.lead_time_days if sup.lead_time_days is not None else default_lead
                    review = sup.review_period_days if sup.review_period_days is not None else default_review
                    if sup.lead_time_days is None:
                        penalties.append(("lead time is assumed, not set for this supplier", 0.10))
                    if prod.supplier_source == "po_history":
                        penalties.append(("supplier inferred from PO history", 0.05))
            inc = incoming.get((loc.id, pid), 0.0)
            pack = prod.case_pack if prod.case_pack and prod.case_pack > 0 else default_pack  # never suggest loose single units
            res = calculate(ads=ads, trading_days=td, lookback=lookback, on_hand=row.on_hand, incoming=inc, lead=lead, review=review,
                            safety_days=prod.safety_days if prod.safety_days is not None else default_safety, case_pack=pack,
                            order_multiple=prod.order_multiple, moq=prod.moq, max_stock=prod.max_stock,
                            max_cover_days=float(s["repl.max_cover_days"]), min_trading_days=int(s["repl.min_trading_days"]),
                            use_stock=not sales_only, cover_days=cover_days)
            final = res["final"]
            notes: list[str] = []
            if kind == "transfer" and not sales_only:  # the warehouse count is not trusted in sales-only mode
                avail = wh_on_hand.get(pid, 0.0)
                if final > avail:
                    notes.append(f"Warehouse has only {avail:g} on hand; transfer limited to what is available. The warehouse order covers the rest.")
                    final = max(0.0, _floor_to(avail, pack))
            if row.on_hand < 0 and not sales_only:
                penalties.append(("on-hand is negative; treated as 0 until counted", 0.30))
                reasons.append("negative_stock")
            if res["history_short"]:
                reasons.append("short_history")
            elif res["history_partial"]:
                penalties.append(("less than 60% of the lookback has sales data", 0.15))
            if prod.case_pack is None:
                penalties.append((f"case pack unknown, assumed packs of {pack:g}", 0.05))
                if s["repl.require_case_pack"]:
                    reasons.append("no_case_pack")
            if (loc.id, pid) in stale_po_products:
                reasons.append("conflicting_po")
                penalties.append(("a stale open PO exists for this item and was ignored", 0.10))
            if loc.id in crit_loc:
                reasons.append("data_quality")
            conf = max(0.1, 1.0 - sum(p for _, p in penalties))
            if final <= 0 and not reasons:
                continue
            if final <= 0 and reasons and res["raw"] <= 0:
                continue
            unit_cost = prod.cost if prod.cost and prod.cost > 0 else last_cost.get(pid)
            status = "needs_review" if reasons else "pending"
            expl = {
                "mode": mode,
                "formula": ("order = max(0, ADS x cover days - incoming); stock on hand is not used" if sales_only else
                            "target = ADS x (lead + review) + ADS x safety days; position = on hand + incoming; order = max(0, target - position)"),
                "inputs": {"units_sold": round(units.get((loc.id, pid), 0) if kind == "transfer" else sum(units.get((st.id, pid), 0) for st in stores), 3),
                           "trading_days": td, "lookback_days": lookback, "lead_time_days": lead, "review_days": review,
                           "safety_days": prod.safety_days if prod.safety_days is not None else default_safety,
                           "on_hand": row.on_hand, "incoming": inc, "case_pack": pack, "order_multiple": prod.order_multiple,
                           "moq": prod.moq, "max_stock": prod.max_stock, "stock_synced_at": row.synced_at.isoformat(),
                           **({"cover_days": cover_days} if sales_only else {})},
                "steps": res["steps"], "notes": notes, "confidence_penalties": [{"reason": a, "penalty": b} for a, b in penalties],
                "source": "transfer from warehouse" if kind == "transfer" else "supplier order for the warehouse",
                "supplier_source": prod.supplier_source,
            }
            vals = dict(supplier_id=prod.supplier_id if kind == "supplier" else None, source_kind=kind, ads=round(ads, 4), trading_days=td,
                        lead_time=lead, review_period=review, safety_days=prod.safety_days if prod.safety_days is not None else default_safety,
                        safety_stock=round(res["safety_stock"], 3), target_stock=round(res["target"], 3), on_hand=row.on_hand, incoming=inc,
                        position=round(res["position"], 3), raw_qty=round(res["raw"], 3), final_qty=final, cases=res["cases"],
                        unit_cost=unit_cost, value=round(final * (unit_cost or 0), 2),
                        days_cover=None if res["days_cover"] is None else round(res["days_cover"], 2), confidence=round(conf, 2),
                        needs_review_reasons=reasons or None, explanation=expl)
            rec = existing.get((loc.id, pid))
            if rec is None:
                db.add(Recommendation(run_id=run_row.id, location_id=loc.id, product_id=pid, business_date=today, calc_version=CALC_VERSION,
                                      status=status, **vals))
                created += 1
            elif rec.status in ("pending", "needs_review"):
                for k, v in vals.items():
                    setattr(rec, k, v)
                rec.status = status
                rec.run_id = run_row.id
                updated += 1
    # an undecided suggestion from an earlier run today that this run no longer produces (nothing to order now) must not linger
    for rec in existing.values():
        if rec.status in ("pending", "needs_review") and rec.run_id != run_row.id:
            rec.status = "expired"
    run_row.counts = {"created": created, "updated": updated, "stores": len(stores), "warehouses": len(warehouses)}
    audit.log(db, actor_type="human" if actor else "system", actor_id=actor.id if actor else "scheduler", action="repl.run",
              entity_type="repl_run", entity_id=run_row.id, after=run_row.counts)
    return run_row


def _last_po_price(db: Session) -> dict[int, float]:
    out: dict[int, float] = {}
    q = (select(PoLine.product_id, PoLine.supplier_price).join(PurchaseOrder, PurchaseOrder.id == PoLine.po_id)
         .where(PoLine.product_id.is_not(None), PoLine.supplier_price.is_not(None), PurchaseOrder.missing_from_api.is_(False))
         .order_by(PurchaseOrder.created_sh.desc()))
    for pid, price in db.execute(q):
        out.setdefault(pid, price)
    return out


# ======================================================================================= manager decisions
class DecisionError(Exception):
    pass


def decide(db: Session, rec: Recommendation, user: User, action: str, qty: float | None = None,
           reason: str | None = None, note: str | None = None) -> Recommendation:
    if rec.status not in ("pending", "needs_review"):
        raise DecisionError(f"This suggestion is already {rec.status}")
    reason = (reason or "").strip()[:200] or None  # free text, in the manager's own words
    if action in ("modify", "reject") and not reason:
        raise DecisionError("Write a reason for changing or rejecting this suggestion")
    if rec.status == "needs_review" and not (note or "").strip():
        raise DecisionError("This suggestion needs review: write a note saying what you checked")
    before = {"status": rec.status, "final_qty": rec.final_qty}
    if action == "approve":
        if qty is not None and qty >= 0 and abs(qty - rec.final_qty) > 1e-9:  # approving a typed quantity: no separate Modify step, no reason needed
            rec.approved_qty = qty
            rec.status = "modified"
        else:
            rec.approved_qty = rec.final_qty
            rec.status = "approved"
    elif action == "modify":
        if qty is None or qty < 0:
            raise DecisionError("Enter the quantity to order")
        rec.approved_qty = qty
        rec.status = "modified"
    elif action == "reject":
        rec.approved_qty = 0.0
        rec.status = "rejected"
    else:
        raise DecisionError("Unknown action")
    value = (rec.approved_qty or 0) * (rec.unit_cost or 0)
    limit = float(cfg.get(db, "repl.approval_limit_manager"))
    if action != "reject" and value > limit and user.role in ("staff", "store_manager"):
        raise DecisionError(f"Order value {value:,.2f} is above your approval limit of {limit:,.2f}; management must approve")
    rec.decided_by, rec.decided_at, rec.reason_code, rec.decision_note = user.id, now_utc(), reason, note
    audit.log(db, actor_type="human", actor_id=user.id, action=f"repl.{action}", entity_type="recommendation", entity_id=rec.id,
              before=before, after={"status": rec.status, "approved_qty": rec.approved_qty, "recommended": rec.final_qty},
              reason=reason, context={"note": note, "confidence": rec.confidence})
    return rec


def create_batches(db: Session, user: User, location_ids: list[int] | None = None) -> list[OrderBatch]:
    """Group approved lines per (location, supplier, source) into batches ready to be keyed into StoreHub."""
    q = select(Recommendation).where(Recommendation.status.in_(("approved", "modified")), Recommendation.batch_id.is_(None),
                                     Recommendation.approved_qty > 0)
    if location_ids is not None:
        q = q.where(Recommendation.location_id.in_(location_ids))
    groups: dict[tuple, list[Recommendation]] = defaultdict(list)
    for r in db.scalars(q):
        groups[(r.location_id, r.supplier_id, r.source_kind)].append(r)
    batches = []
    for (loc, sup, kind), recs in groups.items():
        b = OrderBatch(location_id=loc, supplier_id=sup, source_kind=kind, created_by=user.id,
                       total_value=round(sum((r.approved_qty or 0) * (r.unit_cost or 0) for r in recs), 2))
        db.add(b)
        db.flush()
        for r in recs:
            r.batch_id = b.id
        audit.log(db, actor_type="human", actor_id=user.id, action="batch.create", entity_type="order_batch", entity_id=b.id,
                  after={"lines": len(recs), "value": b.total_value, "supplier_id": sup, "location_id": loc})
        batches.append(b)
    return batches


def mark_entered(db: Session, batch: OrderBatch, user: User, po_number: int | None) -> OrderBatch:
    """Record that the batch was keyed into StoreHub (the API cannot create POs) and keep the PO number."""
    if batch.status != "approved":
        raise DecisionError(f"Batch is already {batch.status}")
    if batch.source_kind == "supplier" and not po_number:
        raise DecisionError("Enter the StoreHub PO number")
    batch.status, batch.entered_by, batch.entered_at, batch.storehub_po_number = "entered", user.id, now_utc(), po_number
    for r in db.scalars(select(Recommendation).where(Recommendation.batch_id == batch.id)):
        r.status = "ordered"
    audit.log(db, actor_type="human", actor_id=user.id, action="batch.entered", entity_type="order_batch", entity_id=batch.id,
              after={"po_number": po_number})
    return batch


def sync_batches_with_pos(db: Session) -> int:
    """When StoreHub shows the PO as completed, mark the batch received."""
    n = 0
    for b in db.scalars(select(OrderBatch).where(OrderBatch.status == "entered", OrderBatch.source_kind == "supplier", OrderBatch.storehub_po_number.is_not(None))):
        po = db.scalar(select(PurchaseOrder).where(PurchaseOrder.sh_po_id == b.storehub_po_number))
        if po and po.status == "completed":
            b.status = "received"
            n += 1
    return n


# ======================================================================================= outcomes and metrics
def track_outcomes(db: Session) -> int:
    """7 days after an approved order, record whether the item stocked out (from our daily snapshots)."""
    n = 0
    cutoff = now_utc() - timedelta(days=7)
    for r in db.scalars(select(Recommendation).where(Recommendation.status.in_(("approved", "modified", "ordered")),
                                                     Recommendation.decided_at <= cutoff, Recommendation.outcome.is_(None))):
        d0 = r.decided_at.date()
        snaps = list(db.scalars(select(InventorySnapshot).where(
            InventorySnapshot.location_id == r.location_id, InventorySnapshot.product_id == r.product_id,
            InventorySnapshot.snap_date > d0, InventorySnapshot.snap_date <= d0 + timedelta(days=7))))
        if not snaps:
            r.outcome = {"status": "insufficient_history"}
        else:
            r.outcome = {"status": "measured", "days_observed": len(snaps), "stockout_days": sum(1 for s in snaps if s.on_hand <= 0)}
        n += 1
    return n


def metrics(db: Session, days: int = 60, until: datetime | None = None) -> dict:
    end = until or now_utc()
    since = end - timedelta(days=days)
    recs = list(db.scalars(select(Recommendation).where(Recommendation.decided_at >= since, Recommendation.decided_at <= end)))
    decided = [r for r in recs if r.status in DECIDED]
    appr = [r for r in decided if r.status in ("approved", "ordered") and r.approved_qty == r.final_qty]
    mod = [r for r in decided if r.status == "modified"]
    rej = [r for r in decided if r.status == "rejected"]
    sizes = [abs((r.approved_qty or 0) - r.final_qty) for r in mod]
    measured = [r for r in decided if r.outcome and r.outcome.get("status") == "measured"]

    def _stockout(rows):
        m = [r for r in rows if r.outcome and r.outcome.get("status") == "measured"]
        return len(m), (sum(1 for r in m if r.outcome["stockout_days"] > 0) / len(m)) if m else None

    # corrections are candidate context until outcomes prove them (benchmark principle 6): compare measured
    # stockout rates of overridden vs accepted suggestions; refuse to conclude on thin samples
    mod_n, mod_rate = _stockout(mod)
    acc_n, acc_rate = _stockout(appr)
    verdict = None
    if mod_n >= 10 and acc_n >= 10:
        verdict = "overrides helped" if (mod_rate or 0) < (acc_rate or 0) else "overrides did not help"
    return {"decided": len(decided), "accepted": len(appr), "modified": len(mod), "rejected": len(rej),
            "acceptance_rate": (len(appr) / len(decided)) if decided else None,
            "override_rate": ((len(mod) + len(rej)) / len(decided)) if decided else None,
            "avg_override_size": (sum(sizes) / len(sizes)) if sizes else None,
            "stockout_rate": (sum(1 for r in measured if r.outcome["stockout_days"] > 0) / len(measured)) if measured else None,
            "measured": len(measured),
            "override_outcomes": {"modified": mod_n, "modified_stockout_rate": mod_rate,
                                  "accepted": acc_n, "accepted_stockout_rate": acc_rate, "verdict": verdict}}
