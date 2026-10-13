"""StoreHub sync: correctness, idempotency, cancellations, time zones, failures."""
from datetime import date, datetime

import httpx
import pytest
from sqlalchemy import func, select

from app.models import (ApiErrorLog, InventoryChange, InventoryCurrent, Location, Payment, Product, PurchaseOrder, SalesDay, SalesDayProduct,
                        SalesTx, Supplier, SyncState)
from app.storehub.client import AuthError, StoreHubClient
from app.timeutil import local_iso, now_utc
from tests.conftest import tx

D = "2026-10-05"


def prime(sync):
    sync.sync_stores(); sync.sync_employees(); sync.sync_products(); sync.sync_inventory()


def test_only_get_requests(sync, fake):
    sync.full_sync()  # the fake asserts the HTTP method on every call
    assert fake.calls


def test_stores_are_classified_and_monitored_by_name(sync, session):
    sync.sync_stores()
    locs = {l.name: l for l in session.scalars(select(Location))}
    assert locs["TheDailyCentro Warehouse"].type == "warehouse" and locs["TheDailyCentro Warehouse"].monitored
    assert locs["The Daily Centro - Alpha"].monitored
    assert not locs["Some Other Shop"].monitored


def test_inventory_only_for_monitored_and_change_log(sync, fake, session):
    prime(sync)
    assert session.scalar(select(func.count()).select_from(InventoryCurrent)) == 6
    assert session.scalar(select(func.count()).select_from(InventoryChange)) == 0  # first load is a baseline, not changes
    fake.data["inventory"]["S_A"][0]["quantityOnHand"] = 7
    sync.sync_inventory()
    ch = session.scalars(select(InventoryChange)).all()
    assert len(ch) == 1 and ch[0].old_qty == 10 and ch[0].new_qty == 7 and ch[0].delta == -3


def test_second_sync_changes_nothing(sync, session):
    prime(sync)
    first = session.scalar(select(func.count()).select_from(InventoryCurrent))
    sync.sync_inventory(); sync.sync_products()
    assert session.scalar(select(func.count()).select_from(InventoryCurrent)) == first
    assert session.scalar(select(func.count()).select_from(Product)) == 3


def test_business_date_uses_manila_time_and_totals(sync, fake, session):
    prime(sync)
    # 23:37 UTC on 4 Oct is 07:37 on 5 Oct in Manila
    fake.data["transactions"] = [
        tx("T1", "S_A", "2026-10-04T23:37:00Z", [("P1", 2, 40.0)]),
        tx("T2", "S_A", "2026-10-05T10:00:00Z", [("P2", 1, 10.0)], pay="GCASH"),
        tx("T3", "S_A", "2026-10-05T11:00:00Z", [("P1", 1, 20.0)], cancelled=True),
        tx("T4", "S_X", "2026-10-05T11:00:00Z", [("P1", 1, 20.0)]),  # unmonitored store is ignored
    ]
    sync.sync_transactions(date.fromisoformat(D), date.fromisoformat(D))
    loc = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    day = session.get(SalesDay, (loc.id, date.fromisoformat(D)))
    assert day.tx_count == 2 and day.cancelled_count == 1
    assert day.gross_sales == 50.0 and day.net_sales == 50.0
    assert day.cash_sales == 40.0 and day.other_sales == 10.0
    assert session.scalar(select(func.count()).select_from(SalesTx)) == 3  # T4 skipped
    units = {r.product_id: r.units for r in session.scalars(select(SalesDayProduct).where(SalesDayProduct.location_id == loc.id))}
    p1 = session.scalar(select(Product.id).where(Product.storehub_id == "P1"))
    assert units[p1] == 2.0  # the cancelled sale does not count


def test_rerun_is_idempotent(sync, fake, session):
    prime(sync)
    fake.data["transactions"] = [tx("T1", "S_A", "2026-10-05T10:00:00Z", [("P1", 2, 40.0)])]
    for _ in range(3):
        sync.sync_transactions(date.fromisoformat(D), date.fromisoformat(D))
    assert session.scalar(select(func.count()).select_from(SalesTx)) == 1
    assert session.scalar(select(func.count()).select_from(Payment)) == 1


