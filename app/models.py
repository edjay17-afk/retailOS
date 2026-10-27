"""Database models. Timestamps are naive UTC; business dates are Asia/Manila dates."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .timeutil import now_utc


class SourceEntity:
    """Source identity; legacy storehub_id remains an internal compatibility key."""
    source: Mapped[str] = mapped_column(String(20), default="storehub", server_default="storehub")
    source_account: Mapped[str] = mapped_column(String(80), default="", server_default="")
    external_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)


# --------------------------------------------------------------------------- settings / people
class OrderDocument(Base):
    """Editable print/export copy. Never changes StoreHub stock or receiving records."""
    __tablename__ = "order_document"
    __table_args__ = (UniqueConstraint("kind", "source_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    source_id: Mapped[int] = mapped_column(Integer)
    header: Mapped[dict] = mapped_column(JSON)
    lines: Mapped[list] = mapped_column(JSON)  # decimal inputs stay strings, never floats
    source_hash: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[int] = mapped_column(ForeignKey("user.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class Setting(Base):
    __tablename__ = "setting"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)


class User(Base):
    __tablename__ = "user"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(60), unique=True)
    full_name: Mapped[str] = mapped_column(String(120), default="")
    role: Mapped[str] = mapped_column(String(20))  # staff, store_manager, warehouse, management, admin
    password_hash: Mapped[str] = mapped_column(String(300))
    password_plain: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # shown to admins on the Users page (local single-site deployment)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # retain historical actor references after account deletion
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    location_ids: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)  # None / [] = all locations
    storehub_employee_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class WebSession(Base):
    __tablename__ = "web_session"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    csrf: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    user: Mapped[User] = relationship()


class Employee(SourceEntity, Base):
    __tablename__ = "employee"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    storehub_id: Mapped[str] = mapped_column(String(40), unique=True)
    first_name: Mapped[str] = mapped_column(String(80), default="")
    last_name: Mapped[str] = mapped_column(String(80), default="")
    email: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip() or self.storehub_id


# --------------------------------------------------------------------------- master data
class Location(SourceEntity, Base):
    __tablename__ = "location"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    storehub_id: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    type: Mapped[str] = mapped_column(String(20), default="store")  # store | warehouse
    monitored: Mapped[bool] = mapped_column(Boolean, default=False)
    city: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    manager_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    lead_time_days: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # transfer lead time from warehouse
    opens_at: Mapped[str] = mapped_column(String(5), default="06:00")
    closes_at: Mapped[str] = mapped_column(String(5), default="22:00")


class Supplier(SourceEntity, Base):
    __tablename__ = "supplier"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    storehub_id: Mapped[Optional[str]] = mapped_column(String(40), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(160))
    name_source: Mapped[str] = mapped_column(String(10), default="auto")  # auto (placeholder) | export (from the PO export) | manual
    lead_time_days: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    order_days: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    review_period_days: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    min_order_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    contact: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Product(SourceEntity, Base):
    __tablename__ = "product"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    storehub_id: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(300))
    sku: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    barcode: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)
    category: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    subcategory: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    unit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # tax-exclusive, as given by StoreHub
    cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    track_stock: Mapped[bool] = mapped_column(Boolean, default=False)
    is_parent: Mapped[bool] = mapped_column(Boolean, default=False)
    tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    in_api: Mapped[bool] = mapped_column(Boolean, default=True)  # False once it disappears from /products
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # system-owned configuration (StoreHub has no such fields)
    supplier_id: Mapped[Optional[int]] = mapped_column(ForeignKey("supplier.id"), nullable=True)
    supplier_source: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)  # po_history | manual
    case_pack: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    order_multiple: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    moq: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    perishable: Mapped[bool] = mapped_column(Boolean, default=False)
    replenish_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    min_stock: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_stock: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    safety_days: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    supplier: Mapped[Optional[Supplier]] = relationship()


class ProductCostHistory(Base):
    __tablename__ = "product_cost_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"), index=True)
    cost: Mapped[Optional[float]] = mapped_column(Float)
    unit_price: Mapped[Optional[float]] = mapped_column(Float)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


# --------------------------------------------------------------------------- inventory
class InventoryCurrent(Base):
    __tablename__ = "inventory_current"
    location_id: Mapped[int] = mapped_column(ForeignKey("location.id"), primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"), primary_key=True)
    on_hand: Mapped[float] = mapped_column(Float)
    warning_stock: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ideal_stock: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class InventoryChange(Base):
    """Every observed change of quantityOnHand. StoreHub keeps no history, so we build it from diffs."""
    __tablename__ = "inventory_change"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("location.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"))
    old_qty: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    new_qty: Mapped[float] = mapped_column(Float)
    delta: Mapped[float] = mapped_column(Float)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    __table_args__ = (Index("ix_invchg_loc_prod_time", "location_id", "product_id", "observed_at"),)


class InventorySnapshot(Base):
    __tablename__ = "inventory_snapshot"
    snap_date: Mapped[date] = mapped_column(Date, primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("location.id"), primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"), primary_key=True)
    on_hand: Mapped[float] = mapped_column(Float)


# --------------------------------------------------------------------------- sales
class SalesTx(Base):
    __tablename__ = "sales_tx"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    refid: Mapped[str] = mapped_column(String(60), unique=True)
    invoice_no: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("location.id"))
    employee_sh_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    terminal: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tx_type: Mapped[str] = mapped_column(String(10))  # Sale | Return
    tx_time: Mapped[datetime] = mapped_column(DateTime)
    business_date: Mapped[date] = mapped_column(Date)
    total: Mapped[float] = mapped_column(Float, default=0)
    subtotal: Mapped[float] = mapped_column(Float, default=0)
    tax: Mapped[float] = mapped_column(Float, default=0)
    discount: Mapped[float] = mapped_column(Float, default=0)
    rounded: Mapped[float] = mapped_column(Float, default=0)
    service_charge: Mapped[float] = mapped_column(Float, default=0)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    cancelled_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    return_reason: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    sale_invoice_no: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    channel: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    raw_hash: Mapped[str] = mapped_column(String(40))
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    lines: Mapped[list["SalesLine"]] = relationship(cascade="all, delete-orphan")
    payments: Mapped[list["Payment"]] = relationship(cascade="all, delete-orphan")
    __table_args__ = (Index("ix_sales_tx_loc_date", "location_id", "business_date"),)


class SalesLine(Base):
    __tablename__ = "sales_line"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tx_id: Mapped[int] = mapped_column(ForeignKey("sales_tx.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("product.id"), nullable=True)
    product_sh_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    item_type: Mapped[str] = mapped_column(String(20), default="Item")
    qty: Mapped[float] = mapped_column(Float, default=0)
    unit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    subtotal: Mapped[float] = mapped_column(Float, default=0)
    tax: Mapped[float] = mapped_column(Float, default=0)
    discount: Mapped[float] = mapped_column(Float, default=0)
    total: Mapped[float] = mapped_column(Float, default=0)


class Payment(Base):
    __tablename__ = "payment"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tx_id: Mapped[int] = mapped_column(ForeignKey("sales_tx.id", ondelete="CASCADE"), index=True)
    method: Mapped[str] = mapped_column(String(30), default="Unknown")
    amount: Mapped[float] = mapped_column(Float, default=0)


class SalesDay(Base):
    """Per location and business date totals, recomputed whenever a day is touched."""
    __tablename__ = "sales_day"
    location_id: Mapped[int] = mapped_column(ForeignKey("location.id"), primary_key=True)
    business_date: Mapped[date] = mapped_column(Date, primary_key=True)
    tx_count: Mapped[int] = mapped_column(Integer, default=0)
    cancelled_count: Mapped[int] = mapped_column(Integer, default=0)
    return_count: Mapped[int] = mapped_column(Integer, default=0)
    gross_sales: Mapped[float] = mapped_column(Float, default=0)  # incl. tax, sales only
    returns_total: Mapped[float] = mapped_column(Float, default=0)
    net_sales: Mapped[float] = mapped_column(Float, default=0)  # gross - returns
    tax: Mapped[float] = mapped_column(Float, default=0)
    cash_sales: Mapped[float] = mapped_column(Float, default=0)
    other_sales: Mapped[float] = mapped_column(Float, default=0)
    gross_profit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # StoreHub's own Gross Profit for the day, read from its Sales over time report
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class SalesDayProduct(Base):
    __tablename__ = "sales_day_product"
    location_id: Mapped[int] = mapped_column(ForeignKey("location.id"), primary_key=True)
    business_date: Mapped[date] = mapped_column(Date, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"), primary_key=True)
    units: Mapped[float] = mapped_column(Float, default=0)  # net of returns, excludes cancelled
    net_sales: Mapped[float] = mapped_column(Float, default=0)  # incl. tax
    __table_args__ = (Index("ix_sdp_prod_date", "product_id", "business_date"),)


class SalesShift(Base):
    """One register shift as StoreHub's back-office Shift Reports show it (read only; the API has no shifts). Times are Manila local time."""
    __tablename__ = "sales_shift"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sh_id: Mapped[str] = mapped_column(String(40), unique=True)  # StoreHub's shift id
    location_id: Mapped[int] = mapped_column(ForeignKey("location.id"), index=True)
    register: Mapped[str] = mapped_column(String(20), default="")
    opened_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # empty while the shift is still open
    tx_count: Mapped[int] = mapped_column(Integer, default=0)
    total_sales: Mapped[float] = mapped_column(Float, default=0)
    deposit: Mapped[float] = mapped_column(Float, default=0)
    cashier: Mapped[str] = mapped_column(String(120), default="")
    opening_fund: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    payments: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # {"Cash": [qty, amount], "GCASH": [qty, amount], ...}
    withdrawals_qty: Mapped[int] = mapped_column(Integer, default=0)
    withdrawals: Mapped[float] = mapped_column(Float, default=0)
    void_total: Mapped[float] = mapped_column(Float, default=0)
    refund_total: Mapped[float] = mapped_column(Float, default=0)
    discount_total: Mapped[float] = mapped_column(Float, default=0)
    tax_total: Mapped[float] = mapped_column(Float, default=0)
    detail_ok: Mapped[bool] = mapped_column(Boolean, default=False)  # the shift's own page has been read (cashier, payments, ...)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class ZReading(Base):
    """One official Z-reading copied from StoreHub's back office with GET requests only. Times are Manila local time."""
    __tablename__ = "z_reading"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sh_id: Mapped[str] = mapped_column(String(40), unique=True)  # StoreHub's Z-reading report id
    location_id: Mapped[int] = mapped_column(ForeignKey("location.id"), index=True)
    close_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    z_count: Mapped[int] = mapped_column(Integer, default=0)
    register: Mapped[str] = mapped_column(String(20), default="")
    tx_count: Mapped[int] = mapped_column(Integer, default=0)
    gross_sales: Mapped[float] = mapped_column(Float, default=0)
    detail_ok: Mapped[bool] = mapped_column(Boolean, default=False)  # the official detail page has been read
    detail_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)  # exact section/table text, grouped as StoreHub shows it
    source_text: Mapped[str] = mapped_column(Text, default="")  # source-page text retained for audit; never recalculated
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


