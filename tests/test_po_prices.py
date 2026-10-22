"""The exact supplier price of a purchase order line: StoreHub's API sends it rounded to 2 decimals, its PO page holds up to 6; the robot reads the page, the price is kept."""
from datetime import timedelta

from sqlalchemy import select

from app import robot
from app.models import PoLine, Product, PurchaseOrder, Supplier
from app.services import po_drafts as pod
from app.services import po_prices
from app.timeutil import now_utc
from tests.test_services import seeded


def _po(fake, no=156, price=254.46, qty=12, extra=None):
    ts = (now_utc() - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    po = {"purchaseOrderId": no, "status": "partiallyReceived", "supplierId": "SUP1", "targetStoreId": "S_WH", "createdTime": ts, "modifiedTime": ts, "expectedArrivalDate": ts,
          "subTotal": round(price * qty, 2), "tax": 0, "discount": 0, "total": 1,
          "orderedItems": [{"productId": "P1", "orderedQuantity": qty, "receivedQuantity": qty, "supplierPrice": price, "subTotal": round(price * qty, 2)}]}
    po.update(extra or {})
    fake.data["purchaseOrders"] = [po]


def test_the_robot_prices_are_saved_by_product_name_and_replace_the_rounded_ones(sync, fake, session, monkeypatch):
    seeded(sync, fake, session)
    _po(fake)
    sync.sync_purchase_orders(); session.expire_all()
    line = session.scalar(select(PoLine))
    assert line.supplier_price == 254.46 and line.price_exact is None and len(po_prices.pending(session)) == 1  # the API alone has only the rounded price
    name = session.scalar(select(Product).where(Product.storehub_id == "P1")).name
    monkeypatch.setattr(robot, "configured", lambda: True)
    asked = {}

    def fake_fetch(wanted, earliest, today):
        asked.update(wanted=wanted)
        return {156: [(name, "254.464285")]}
    out = po_prices.run(session, fetch=fake_fetch)
    assert out["ok"] and out["lines"] == 1 and asked["wanted"] == {156} and out["still_missing"] == 0
    session.expire_all()
    line = session.scalar(select(PoLine))
    assert line.price_exact == "254.464285" and line.supplier_price == 254.464285 and po_prices.pending(session) == []
    assert po_prices.due(session) is False  # nothing left to read


def test_the_exact_price_survives_a_resync_while_storehub_sends_the_same_rounded_price(sync, fake, session):
    seeded(sync, fake, session)
    _po(fake)
    sync.sync_purchase_orders(); session.expire_all()
    po = session.scalar(select(PurchaseOrder))
    name = session.scalar(select(Product).where(Product.storehub_id == "P1")).name
    po_prices.apply_po(po, [(name, "254.464285")]); session.commit()
    _po(fake, extra={"modifiedTime": (now_utc()).strftime("%Y-%m-%dT%H:%M:%SZ"), "notes": "changed"})  # StoreHub changed the PO: the lines are rebuilt
    sync.sync_purchase_orders(); session.expire_all()
    line = session.scalar(select(PoLine))
    assert line.price_exact == "254.464285" and line.supplier_price == 254.464285
    _po(fake, price=300.0, extra={"modifiedTime": (now_utc() + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")})  # the price itself changed: read again
    sync.sync_purchase_orders(); session.expire_all()
    line = session.scalar(select(PoLine))
    assert line.price_exact is None and line.supplier_price == 300.0 and len(po_prices.pending(session)) == 1


def test_last_paid_price_for_a_new_order_is_storehubs_own_price(sync, fake, session):
    seeded(sync, fake, session)
    _po(fake)
    sync.sync_purchase_orders(); session.expire_all()
    po = session.scalar(select(PurchaseOrder))
    name = session.scalar(select(Product).where(Product.storehub_id == "P1")).name
    po_prices.apply_po(po, [(name, "254.464285")]); session.commit()
    sup = session.scalar(select(Supplier).where(Supplier.storehub_id == "SUP1"))
    pid = session.scalar(select(Product.id).where(Product.storehub_id == "P1"))
    assert pod.last_prices(session, sup.id)[pid] == ("254.464285", 156)


def test_a_line_that_does_not_match_a_page_row_is_left_alone():
    class P:
        name = "Cola"

    class L:
        id = 1
        product = P()
        price_exact = None
        supplier_price = 10.0

    class PO:
        lines = [L()]
    assert po_prices.apply_po(PO(), [("Something else", "5.5")]) == 0 and PO.lines[0].price_exact is None
    assert po_prices.apply_po(PO(), [("cola ", "1,234.5000")]) == 1 and PO.lines[0].price_exact == "1234.5" and PO.lines[0].supplier_price == 1234.5
