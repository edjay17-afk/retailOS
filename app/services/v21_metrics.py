"""V2.1 repair queue (MUST 4) and operating metrics (spec section 9). Everything here is calculated by code from stored facts; nothing is estimated by AI.

Value at risk of a blocked suggestion = the quantity it would order (the larger of the raw and final quantity) x unit cost.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..models import (CycleCount, CycleCountLine, InventoryChange, InventoryCurrent, InventoryEvent, Location, OrderBatch, PurchaseOrder, Receiving,
                      Recommendation, SalesDayProduct, StockTransfer, StockTransferLine)
from ..timeutil import now_utc, today_local
from . import dq, replenishment

WHY = {
    "negative_stock": "StoreHub shows less than zero on hand, so the stock figure the order is based on is wrong.",
    "no_supplier": "Nobody is set to supply this product, so there is nobody to order from.",
    "no_case_pack": "The case pack is unknown, so the order size cannot be rounded to what the supplier ships.",
    "conflicting_po": "An old open PO exists for this item, so what is already on the way is uncertain.",
    "short_history": "There are too few trading days of sales to trust the sales rate.",
    "data_quality": "A critical data-quality finding is open for this location.",
}
HIGH_VALUE, MID_VALUE = 5000.0, 1000.0


def value_at_risk(r: Recommendation) -> float:
    return round(max(r.final_qty or 0.0, r.raw_qty or 0.0) * (r.unit_cost or 0.0), 2)


def severity(value: float) -> str:
    return "high" if value >= HIGH_VALUE else "medium" if value >= MID_VALUE else "low"


def repair_queue(db: Session, location_ids: list[int] | None = None, day=None) -> list[dict]:
    """Every blocked (needs_review) suggestion of the day with its exact causes, why it cannot be trusted, value at risk and where to fix it."""
    day = day or today_local()
    q = select(Recommendation).where(Recommendation.business_date == day, Recommendation.status == "needs_review") \
        .options(selectinload(Recommendation.product), selectinload(Recommendation.location))
    if location_ids is not None:
        q = q.where(Recommendation.location_id.in_(location_ids or [0]))
    rows = []
    for r in db.scalars(q):
        causes = []
        for code in r.needs_review_reasons or []:
            fix = dq.FIXES.get(code)
            causes.append({"code": code, "label": replenishment.REASON_LABELS.get(code, code.replace("_", " ")),
                           "why": WHY.get(code, "The data behind this suggestion cannot be trusted."),
                           "fix_url": fix[0] if fix else None, "fix_label": fix[1] if fix else None})
        v = value_at_risk(r)
        rows.append({"rec": r, "causes": causes, "value": v, "severity": severity(v), "count_task": "negative_stock" in (r.needs_review_reasons or [])})
    rows.sort(key=lambda x: (-x["value"], x["rec"].product.name if x["rec"].product else ""))
    return rows


def repair_kpis(db: Session, days: int = 28, location_ids: list[int] | None = None) -> dict:
    """Open blocked value and its daily trend, value unblocked per week, mean time to repair and repeat blocker rate, from the stored daily suggestions."""
    today = today_local()
    since = today - timedelta(days=days)
    q = select(Recommendation.location_id, Recommendation.product_id, Recommendation.business_date, Recommendation.needs_review_reasons, Recommendation.final_qty,
               Recommendation.raw_qty, Recommendation.unit_cost).where(Recommendation.business_date >= since)
    if location_ids is not None:
        q = q.where(Recommendation.location_id.in_(location_ids or [0]))
    history: dict[tuple[int, int], dict] = defaultdict(dict)
    trend: dict = defaultdict(float)
    for lid, pid, d, reasons, fq, rq, uc in db.execute(q):
        val = round(max(fq or 0.0, rq or 0.0) * (uc or 0.0), 2)
        blocked = bool(reasons)
        history[(lid, pid)][d] = (blocked, val, round((fq or 0.0) * (uc or 0.0), 2))
        if blocked:
            trend[d] += val
    unblocked_week = 0.0
    repairs: list[int] = []
    ever_blocked = repeat = 0
    for days_map in history.values():
        state, since_blocked, was_repaired = False, None, False
        for d in sorted(days_map):
            blocked, _val, order_value = days_map[d]
            if blocked and not state:
                state, since_blocked = True, d
                if was_repaired:
                    repeat += 1
                else:
                    ever_blocked += 1
            elif not blocked and state:
                state, was_repaired = False, True
                repairs.append((d - since_blocked).days)
                if (today - d).days < 7:
                    unblocked_week += order_value
    series = [(today - timedelta(days=i), round(trend.get(today - timedelta(days=i), 0.0), 2)) for i in range(13, -1, -1)]
    return {"open_value": series[-1][1], "trend": series, "unblocked_week_value": round(unblocked_week, 2),
            "mean_days_to_repair": (sum(repairs) / len(repairs)) if repairs else None, "repairs": len(repairs),
            "repeat_blocker_rate": (repeat / ever_blocked) if ever_blocked else None}


# ======================================================================================= section 9 metrics
def _explained_deltas(db: Session, since, location_ids) -> tuple[int, int, int]:
    """(all deltas, explained by sales, explained by a recorded event or a StoreHub transfer, in that order of precedence) for deltas observed since `since`."""
    q = select(InventoryChange).where(InventoryChange.observed_at >= since, InventoryChange.old_qty.is_not(None))
    if location_ids is not None:
        q = q.where(InventoryChange.location_id.in_(location_ids or [0]))
    changes = list(db.scalars(q))
    if not changes:
        return 0, 0, 0
    sales: dict[tuple[int, int], float] = defaultdict(float)
    start = (since - timedelta(days=1)).date()
    for lid, pid, d, u in db.execute(select(SalesDayProduct.location_id, SalesDayProduct.product_id, SalesDayProduct.business_date, SalesDayProduct.units)
                                     .where(SalesDayProduct.business_date >= start)):
        sales[(lid, pid, d)] = u or 0.0
    events: dict[tuple[int, int], list] = defaultdict(list)
    for e in db.scalars(select(InventoryEvent).where(InventoryEvent.recorded_at >= since - timedelta(days=4), InventoryEvent.product_id.is_not(None))):
        events[(e.location_id, e.product_id)].append(e.recorded_at)
    transfers: dict[tuple[int, int], list] = defaultdict(list)
    for tl, t in db.execute(select(StockTransferLine, StockTransfer).join(StockTransfer, StockTransfer.id == StockTransferLine.transfer_id)
                            .where(StockTransfer.created_sh >= since - timedelta(days=10), StockTransferLine.product_id.is_not(None))):
        for lid in (t.source_location_id, t.target_location_id):
            if lid:
                transfers[(lid, tl.product_id)].append(t.received_at or t.shipped_at or t.created_sh)
    by_sales = by_event = 0
    window = timedelta(days=3)
    for c in changes:
        d = c.observed_at.date()
        if c.delta < 0 and (sales.get((c.location_id, c.product_id, d), 0.0) + sales.get((c.location_id, c.product_id, d - timedelta(days=1)), 0.0)) >= abs(c.delta) - 1e-9:
            by_sales += 1
        elif any(t and abs(c.observed_at - t) <= window for t in events.get((c.location_id, c.product_id), []) + transfers.get((c.location_id, c.product_id), [])):
            by_event += 1
    return len(changes), by_sales, by_event


def operating_metrics(db: Session, days: int = 30, location_ids: list[int] | None = None) -> dict:
    """The spec section 9 rates for the last `days` days. A rate is None when nothing is there to divide (shown as "no data yet", never as 0%)."""
    since = now_utc() - timedelta(days=days)

    def rate(a, b):
        return (a / b) if b else None

    out: dict = {"days": days}
    # 9.2 / 9.3: how much of what changed stock has a known cause
    total, by_sales, by_event = _explained_deltas(db, since, location_ids)
    non_sales = total - by_sales
    out.update({"deltas": total, "unexplained": total - by_sales - by_event,
                "unexplained_delta_rate": rate(total - by_sales - by_event, total),
                "traceability_rate": rate(by_event, non_sales)})
    # 9.4: completed deliveries that have a physical receiving record
    pq = select(PurchaseOrder).where(PurchaseOrder.status == "completed", PurchaseOrder.missing_from_api.is_(False), PurchaseOrder.modified_sh >= since)
    if location_ids is not None:
        pq = pq.where(PurchaseOrder.location_id.in_(location_ids or [0]))
    pos = list(db.scalars(pq))
    have = set(db.scalars(select(Receiving.po_id).where(Receiving.status.in_(("completed", "mismatch")), Receiving.po_id.in_([p.id for p in pos] or [0]))))
    out.update({"completed_deliveries": len(pos), "receiving_confirmation_rate": rate(sum(1 for p in pos if p.id in have), len(pos))})
    # 9.5: dispatched transfers that the store confirmed
    bq = select(OrderBatch).where(OrderBatch.source_kind == "transfer", OrderBatch.dispatched_at >= since)
    if location_ids is not None:
        bq = bq.where(OrderBatch.location_id.in_(location_ids or [0]))
    batches = list(db.scalars(bq))
    done = [b for b in batches if b.received_at]
    var_batches = set(db.scalars(select(InventoryEvent.related_transfer_id).where(InventoryEvent.event_type == "transfer_variance",
                                                                                   InventoryEvent.related_transfer_id.in_([b.id for b in done] or [0]))))
    hours = [(b.received_at - b.dispatched_at).total_seconds() / 3600 for b in done]
    out.update({"dispatched_transfers": len(batches), "transfer_confirmation_rate": rate(len(done), len(batches)),
                "in_transit_variance_rate": rate(len([b for b in done if b.id in var_batches]), len(done)),
                "avg_dispatch_to_confirmation_hours": (sum(hours) / len(hours)) if hours else None})
    # receiving + event reconciliation
    out.update({k: v for k, v in _event_summary(db, since, location_ids).items()})
    # cycle counts
    cq = select(CycleCountLine.variance).join(CycleCount, CycleCount.id == CycleCountLine.count_id).where(
        CycleCount.status == "approved", CycleCount.approved_at >= since, CycleCountLine.counted_qty.is_not(None))
    if location_ids is not None:
        cq = cq.where(CycleCount.location_id.in_(location_ids or [0]))
    variances = [v or 0.0 for v in db.scalars(cq)]
    out.update({"counted_lines": len(variances), "cycle_count_accuracy": rate(sum(1 for v in variances if abs(v) < 1e-9), len(variances))})
    pairs = defaultdict(int)
    for lid, pid in db.execute(select(InventoryEvent.location_id, InventoryEvent.product_id).where(
            InventoryEvent.event_type == "cycle_count", InventoryEvent.quantity > 0, InventoryEvent.recorded_at >= since)):
        pairs[(lid, pid)] += 1
    out["repeat_variance_rate"] = rate(sum(1 for n in pairs.values() if n >= 2), len(pairs))
    nq = select(func.count()).select_from(InventoryCurrent).where(InventoryCurrent.on_hand < 0)
    if location_ids is not None:
        nq = nq.where(InventoryCurrent.location_id.in_(location_ids or [0]))
    out["negative_rows"] = db.scalar(nq) or 0  # an outcome indicator only, never the main target
    return out


def _event_summary(db: Session, since, location_ids) -> dict:
    q = select(InventoryEvent.event_type, InventoryEvent.reconciliation_status, func.count()).where(InventoryEvent.recorded_at >= since)
    if location_ids is not None:
        q = q.where(InventoryEvent.location_id.in_(location_ids or [0]))
    c: dict[tuple[str, str], int] = {}
    for t, s, n in db.execute(q.group_by(InventoryEvent.event_type, InventoryEvent.reconciliation_status)):
        c[(t, s)] = n

    def tot(types, statuses=None):
        return sum(n for (t, s), n in c.items() if t in types and (statuses is None or s in statuses))
    rcv = tot(("supplier_receiving",))
    return {"events": tot(tuple(k[0] for k in c) or ("",)), "unresolved_events": sum(n for (_, s), n in c.items() if s in ("pending", "mismatch", "needs_review")),
            "mismatch_events": sum(n for (_, s), n in c.items() if s == "mismatch"),
            "receiving_variance_rate": (tot(("receiving_variance",)) / rcv) if rcv else None,
            "receiving_reconciled_rate": (tot(("supplier_receiving",), ("matched",)) / rcv) if rcv else None}