# --------------------------------------------------------------------------- purchase orders
class PurchaseOrder(Base):
    __tablename__ = "purchase_order"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sh_po_id: Mapped[int] = mapped_column(Integer, unique=True)  # StoreHub purchaseOrderId
    supplier_id: Mapped[Optional[int]] = mapped_column(ForeignKey("supplier.id"), nullable=True)
    location_id: Mapped[Optional[int]] = mapped_column(ForeignKey("location.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    created_sh: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    modified_sh: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    expected_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    subtotal: Mapped[float] = mapped_column(Float, default=0)
    tax: Mapped[float] = mapped_column(Float, default=0)
    discount: Mapped[float] = mapped_column(Float, default=0)
    total: Mapped[float] = mapped_column(Float, default=0)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    completed_by: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    missing_from_api: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_hash: Mapped[str] = mapped_column(String(40), default="")
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    lines: Mapped[list["PoLine"]] = relationship(cascade="all, delete-orphan")
    supplier: Mapped[Optional[Supplier]] = relationship()
    location: Mapped[Optional[Location]] = relationship()


class PoLine(Base):
    __tablename__ = "po_line"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_order.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("product.id"), nullable=True)
    product_sh_id: Mapped[str] = mapped_column(String(40))
    ordered_qty: Mapped[float] = mapped_column(Float, default=0)
    received_qty: Mapped[float] = mapped_column(Float, default=0)  # StoreHub omits the field when nothing received
    received_present: Mapped[bool] = mapped_column(Boolean, default=False)
    supplier_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_exact: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)  # the supplier price exactly as StoreHub holds it (up to 6 decimals), read from its PO page
    subtotal: Mapped[float] = mapped_column(Float, default=0)
    product: Mapped[Optional[Product]] = relationship()