def test_late_cancellation_is_picked_up(sync, fake, session):
    prime(sync)
    fake.data["transactions"] = [tx("T1", "S_A", "2026-10-05T10:00:00Z", [("P1", 2, 40.0)])]
    sync.sync_transactions(date.fromisoformat(D), date.fromisoformat(D))
    loc = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    assert session.get(SalesDay, (loc.id, date.fromisoformat(D))).net_sales == 40.0
    fake.data["transactions"] = [tx("T1", "S_A", "2026-10-05T10:00:00Z", [("P1", 2, 40.0)], cancelled=True)]
    sync.sync_transactions(date.fromisoformat(D), date.fromisoformat(D))
    session.expire_all()
    assert session.get(SalesDay, (loc.id, date.fromisoformat(D))).net_sales == 0.0
    assert session.scalar(select(func.count()).select_from(SalesTx)) == 1


def test_returns_are_subtracted(sync, fake, session):
    prime(sync)
    fake.data["transactions"] = [tx("T1", "S_A", "2026-10-05T10:00:00Z", [("P1", 3, 60.0)]),
                                 tx("T2", "S_A", "2026-10-05T12:00:00Z", [("P1", 1, 20.0)], ttype="Return", saleInvoiceNumber="T1")]
    sync.sync_transactions(date.fromisoformat(D), date.fromisoformat(D))
    loc = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    day = session.get(SalesDay, (loc.id, date.fromisoformat(D)))
    assert day.gross_sales == 60.0 and day.returns_total == 20.0 and day.net_sales == 40.0 and day.cash_sales == 40.0
    p1 = session.scalar(select(Product.id).where(Product.storehub_id == "P1"))
    assert session.get(SalesDayProduct, (loc.id, date.fromisoformat(D), p1)).units == 2.0


def test_cursor_does_not_advance_when_a_day_fails(sync, fake, session):
    prime(sync)
    fake.fail_next = [500] * 40  # every call fails until the list drains
    out = sync.sync_transactions(date.fromisoformat("2026-10-05"), date.fromisoformat("2026-10-05"))
    st = session.get(SyncState, "sales")
    assert st.last_success_at is None and st.consecutive_failures >= 1
    assert session.scalar(select(func.count()).select_from(ApiErrorLog)) >= 1


def test_purchase_orders_missing_received_and_deletion(sync, fake, session):
    prime(sync)
    fake.data["purchaseOrders"] = [
        {"purchaseOrderId": 1, "status": "open", "supplierId": "SUP1", "targetStoreId": "S_WH", "createdTime": "2026-10-01T01:00:00Z",
         "modifiedTime": "2026-10-01T01:00:00Z", "expectedArrivalDate": "2026-10-03T16:00:00Z", "subTotal": 100, "tax": 12, "discount": 0, "total": 112,
         "orderedItems": [{"productId": "P1", "orderedQuantity": 10, "supplierPrice": 10, "subTotal": 100}]},
        {"purchaseOrderId": 2, "status": "completed", "supplierId": "SUP1", "targetStoreId": "S_WH", "createdTime": "2026-10-01T01:00:00Z",
         "modifiedTime": "2026-10-01T02:00:00Z", "subTotal": 50, "tax": 6, "discount": 0, "total": 56,
         "orderedItems": [{"productId": "P2", "orderedQuantity": 5, "receivedQuantity": 5, "supplierPrice": 10, "subTotal": 50}]}]
    sync.sync_purchase_orders()
    po1 = session.scalar(select(PurchaseOrder).where(PurchaseOrder.sh_po_id == 1))
    assert po1.lines[0].received_qty == 0 and po1.lines[0].received_present is False
    assert session.scalar(select(Supplier.id)) is not None
    p1 = session.scalar(select(Product).where(Product.storehub_id == "P1"))
    assert p1.supplier_id is not None and p1.supplier_source == "po_history"
    fake.data["purchaseOrders"].pop(0)  # PO 1 deleted in StoreHub
    sync.sync_purchase_orders()
    session.expire_all()
    assert session.scalar(select(PurchaseOrder).where(PurchaseOrder.sh_po_id == 1)).missing_from_api is True


