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
    "product.web_lookup": (True, "On the new-product page, look the scanned barcode up on Open Food Facts to pre-fill name, brand and category. Only the barcode is sent"),
    "counts.repeat_variance_count": (3, "Raise a root-cause issue when this many cycle counts in 90 days found a difference for the same product and place"),
    "counts.reconcile_grace_days": (3, "Days to wait for StoreHub to show a count adjustment before flagging it for review"),
    "repl.require_case_pack": (False, "Block recommendations for products with no case pack"),
    "repl.approval_limit_manager": (20000.0, "Order value above which a store manager cannot approve (needs management)"),
    "repl.excluded_categories": (["Food Service (Meals, Drinks & Pastries)", "Daily Cravings (Hot Meals)", "Maple & Ovens"], "Product categories that Auto replenishment leaves out (made or sold fresh in the store, not ordered from the warehouse)"),
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
    "product.vat_rate": (12, "Sales tax percent. StoreHub gives the price without tax; the retail price shown in Product settings adds this tax"),
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


# ------------------------------------------------------------------------------------------------ presentation metadata for the /settings page
# (group id, title, subtitle). "advanced" catches keys that have no META entry.
GROUPS: list[tuple[str, str, str]] = [
    ("general", "General", "Name and tax used across the app"),
    ("freshness", "Data freshness", "Data older than this is shown as stale and blocks unsafe orders"),
    ("replenishment", "Replenishment", "How suggestions are worked out"),
    ("orders", "Orders, transfers and counts", ""),
    ("issues", "Issues and escalation", "Acknowledge minutes and due hours per severity"),
    ("dq", "Data quality and expiry", ""),
    ("sync", "Sync (advanced)", "Changing these re-reads history; use with care"),
    ("ai", "AI assistant (sensitive)", "Changes here are confirmed before they apply"),
    ("advanced", "Advanced", ""),
]

# Per key: label (short row title), control type (bool, int, float, str, date, select, lines, sla), group, optional min/max,
# options for select, "item": int for a lines list of numbers, and danger=True for settings that get a confirmation dialog.
META: dict[str, dict] = {
    "app.company": {"group": "general", "type": "str", "label": "Company name"},
    "product.vat_rate": {"group": "general", "type": "float", "min": 0, "max": 99, "label": "Sales tax percent"},
    "product.web_lookup": {"group": "general", "type": "bool", "label": "Barcode web lookup"},
    "fresh.sales_min": {"group": "freshness", "type": "int", "min": 5, "label": "Sales data stale after (minutes)"},
    "fresh.inventory_min": {"group": "freshness", "type": "int", "min": 5, "label": "Inventory data stale after (minutes)"},
    "fresh.purchase_orders_min": {"group": "freshness", "type": "int", "min": 5, "label": "Purchase order data stale after (minutes)"},
    "fresh.products_min": {"group": "freshness", "type": "int", "min": 60, "label": "Product data stale after (minutes)"},
    "fresh.people_min": {"group": "freshness", "type": "int", "min": 5, "label": "People data stale after (minutes)"},
    "repl.mode": {"group": "replenishment", "type": "select", "options": ["stock_and_sales", "sales_only"], "danger": True, "label": "Mode"},
    "repl.sales_only_cover_days": {"group": "replenishment", "type": "int", "min": 1, "max": 60, "label": "Sales-only cover (days)"},
    "repl.lookback_days": {"group": "replenishment", "type": "int", "min": 1, "max": 90, "label": "Days of sales history"},
    "repl.min_trading_days": {"group": "replenishment", "type": "int", "min": 0, "label": "Minimum trading days"},
    "repl.default_supplier_lead_days": {"group": "replenishment", "type": "int", "min": 0, "label": "Default supplier lead (days)"},
    "repl.default_review_days": {"group": "replenishment", "type": "int", "min": 1, "label": "Default review period (days)"},
    "repl.default_safety_days": {"group": "replenishment", "type": "int", "min": 0, "label": "Safety stock (days of sales)"},
    "repl.store_lead_days": {"group": "replenishment", "type": "int", "min": 0, "label": "Store transfer lead (days)"},
    "repl.max_cover_days": {"group": "replenishment", "type": "int", "min": 1, "label": "Maximum cover (days)"},
    "repl.default_pack": {"group": "replenishment", "type": "int", "min": 1, "label": "Default case pack"},
    "repl.min_weekly_units": {"group": "replenishment", "type": "int", "min": 0, "label": "Minimum weekly units"},
    "repl.require_case_pack": {"group": "replenishment", "type": "bool", "label": "Block recommendations with no case pack"},
    "repl.approval_limit_manager": {"group": "replenishment", "type": "float", "min": 0, "danger": True, "label": "Manager approval limit"},
    "repl.excluded_categories": {"group": "replenishment", "type": "lines", "label": "Excluded categories"},
    "repl.reason_codes": {"group": "replenishment", "type": "lines", "label": "Reason codes"},
    "po.stale_days": {"group": "orders", "type": "int", "min": 1, "label": "Stale open PO (days)"},
    "transfers.stale_hours": {"group": "orders", "type": "int", "min": 1, "label": "Transfer export stale (hours)"},
    "counts.repeat_variance_count": {"group": "orders", "type": "int", "min": 1, "label": "Repeat variance counts"},
    "counts.reconcile_grace_days": {"group": "orders", "type": "int", "min": 0, "label": "Reconcile grace (days)"},
    "issue.sla": {"group": "issues", "type": "sla", "label": "Acknowledge and due times"},
    "dq.sales_spike_sigma": {"group": "dq", "type": "float", "min": 1, "label": "Sales spike (standard deviations)"},
    "dq.sales_spike_min": {"group": "dq", "type": "float", "min": 0, "label": "Ignore spikes below"},
    "expiry.warn_days": {"group": "dq", "type": "lines", "item": "int", "label": "Expiry warning stages (days)"},
    "sync.backfill_start": {"group": "sync", "type": "date", "danger": True, "label": "Sales backfill start"},
    "sync.tx_overlap_days": {"group": "sync", "type": "int", "min": 0, "max": 30, "label": "Sales re-read overlap (days)"},
    "sync.po_since": {"group": "sync", "type": "date", "danger": True, "label": "POs created since"},
    "ai.enabled": {"group": "ai", "type": "bool", "label": "AI assistant enabled"},
    "ai.max_calls_per_day": {"group": "ai", "type": "int", "min": 0, "label": "Cost cap: AI requests per day"},
    "ai.auto_low_risk": {"group": "ai", "type": "bool", "danger": True, "label": "Let the AI run low-risk actions itself"},
    "ai.auto_min_samples": {"group": "ai", "type": "int", "min": 0, "label": "Minimum samples before auto"},
    "ai.auto_min_acceptance": {"group": "ai", "type": "float", "min": 0, "max": 1, "label": "Minimum acceptance rate"},
    "ai.min_confidence": {"group": "ai", "type": "float", "min": 0, "max": 1, "label": "Minimum confidence to act"},
}