class Timesheet(Base):
    __tablename__ = "timesheet"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_sh_id: Mapped[str] = mapped_column(String(40))
    location_id: Mapped[Optional[int]] = mapped_column(ForeignKey("location.id"), nullable=True)
    clock_in: Mapped[datetime] = mapped_column(DateTime)
    clock_out: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    __table_args__ = (UniqueConstraint("employee_sh_id", "clock_in"),)


# --------------------------------------------------------------------------- sync / data quality
class SyncRun(Base):
    __tablename__ = "sync_run"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job: Mapped[str] = mapped_column(String(40), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="running")  # running | ok | failed | partial
    target: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    api_calls: Mapped[int] = mapped_column(Integer, default=0)
    received: Mapped[int] = mapped_column(Integer, default=0)
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cursor: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)


class SyncState(Base):
    __tablename__ = "sync_state"
    domain: Mapped[str] = mapped_column(String(30), primary_key=True)  # sales | inventory | purchase_orders | products | people
    last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cursor: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)


class ApiErrorLog(Base):
    __tablename__ = "api_error_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    endpoint: Mapped[str] = mapped_column(String(200))
    params: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_class: Mapped[str] = mapped_column(String(40))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    body_excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class JobLock(Base):
    __tablename__ = "job_lock"
    name: Mapped[str] = mapped_column(String(60), primary_key=True)
    locked_until: Mapped[datetime] = mapped_column(DateTime)
    owner: Mapped[str] = mapped_column(String(60), default="")


class DqEvent(Base):
    __tablename__ = "dq_event"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule: Mapped[str] = mapped_column(String(40), index=True)
    severity: Mapped[str] = mapped_column(String(10))  # info | warning | critical
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    location_id: Mapped[Optional[int]] = mapped_column(ForeignKey("location.id"), nullable=True)
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("product.id"), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    detail: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    issue_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


