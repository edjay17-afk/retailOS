"""Operating metrics from the spec (sections 15 and 24): is the business actually improving, not just using the software?"""
from __future__ import annotations

import statistics
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (CycleCount, CycleCountLine, DqEvent, Inspection, InventoryCurrent, Issue, Product, Receiving, SalesDayProduct, SyncRun)
from ..timeutil import now_utc, today_local
from . import freshness, replenishment


def _hours(a, b):
    return (b - a).total_seconds() / 3600


def compute(db: Session, location_ids: list[int], days: int = 60, until=None) -> dict:
    now = until or now_utc()  # custom ranges end at `until`; window-based metrics read [since, now]
    since = now - timedelta(days=days)
    out: dict = {"days": days}

    # ---- replenishment
    out["replenishment"] = replenishment.metrics(db, days, until=until)

    # ---- inventory accuracy and dead stock
    lines = list(db.scalars(select(CycleCountLine).join(CycleCount, CycleCount.id == CycleCountLine.count_id).where(
        CycleCount.location_id.in_(location_ids or [0]), CycleCount.status == "approved", CycleCount.created_at >= since, CycleCount.created_at <= now,
        CycleCountLine.variance.is_not(None))))
    out["inventory"] = {"counted_lines": len(lines), "accuracy": (sum(1 for l in lines if abs(l.variance) < 1e-9) / len(lines)) if lines else None,
                        "negative_items": db.scalar(select(func.count()).select_from(InventoryCurrent).where(InventoryCurrent.on_hand < 0, InventoryCurrent.location_id.in_(location_ids or [0]))) or 0}
    sold_ids = set(db.scalars(select(SalesDayProduct.product_id).where(SalesDayProduct.business_date >= today_local() - timedelta(days=60), SalesDayProduct.units > 0,
                                                                         SalesDayProduct.location_id.in_(location_ids or [0])).distinct()))
    dead_n, dead_val = 0, 0.0
    for inv, p in db.execute(select(InventoryCurrent, Product).join(Product, Product.id == InventoryCurrent.product_id).where(
            InventoryCurrent.location_id.in_(location_ids or [0]), InventoryCurrent.on_hand > 0, Product.in_api.is_(True))):
        if p.id not in sold_ids:
            dead_n += 1
            dead_val += inv.on_hand * (p.cost or 0)
    out["inventory"].update({"dead_stock_items": dead_n, "dead_stock_value": dead_val})

    # ---- issues
    issues_ = list(db.scalars(select(Issue).where(Issue.created_at >= since, Issue.created_at <= now,
                                                  (Issue.location_id.in_(location_ids or [0])) | Issue.location_id.is_(None))))
    opn = [i for i in issues_ if i.status != "closed"]
    ack = [_hours(i.created_at, i.acknowledged_at) for i in issues_ if i.acknowledged_at]
    res = [_hours(i.created_at, i.resolved_at) for i in issues_ if i.resolved_at]
    seen: dict[tuple, int] = {}
    for i in issues_:
        k = (i.type, i.location_id, i.title.split(":")[0][:60])
        seen[k] = seen.get(k, 0) + 1
    repeats = sum(n - 1 for n in seen.values() if n > 1)
    out["issues"] = {"created": len(issues_), "open": len(opn), "overdue": sum(1 for i in opn if i.due_at < now),
                     "median_ack_h": statistics.median(ack) if ack else None, "median_resolve_h": statistics.median(res) if res else None,
                     "repeat_rate": (repeats / len(issues_)) if issues_ else None}

    # ---- inspections, receiving
    insp = list(db.scalars(select(Inspection).where(Inspection.business_date >= today_local() - timedelta(days=30), Inspection.location_id.in_(location_ids or [0]), Inspection.due_at < now)))
    done = sum(1 for i in insp if i.status in ("completed", "late", "submitted"))
    out["inspections"] = {"due": len(insp), "completed": done, "completion_rate": (done / len(insp)) if insp else None,
                          "on_time": sum(1 for i in insp if i.status == "completed" and i.completed_at and i.completed_at <= i.due_at)}
    rec = list(db.scalars(select(Receiving).where(Receiving.status != "open", Receiving.created_at >= since, Receiving.created_at <= now)))
    out["receiving"] = {"completed": len(rec), "with_differences": sum(1 for r in rec if r.status == "mismatch"),
                        "discrepancy_rate": (sum(1 for r in rec if r.status == "mismatch") / len(rec)) if rec else None}

    # ---- data health
    week = now - timedelta(days=7)
    runs = list(db.scalars(select(SyncRun).where(SyncRun.started_at >= week, SyncRun.finished_at.is_not(None))))
    ok_runs = sum(1 for r in runs if r.status == "ok")
    out["data"] = {"sync_runs": len(runs), "sync_success_rate": (ok_runs / len(runs)) if runs else None,
                   "open_findings": db.scalar(select(func.count()).select_from(DqEvent).where(DqEvent.resolved_at.is_(None))) or 0,
                   "fresh_domains": sum(1 for f in freshness.freshness(db) if f["status"] == "fresh"), "domains": len(freshness.freshness(db))}
    return out