def meta_for(key: str) -> dict:
    """The control metadata for a setting; a key without an entry gets a type inferred from its default."""
    m = dict(META.get(key, {}))
    if "type" not in m:
        d = DEFAULTS[key][0]
        m["type"] = ("bool" if isinstance(d, bool) else "int" if isinstance(d, int) else "float" if isinstance(d, float)
                     else "lines" if isinstance(d, list) else "sla" if isinstance(d, dict) else "str")
    m.setdefault("group", "advanced")
    return m


def parse_value(key: str, form) -> tuple[Any, str | None]:
    """Validate the raw form fields for a setting against its control type. Returns (typed value, error message)."""
    if key not in DEFAULTS:
        return None, "unknown setting"
    m = meta_for(key)
    t = m["type"]
    get = lambda name: str(form.get(name, "") or "").strip()  # noqa: E731
    try:
        if t == "bool":
            return get("value") in ("1", "true", "on"), None
        if t == "sla":
            out = {}
            for sev in ("P0", "P1", "P2", "P3"):
                ack, due = int(get(sev.lower() + "_ack")), int(get(sev.lower() + "_due"))
                if ack < 0 or due <= 0:
                    raise ValueError
                out[sev] = [ack, due]
            return out, None
        raw = get("value")
        if t == "int":
            v: Any = int(raw)
        elif t == "float":
            v = float(raw)
        elif t == "date":
            from datetime import date
            date.fromisoformat(raw)
            v = raw
        elif t == "select":
            if raw not in m.get("options", []):
                return None, "choose one of the listed options"
            v = raw
        elif t == "lines":
            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
            if not lines:
                return None, "at least one line is required"
            v = [int(ln) for ln in lines] if m.get("item") == "int" else lines
        else:
            v = raw
    except (ValueError, TypeError):
        return None, "invalid value"
    if t in ("int", "float"):
        if "min" in m and v < m["min"]:
            return None, f"must be at least {m['min']}"
        if "max" in m and v > m["max"]:
            return None, f"must be at most {m['max']}"
    if not isinstance(v, type(DEFAULTS[key][0])):
        return None, "invalid value"
    return v, None