# --------------------------------------------------------------------------- replenishment
class ReplRun(Base):
    __tablename__ = "repl_run"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    business_date: Mapped[date] = mapped_column(Date)
    calc_version: Mapped[str] = mapped_column(String(20))
    params: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(12), default="ok")  # ok | blocked
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    counts: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class Recommendation(Base):
    __tablename__ = "recommendation"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("repl_run.id"))
    location_id: Mapped[int] = mapped_column(ForeignKey("location.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"))
    supplier_id: Mapped[Optional[int]] = mapped_column(ForeignKey("supplier.id"), nullable=True)
    business_date: Mapped[date] = mapped_column(Date)
    calc_version: Mapped[str] = mapped_column(String(20))
    source_kind: Mapped[str] = mapped_column(String(10), default="supplier")  # supplier | transfer
    ads: Mapped[float] = mapped_column(Float, default=0)
    trading_days: Mapped[int] = mapped_column(Integer, default=0)
    lead_time: Mapped[float] = mapped_column(Float, default=0)
    review_period: Mapped[float] = mapped_column(Float, default=0)
    safety_days: Mapped[float] = mapped_column(Float, default=0)
    safety_stock: Mapped[float] = mapped_column(Float, default=0)
    target_stock: Mapped[float] = mapped_column(Float, default=0)
    on_hand: Mapped[float] = mapped_column(Float, default=0)
    incoming: Mapped[float] = mapped_column(Float, default=0)
    position: Mapped[float] = mapped_column(Float, default=0)
    raw_qty: Mapped[float] = mapped_column(Float, default=0)
    final_qty: Mapped[float] = mapped_column(Float, default=0)
    cases: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    unit_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    value: Mapped[float] = mapped_column(Float, default=0)
    days_cover: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String(14), default="pending")  # pending | needs_review | approved | modified | rejected | ordered | expired
    needs_review_reasons: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    explanation: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    approved_qty: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    saved_qty: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # editable draft; not an approval
    transfer_sheet_id: Mapped[Optional[int]] = mapped_column(ForeignKey("transfer_sheet.id", ondelete="SET NULL"), nullable=True)
    decided_by: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    reason_code: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # the manager's own words
    decision_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    batch_id: Mapped[Optional[int]] = mapped_column(ForeignKey("order_batch.id"), nullable=True)
    received_qty: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # counted at the destination for transfers
    outcome: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    product: Mapped[Product] = relationship()
    location: Mapped[Location] = relationship()
    supplier: Mapped[Optional[Supplier]] = relationship()
    __table_args__ = (UniqueConstraint("location_id", "product_id", "business_date", "calc_version", name="uq_rec_key"),)


class OrderBatch(Base):
    __tablename__ = "order_batch"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier_id: Mapped[Optional[int]] = mapped_column(ForeignKey("supplier.id"), nullable=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("location.id"))
    source_kind: Mapped[str] = mapped_column(String(10), default="supplier")
    status: Mapped[str] = mapped_column(String(14), default="approved")  # approved | entered | received | cancelled
    total_value: Mapped[float] = mapped_column(Float, default=0)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    storehub_po_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    entered_by: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    entered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    dispatched_by: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    dispatched_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    received_by: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    received_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    supplier: Mapped[Optional[Supplier]] = relationship()
    location: Mapped[Location] = relationship()
    lines: Mapped[list[Recommendation]] = relationship(foreign_keys="Recommendation.batch_id")


# --------------------------------------------------------------------------- receiving, expiry, counts
class StockTransferImport(Base):
    """Immutable source CSV and an actor-bound preview/confirmation record."""
    __tablename__ = "stock_transfer_import"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True)
    filename: Mapped[str] = mapped_column(String(240))
    sha256: Mapped[str] = mapped_column(String(64))
    raw_csv: Mapped[bytes] = mapped_column(LargeBinary)
    created_by: Mapped[int] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    imported_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    counts: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class StockTransfer(Base):
    """StoreHub export mirror; these statuses never confirm a RetailOS physical event."""
    __tablename__ = "stock_transfer"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(80), unique=True)
    source_name: Mapped[str] = mapped_column(String(160))
    target_name: Mapped[str] = mapped_column(String(160))
    source_location_id: Mapped[Optional[int]] = mapped_column(ForeignKey("location.id"), nullable=True)
    target_location_id: Mapped[Optional[int]] = mapped_column(ForeignKey("location.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    created_sh: Mapped[datetime] = mapped_column(DateTime)
    shipped_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    received_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    sent_by: Mapped[str] = mapped_column(String(160), default="")
    received_by: Mapped[str] = mapped_column(String(160), default="")
    cancelled_by: Mapped[str] = mapped_column(String(160), default="")
    currency: Mapped[str] = mapped_column(String(20), default="")
    total: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    last_import_id: Mapped[int] = mapped_column(ForeignKey("stock_transfer_import.id"))
    last_import: Mapped[StockTransferImport] = relationship()
    lines: Mapped[list["StockTransferLine"]] = relationship(cascade="all, delete-orphan", order_by="StockTransferLine.line_no")


class StockTransferLine(Base):
    __tablename__ = "stock_transfer_line"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transfer_id: Mapped[int] = mapped_column(ForeignKey("stock_transfer.id"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("product.id"), nullable=True)
    product_name: Mapped[str] = mapped_column(String(300))
    sku: Mapped[str] = mapped_column(String(80), default="")
    serial_no: Mapped[str] = mapped_column(String(200), default="")
    category: Mapped[str] = mapped_column(String(120), default="")
    quantity: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(80), default="")
    unit_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    subtotal: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    __table_args__ = (UniqueConstraint("transfer_id", "line_no", name="uq_stock_transfer_line"),)


class StockTransferReceipt(Base):
    """V2.1: the destination store's own physical count of what arrived on a StoreHub transfer.
    StoreHub's Received status is closed by the warehouse account; this record is the store's independent confirmation."""
    __tablename__ = "stock_transfer_receipt"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transfer_id: Mapped[int] = mapped_column(ForeignKey("stock_transfer.id"), unique=True, index=True)  # one confirmation per transfer
    location_id: Mapped[Optional[int]] = mapped_column(ForeignKey("location.id"), nullable=True)  # the store that counted
    counted_by: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    counted_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    differences: Mapped[int] = mapped_column(Integer, default=0)
    transfer: Mapped[StockTransfer] = relationship()
    counter: Mapped[Optional[User]] = relationship()
    lines: Mapped[list["StockTransferReceiptLine"]] = relationship(cascade="all, delete-orphan")


class StockTransferReceiptLine(Base):
    __tablename__ = "stock_transfer_receipt_line"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("stock_transfer_receipt.id", ondelete="CASCADE"), index=True)
    transfer_line_id: Mapped[int] = mapped_column(ForeignKey("stock_transfer_line.id"))
    expected_qty: Mapped[float] = mapped_column(Float)
    counted_qty: Mapped[float] = mapped_column(Float)
    transfer_line: Mapped[StockTransferLine] = relationship()
    __table_args__ = (UniqueConstraint("receipt_id", "transfer_line_id", name="uq_st_receipt_line"),)


class NewProduct(Base):
    """A product registered by staff before it exists in StoreHub. Mirrored to the Lark "New Product" table and
    exported as an Excel file that the ShadowBot RPA keys into StoreHub. StoreHub stays the source of truth."""
    __tablename__ = "new_product"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    barcode: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(120), default="")
    supplier_name: Mapped[str] = mapped_column(String(160), default="")
    cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    qty: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # initial quantity on hand, if the goods already arrived
    expiry_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)  # mirrors the Lark table's Expiration Date column
    note: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    # product entry process: new = barcode not known yet, existing = barcode already a StoreHub product (re-entry of stock/cost)
    entry_type: Mapped[str] = mapped_column(String(10), default="new")
    brand: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    flavor: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)  # one record per flavor
    unit: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)  # piece | pack | case
    units_per_pack: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pack_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # supplier price of one pack/case; cost = pack_price / units_per_pack
    packs: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # packs counted; qty = packs * units_per_pack (base units)
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("product.id"), nullable=True)  # existing products only
    status: Mapped[str] = mapped_column(String(12), default="recorded")  # recorded | lark_sent | lark_failed
    lark_record_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    lark_error: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    excel_path: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)  # relative to data_dir
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    creator: Mapped[Optional[User]] = relationship()