def test_manual_supplier_is_not_overwritten(sync, fake, session):
    prime(sync)
    fake.data["purchaseOrders"] = [{"purchaseOrderId": 1, "status": "completed", "supplierId": "SUP1", "targetStoreId": "S_WH", "createdTime": "2026-10-01T01:00:00Z",
                                    "modifiedTime": "2026-10-01T01:00:00Z", "subTotal": 1, "tax": 0, "discount": 0, "total": 1,
                                    "orderedItems": [{"productId": "P1", "orderedQuantity": 1, "receivedQuantity": 1, "supplierPrice": 1, "subTotal": 1}]}]
    sync.sync_purchase_orders()
    p1 = session.scalar(select(Product).where(Product.storehub_id == "P1"))
    sup2 = Supplier(name="Mine"); session.add(sup2); session.flush()
    p1.supplier_id, p1.supplier_source = sup2.id, "manual"; session.commit()
    sync.sync_purchase_orders()
    session.expire_all()
    assert session.scalar(select(Product).where(Product.storehub_id == "P1")).supplier_id == sup2.id


def test_product_disappearing_is_marked(sync, fake, session):
    prime(sync)
    fake.data["products"] = fake.data["products"][:2]
    sync.sync_products()
    session.expire_all()
    assert session.scalar(select(Product).where(Product.storehub_id == "P3")).in_api is False


def test_cost_history_is_recorded(sync, fake, session):
    prime(sync)
    fake.data["products"][0]["cost"] = 13.0
    sync.sync_products()
    from app.models import ProductCostHistory
    assert session.scalar(select(func.count()).select_from(ProductCostHistory).join(Product, Product.id == ProductCostHistory.product_id).where(Product.storehub_id == "P1")) == 2


def test_reconciliation_detects_a_difference(sync, fake, session):
    prime(sync)
    today = now_utc()
    iso = today.strftime("%Y-%m-%dT%H:%M:%SZ")
    fake.data["transactions"] = [tx("T1", "S_A", iso, [("P1", 1, 20.0)])]
    sync.sync_transactions()
    sync.reconcile()
    from app.models import DqEvent
    assert not [e for e in session.scalars(select(DqEvent)) if e.rule == "reconciliation_mismatch" and e.resolved_at is None]
    fake.data["transactions"].append(tx("T2", "S_A", iso, [("P1", 1, 20.0)]))  # exists in StoreHub, never synced
    sync.reconcile()
    session.expire_all()
    ev = [e for e in session.scalars(select(DqEvent)) if e.rule == "reconciliation_mismatch" and e.resolved_at is None]
    assert len(ev) == 1 and ev[0].detail["api_count"] == 2 and ev[0].detail["db_count"] == 1


# ---------------------------------------------------------------- client behaviour
def test_client_retries_then_succeeds(cfg, fake):
    fake.fail_next = [500, 429]
    errs = []
    c = StoreHubClient(transport=httpx.MockTransport(fake.handler), error_sink=errs.append, sleep=lambda s: None)
    class W: wait = lambda self: None
    c.limiter = W()
    assert c.stores()
    assert c.retries == 2 and len(errs) == 2


def test_client_stops_on_auth_error(cfg, fake):
    fake.fail_next = [401]
    c = StoreHubClient(transport=httpx.MockTransport(fake.handler), sleep=lambda s: None)
    class W: wait = lambda self: None
    c.limiter = W()
    with pytest.raises(AuthError):
        c.stores()
    assert c.calls == 1  # never retried


def test_rate_limiter_enforces_spacing():
    import time
    from app.storehub.client import RateLimiter
    rl = RateLimiter(20)  # 50 ms
    t = time.monotonic()
    for _ in range(4):
        rl.wait()
    assert time.monotonic() - t >= 0.14


def test_config_refuses_rates_above_the_safe_ceiling():
    from pydantic import ValidationError
    from app.config import Config
    with pytest.raises(ValidationError):
        Config(storehub_rps=3.0)
