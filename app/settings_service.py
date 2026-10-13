"""Typed access to runtime settings stored in the database, with documented defaults."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Setting

DEFAULTS: dict[str, tuple[Any, str]] = {
    # freshness limits in minutes; data older than this is shown as stale and blocks unsafe orders
    "fresh.sales_min": (20, "Sales data is stale after this many minutes without a successful sync"),
    "fresh.inventory_min": (30, "Inventory data is stale after this many minutes"),
    "fresh.purchase_orders_min": (30, "Purchase order data is stale after this many minutes"),
    "fresh.products_min": (2160, "Product data is stale after this many minutes (36 hours)"),
    "fresh.people_min": (60, "Employees and timesheets are stale after this many minutes"),
    # purchase orders
    "po.stale_days": (7, "An open PO more than this many days past its expected date no longer counts as incoming"),
    # replenishment engine V1
    "repl.mode": ("stock_and_sales", "How suggestions are worked out. sales_only ignores stock on hand (use it while inventory is not accurate); stock_and_sales also subtracts what is on hand"),
    "repl.sales_only_cover_days": (7, "In sales-only mode, each suggestion covers this many days of sales (minus what is already on order)"),
    "repl.lookback_days": (7, "Days of sales history used for average daily sales (one week)"),
    "repl.min_trading_days": (5, "Minimum trading days of history before a recommendation is trusted"),
    "repl.default_supplier_lead_days": (3, "Assumed supplier lead time when the supplier has none set (lowers confidence)"),
    "repl.default_review_days": (1, "Days between orders when the supplier has no review period"),
    "repl.default_safety_days": (2, "Safety stock, in days of sales"),
    "repl.store_lead_days": (1, "Lead time for store replenishment from the warehouse, in days"),
    "repl.max_cover_days": (30, "Never recommend more than this many days of cover (maximum-stock protection)"),
    "repl.default_pack": (12, "Order in whole packs of this many units (12, 24, 36...) when the product has no case pack of its own. Use 1 to allow single units"),
    "repl.min_weekly_units": (6, "Only suggest products that sell at least this many units a week (a store, or all stores for the warehouse). Slower sellers are left out"),
    "counts.repeat_variance_count": (3, "Raise a root-cause issue when this many cycle counts in 90 days found a difference for the same product and place"),
    "counts.reconcile_grace_days": (3, "Days to wait for StoreHub to show a count adjustment before flagging it for review"),
    "repl.require_case_pack": (False, "Block recommendations for products with no case pack"),
    "repl.approval_limit_manager": (20000.0, "Order value above which a store manager cannot approve (needs management)"),
    "repl.reason_codes": ([
        "Promotion ended", "Store closure / renovation", "Supplier constraint", "Known upcoming event",
        "Overstock concern", "Incorrect inventory", "Expected transfer", "Demand anomaly", "Other"],
        "Reason codes for modifying or rejecting"),
    # issues: (acknowledge within minutes, due within hours)
    "issue.sla": ({"P0": [10, 4], "P1": [30, 24], "P2": [240, 72], "P3": [1440, 168]},
                  "Acknowledge minutes and due hours per severity"),
    # data quality
    "dq.sales_spike_sigma": (3.0, "Daily sales above mean + this many standard deviations is flagged"),
    "dq.sales_spike_min": (5000.0, "Ignore sales spikes below this daily amount"),
    # stock transfers: how long without a new StoreHub export before the team is warned
    "transfers.stale_hours": (30, "Hours without a new stock-transfer export before the team is warned"),
    # expiry
    "expiry.warn_days": ([30, 14, 7, 3], "Warning stages in days before expiry"),
    # sync
    "sync.backfill_start": ("2026-07-01", "First business date loaded on the first sales sync"),
    "sync.tx_overlap_days": (3, "Days re-read on every sales sync, to pick up late cancellations"),
    "sync.po_since": ("2026-01-01", "Purchase orders created since this date are loaded"),
    # AI layer
    "ai.enabled": (True, "Turn the AI assistant on or off"),
    "ai.max_calls_per_day": (60, "Cost cap: AI requests allowed per day"),
    "ai.auto_low_risk": (False, "Let the AI run low-risk actions itself (create issue, send reminder) when the policy gate allows. Off by default"),
    "ai.auto_min_samples": (20, "Decided actions of a type needed before that type may run automatically"),
    "ai.auto_min_acceptance": (0.8, "Acceptance rate a decision class must hold before it may run automatically"),
    "ai.min_confidence": (0.6, "Actions proposed with lower confidence always need a person"),
    "app.company": ("The Daily Centro", "Name shown in the app"),
}


def get(db: Session, key: str, default: Any = None) -> Any:
    row = db.get(Setting, key)
    if row is not None:
        return row.value
    if key in DEFAULTS:
        return DEFAULTS[key][0]
    return default


def set_(db: Session, key: str, value: Any) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


def all_settings(db: Session) -> dict[str, Any]:
    stored = {r.key: r.value for r in db.scalars(select(Setting))}
    return {k: stored.get(k, v[0]) for k, v in DEFAULTS.items()}