class NewProductList(Base):
    """A batch of new products staff scanned (barcode, name, supplier, category, quantity). Saving it makes a PDF of the product information."""
    __tablename__ = "new_product_list"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(12), default="draft", index=True)  # draft | saved
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    saved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    excel_path: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)  # relative to data_dir: the Excel file the RPA picks up
    creator: Mapped[Optional[User]] = relationship()
    items: Mapped[list["NewProductItem"]] = relationship(cascade="all, delete-orphan", order_by="NewProductItem.id")


class NewProductItem(Base):
    __tablename__ = "new_product_item"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    list_id: Mapped[int] = mapped_column(ForeignKey("new_product_list.id", ondelete="CASCADE"), index=True)
    barcode: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(300))
    supplier_name: Mapped[str] = mapped_column(String(160), default="")
    category: Mapped[str] = mapped_column(String(120), default="")
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # selling price; StoreHub needs one to create the product
    cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # supplier cost per piece (optional, StoreHub takes it on create)
    # StoreHub push: pending (Excel/RPA path or awaiting a push) | created (in StoreHub, linked) | failed (StoreHub said no)
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    storehub_product_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    push_error: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    __table_args__ = (UniqueConstraint("list_id", "barcode", name="uq_new_product_item"),)


class StoreHubPush(Base):
    """A product RetailOS created in StoreHub — from a saved new-product list, or quick-created from a purchase order or a
    transfer sheet. Kept even if the list is later deleted, so there is always a record of what went in, when and by whom."""
    __tablename__ = "storehub_push"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    storehub_product_id: Mapped[str] = mapped_column(String(40), index=True)
    barcode: Mapped[str] = mapped_column(String(80), default="")
    name: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(120), default="")
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    origin: Mapped[str] = mapped_column(String(20), default="list")  # list | purchase_order | transfer_sheet | quick
    list_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # the NP list it came from (plain id: the list may be deleted)
    pushed_by: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    pushed_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, index=True)
    pusher: Mapped[Optional[User]] = relationship()


class TransferSheet(Base):
    """A warehouse-to-store transfer list built by scanning products (replaces the notebook). draft -> submitted -> in_storehub."""
    __tablename__ = "transfer_sheet"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_location_id: Mapped[int] = mapped_column(ForeignKey("location.id"))
    target_location_id: Mapped[int] = mapped_column(ForeignKey("location.id"), index=True)
    status: Mapped[str] = mapped_column(String(12), default="draft", index=True)  # draft | submitted | in_storehub | cancelled
    note: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    submitted_by: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    excel_path: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)  # relative to data_dir, the file the RPA picks up
    sh_reference: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)  # the StoreHub transfer number, e.g. ST0071
    source: Mapped[Location] = relationship(foreign_keys=[source_location_id])
    target: Mapped[Location] = relationship(foreign_keys=[target_location_id])
    creator: Mapped[Optional[User]] = relationship(foreign_keys=[created_by])
    lines: Mapped[list["TransferSheetLine"]] = relationship(cascade="all, delete-orphan", order_by="TransferSheetLine.id")


class TransferSheetLine(Base):
    __tablename__ = "transfer_sheet_line"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sheet_id: Mapped[int] = mapped_column(ForeignKey("transfer_sheet.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("product.id"), nullable=True)  # empty while StoreHub does not know the product yet
    manual_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)  # what staff typed for a product that is not in StoreHub
    manual_barcode: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    qty: Mapped[float] = mapped_column(Float)
    unit_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # cost when scanned, so later cost changes do not rewrite the sheet
    product: Mapped[Optional[Product]] = relationship()
    __table_args__ = (UniqueConstraint("sheet_id", "product_id", name="uq_transfer_sheet_line"),)


class PoDraft(Base):
    """A purchase order being written in Retail OS: the supplier and where it is delivered, then the products with quantity and supplier price.
    Quantities and prices are kept as exact decimal text (never a float), so no digit of a price such as 254.4642857 is lost. draft -> saved."""
    __tablename__ = "po_draft"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("supplier.id"))
    location_id: Mapped[int] = mapped_column(ForeignKey("location.id"))
    status: Mapped[str] = mapped_column(String(12), default="draft", index=True)  # draft | saved
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    saved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    excel_path: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)  # relative to data_dir
    tax_rate: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # percent, exact text, fixed when the order starts (12 = 12%)
    # products quick-created in StoreHub from this order: when the order is saved, their quantity is split between the stores
    new_product_ids: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    supplier: Mapped[Supplier] = relationship()
    location: Mapped[Location] = relationship()
    creator: Mapped[Optional[User]] = relationship()
    lines: Mapped[list["PoDraftLine"]] = relationship(cascade="all, delete-orphan", order_by="PoDraftLine.id")


class PoDraftLine(Base):
    __tablename__ = "po_draft_line"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey("po_draft.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"))
    qty: Mapped[str] = mapped_column(String(60))  # exact decimal text
    price: Mapped[str] = mapped_column(String(60))  # supplier price in PHP, exact decimal text
    product: Mapped[Product] = relationship()
    __table_args__ = (UniqueConstraint("draft_id", "product_id", name="uq_po_draft_line"),)


class WastageEntry(Base):
    """One wasted product, entered in Retail OS and mirrored to the Lark Wastage Record table (Store, Date Created, Product, Category, Supplier, Quantity, Reason)."""
    __tablename__ = "wastage_entry"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("location.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"))
    entry_date: Mapped[date] = mapped_column(Date, index=True)  # the "Date Created" column
    qty: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(120), default="")  # copied from the product when recorded, so later renames do not rewrite history
    supplier_name: Mapped[str] = mapped_column(String(200), default="")
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    product: Mapped[Product] = relationship()
    location: Mapped[Location] = relationship()


class Receiving(Base):
    __tablename__ = "receiving"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_order.id"))
    location_id: Mapped[Optional[int]] = mapped_column(ForeignKey("location.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="open")  # open | completed | mismatch
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    photo_path: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)  # evidence of the delivery (damage, condition)
    po: Mapped[PurchaseOrder] = relationship()
    lines: Mapped[list["ReceivingLine"]] = relationship(cascade="all, delete-orphan")


class ReceivingLine(Base):
    __tablename__ = "receiving_line"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    receiving_id: Mapped[int] = mapped_column(ForeignKey("receiving.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("product.id"), nullable=True)
    ordered_qty: Mapped[float] = mapped_column(Float, default=0)
    storehub_received_qty: Mapped[float] = mapped_column(Float, default=0)
    counted_qty: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    damaged_qty: Mapped[float] = mapped_column(Float, default=0)
    expiry_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    batch_no: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    product: Mapped[Optional[Product]] = relationship()


class ExpiryBatch(Base):
    __tablename__ = "expiry_batch"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("location.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"))
    qty: Mapped[float] = mapped_column(Float)
    expiry_date: Mapped[date] = mapped_column(Date, index=True)
    batch_no: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    received_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="active")  # active | cleared | written_off
    source: Mapped[str] = mapped_column(String(20), default="receiving")
    note: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    product: Mapped[Product] = relationship()
    location: Mapped[Location] = relationship()


class CycleCount(Base):
    __tablename__ = "cycle_count"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("location.id"))
    business_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default="open")  # open | submitted | approved
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    approved_by: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    location: Mapped[Location] = relationship()
    lines: Mapped[list["CycleCountLine"]] = relationship(cascade="all, delete-orphan")


class CycleCountLine(Base):
    __tablename__ = "cycle_count_line"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    count_id: Mapped[int] = mapped_column(ForeignKey("cycle_count.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"))
    reason: Mapped[str] = mapped_column(String(30))  # negative_stock | high_value | high_sales | repeat_mismatch | random
    system_qty: Mapped[float] = mapped_column(Float)
    counted_qty: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    variance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    product: Mapped[Product] = relationship()


class StockEvent(Base):
    """An inventory-changing real-world event captured on the floor (damage, expiry, an unplanned delivery). The change itself
    is keyed into the StoreHub back office by a person; verified_at is set once a sync observes the matching stock move."""
    __tablename__ = "stock_event"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # write_off | unplanned_receipt
    location_id: Mapped[int] = mapped_column(ForeignKey("location.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"))
    qty: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(40))
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    photo_path: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)  # evidence: damaged goods, delivery papers
    supplier_id: Mapped[Optional[int]] = mapped_column(ForeignKey("supplier.id"), nullable=True)  # unplanned receipts only
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    verify_issue_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # the "key it into StoreHub" to-do, closed on verification
    product: Mapped[Product] = relationship()
    location: Mapped[Location] = relationship()
    supplier: Mapped[Optional[Supplier]] = relationship()


class InventoryEvent(Base):
    """V2.1 spec 4: the trace of one inventory-changing real-world event. It is NOT a second ledger: StoreHub stays the stock number.
    The event says what a person saw or did and whether a later StoreHub state supports it (reconciliation_status)."""
    __tablename__ = "inventory_event"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_uid: Mapped[str] = mapped_column(String(80), unique=True)  # idempotency key: the same real event is never recorded twice
    event_type: Mapped[str] = mapped_column(String(24), index=True)
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("product.id"), nullable=True)
    location_id: Mapped[Optional[int]] = mapped_column(ForeignKey("location.id"), nullable=True, index=True)
    quantity: Mapped[float] = mapped_column(Float, default=0)  # always positive; direction carries the sign
    direction: Mapped[str] = mapped_column(String(1), default="+")
    actor_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    actor_role: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    reason_code: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    reason_text: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    evidence_path: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    source_system: Mapped[str] = mapped_column(String(12), default="retailos")
    storehub_reference: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    related_po_id: Mapped[Optional[int]] = mapped_column(ForeignKey("purchase_order.id"), nullable=True, index=True)
    related_transfer_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    reconciliation_status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    reconciled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    reconciliation_note: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    audit_event_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    product: Mapped[Optional[Product]] = relationship()
    location: Mapped[Optional[Location]] = relationship()


# --------------------------------------------------------------------------- inspections / issues
PHOTO_ONLY_TEMPLATES = ("store_daily",)  # checklists reviewed from their photos (they go to Lark): no pass/fail and no comment per item


class InspectionTemplate(Base):
    __tablename__ = "inspection_template"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    location_type: Mapped[str] = mapped_column(String(20))  # store | warehouse
    slot: Mapped[str] = mapped_column(String(12))  # opening | midday | closing | daily
    due_time: Mapped[str] = mapped_column(String(5), default="10:00")  # local HH:MM, task is overdue after this
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    items: Mapped[list["TemplateItem"]] = relationship(cascade="all, delete-orphan", order_by="TemplateItem.sort")

    @property
    def photo_only(self) -> bool:
        return self.code in PHOTO_ONLY_TEMPLATES


class TemplateItem(Base):
    __tablename__ = "template_item"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("inspection_template.id", ondelete="CASCADE"), index=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    label: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(10), default="check")  # check | number | text | video
    min_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    unit: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    fail_severity: Mapped[str] = mapped_column(String(2), default="P2")
    photo_on_fail: Mapped[bool] = mapped_column(Boolean, default=True)


class Inspection(Base):
    __tablename__ = "inspection"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("inspection_template.id"))
    location_id: Mapped[int] = mapped_column(ForeignKey("location.id"))
    business_date: Mapped[date] = mapped_column(Date)
    due_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending | in_progress | completed | late | missed | submitted (external Lark report)
    assigned_to: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_by: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    clocked_in_ok: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    missed_alerted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    template: Mapped[InspectionTemplate] = relationship()
    location: Mapped[Location] = relationship()
    results: Mapped[list["InspectionResult"]] = relationship(cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint("template_id", "location_id", "business_date", name="uq_inspection_key"),)


class InspectionResult(Base):
    __tablename__ = "inspection_result"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inspection_id: Mapped[int] = mapped_column(ForeignKey("inspection.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("template_item.id"))
    value: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    passed: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    photo_path: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    photo_paths: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    video_paths: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)  # a walkthrough can have several videos; photo_path keeps the first
    item: Mapped[TemplateItem] = relationship()

    @property
    def videos(self) -> list[str]:
        """Every video of a walkthrough item (older inspections only have the one in photo_path)."""
        return self.video_paths or ([self.photo_path] if self.photo_path and self.item.kind == "video" else [])

    @property
    def photos(self) -> list[str]:
        """Keep single-photo inspections visible after the multi-photo upgrade."""
        return self.photo_paths or ([self.photo_path] if self.photo_path and self.item.kind != "video" else [])


class Issue(Base):
    __tablename__ = "issue"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(30), index=True)
    severity: Mapped[str] = mapped_column(String(2))  # P0 P1 P2 P3
    location_id: Mapped[Optional[int]] = mapped_column(ForeignKey("location.id"), nullable=True)
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("product.id"), nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="manual")  # inspection | system | manual | receiving | cash | count
    source_ref: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    title: Mapped[str] = mapped_column(String(250))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    due_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(14), default="open", index=True)  # open | acknowledged | in_progress | resolved | closed
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    verified_by: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    resolution_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    escalation_level: Mapped[int] = mapped_column(Integer, default=0)
    last_escalated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    location: Mapped[Optional[Location]] = relationship()
    product: Mapped[Optional[Product]] = relationship()
    owner: Mapped[Optional[User]] = relationship(foreign_keys=[owner_id])
    events: Mapped[list["IssueEvent"]] = relationship(cascade="all, delete-orphan", order_by="IssueEvent.at")


class IssueEvent(Base):
    __tablename__ = "issue_event"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issue.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(20))  # created | comment | status | assign | escalate | evidence
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    photo_path: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    user: Mapped[Optional[User]] = relationship()


# --------------------------------------------------------------------------- notifications / audit
class Notification(Base):
    __tablename__ = "notification"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True, index=True)
    role: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    location_id: Mapped[Optional[int]] = mapped_column(ForeignKey("location.id"), nullable=True)
    level: Mapped[str] = mapped_column(String(10), default="info")
    title: Mapped[str] = mapped_column(String(250))
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    link: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, unique=True)
    lark_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class AuditEvent(Base):
    """Append-only (enforced with database triggers)."""
    __tablename__ = "audit_event"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now_utc, index=True)
    actor_type: Mapped[str] = mapped_column(String(10))  # human | system | ai
    actor_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    action: Mapped[str] = mapped_column(String(50), index=True)
    entity_type: Mapped[str] = mapped_column(String(30), index=True)
    entity_id: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    before: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    after: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    context: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    prev_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class LarkLink(Base):
    """Maps internal entities to Lark Base record ids (used by the optional Lark mirror)."""
    __tablename__ = "lark_link"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[str] = mapped_column(String(40))
    lark_table: Mapped[str] = mapped_column(String(40))
    lark_record_id: Mapped[str] = mapped_column(String(40))
    content_hash: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    __table_args__ = (UniqueConstraint("entity_type", "entity_id", "lark_table"),)


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


# --------------------------------------------------------------------------- AI layer
class AiOutput(Base):
    """Every AI answer, with exactly what it was given (context snapshot, prompt and context versions) so it can be audited and reproduced."""
    __tablename__ = "ai_output"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(12), index=True)  # brief | chat | explain
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, index=True)
    provider: Mapped[str] = mapped_column(String(20), default="")
    model: Mapped[str] = mapped_column(String(60), default="")
    prompt_version: Mapped[str] = mapped_column(String(20), default="")
    context_version: Mapped[str] = mapped_column(String(20), default="")
    context_hash: Mapped[str] = mapped_column(String(40), default="")
    context_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    question: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    raw_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    validation: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    tool_log: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="ok")  # ok | failed
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 1 useful, 0 not useful
    rating_note: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    user: Mapped[Optional[User]] = relationship()
    actions: Mapped[list["AiAction"]] = relationship(cascade="all, delete-orphan")


class AiAction(Base):
    """An action the AI proposes. Nothing runs until it passes the policy gate, and high-risk actions need a person to approve."""
    __tablename__ = "ai_action"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    output_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ai_output.id", ondelete="CASCADE"), nullable=True, index=True)
    tool: Mapped[str] = mapped_column(String(40))
    args: Mapped[dict] = mapped_column(JSON)
    risk: Mapped[str] = mapped_column(String(6))  # read | low | high
    why: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="proposed", index=True)  # proposed | needs_approval | executed | rejected | failed | denied
    gate: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    proposed_for: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    decided_by: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ComplianceItem(Base):
    """Permits, certificates and equipment service dates, with 30-day warnings (proposal flow G)."""
    __tablename__ = "compliance_item"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(14))  # permit | certificate | equipment
    name: Mapped[str] = mapped_column(String(160))
    location_id: Mapped[Optional[int]] = mapped_column(ForeignKey("location.id"), nullable=True)
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"), nullable=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, index=True, nullable=True)  # the permit's expiry date; empty = the paper has no expiry
    processed_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)  # when the permit was processed
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="active")  # active | renewed | retired
    reference: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    document_path: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    location: Mapped[Optional[Location]] = relationship()
    owner: Mapped[Optional[User]] = relationship()


# Loyverse data is account-scoped and never mixed into StoreHub mirrors.
class LoyverseAccount(Base):
    reporting_catalog_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    reporting_history_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    reporting_error: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    reporting_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    __tablename__ = "loyverse_account"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    catalog_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    history_from: Mapped[Optional[date]] = mapped_column(Date)
    last_error: Mapped[Optional[str]] = mapped_column(String(200))


class LoyverseStore(Base):
    __tablename__ = "loyverse_store"
    account_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LoyverseVariant(Base):
    __tablename__ = "loyverse_variant"
    account_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    variant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    item_id: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(400))
    sku: Mapped[Optional[str]] = mapped_column(String(120))
    barcode: Mapped[Optional[str]] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sold_by_weight: Mapped[bool] = mapped_column(Boolean, default=False)
    composite: Mapped[bool] = mapped_column(Boolean, default=False)


class LoyverseReceipt(Base):
    __tablename__ = "loyverse_receipt"
    account_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    receipt_number: Mapped[str] = mapped_column(String(100), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(80), index=True)
    business_date: Mapped[date] = mapped_column(Date, index=True)
    receipt_type: Mapped[str] = mapped_column(String(10))
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    lines: Mapped[list] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
