"""Auto replenishment: what the warehouse has goes on a transfer sheet, what it does not have goes on a purchase order, both when the selection mixes them."""
from sqlalchemy import select

from app.models import InventoryCurrent, Location, PoDraft, Product, Recommendation, Supplier, TransferSheet
from app.services import replenishment
from tests.test_replenishment_sheet import _ready
from tests.test_services import seeded
from tests.test_web import add_user, client, csrf_of, login  # noqa: F401


def _setup(client, session, sync, fake, warehouse_stock):
    _ready(client, session, sync, fake)
    prod = session.scalar(select(Product).where(Product.source == "storehub"))
    sup = session.scalar(select(Supplier).where(Supplier.source == "storehub")) or Supplier(storehub_id="SUP1", name="Test Supplier", source="storehub")
    session.add(sup); session.flush()
    sup.active = True; sup.name_source = "manual"
    prod.supplier_id = sup.id; prod.cost = 5.0
    wh = session.scalar(select(Location).where(Location.type == "warehouse"))
    row = session.scalar(select(InventoryCurrent).where(InventoryCurrent.location_id == wh.id, InventoryCurrent.product_id == prod.id))
    row.on_hand = warehouse_stock
    session.commit()
    ids = [r.id for r in session.scalars(select(Recommendation).where(Recommendation.status == "pending", Recommendation.product_id == prod.id))]
    return prod, ids


def test_stock_in_the_warehouse_makes_only_a_transfer_sheet(client, session, sync, fake):
    prod, ids = _setup(client, session, sync, fake, 1000)
    r = client.post("/replenishment/proceed", data={"csrf": csrf_of(client, "/replenishment/sheet"), "ids": ids})
    assert r.status_code == 303 and r.headers["location"].startswith("/transfer-sheets")
    assert session.scalars(select(TransferSheet)).first() is not None and session.scalars(select(PoDraft)).first() is None


def test_no_stock_makes_only_a_purchase_order(client, session, sync, fake):
    prod, ids = _setup(client, session, sync, fake, 0)
    r = client.post("/replenishment/proceed", data={"csrf": csrf_of(client, "/replenishment/sheet"), "ids": ids})
    assert r.status_code == 303 and r.headers["location"].startswith("/purchase-orders/new")
    d = session.scalars(select(PoDraft)).first()
    assert d is not None and len(d.lines) == 1 and d.lines[0].product_id == prod.id and session.scalars(select(TransferSheet)).first() is None
    need = sum(r.final_qty for r in session.scalars(select(Recommendation).where(Recommendation.id.in_(ids))))
    assert float(d.lines[0].qty) == need


def test_part_stock_makes_both_and_splits_the_quantity(client, session, sync, fake):
    prod, ids = _setup(client, session, sync, fake, 5)
    need = sum(r.final_qty for r in session.scalars(select(Recommendation).where(Recommendation.id.in_(ids))))
    assert need > 5
    r = client.post("/replenishment/proceed", data={"csrf": csrf_of(client, "/replenishment/sheet"), "ids": ids})
    assert r.status_code == 303
    sheets = session.scalars(select(TransferSheet)).all()
    d = session.scalars(select(PoDraft)).first()
    assert sheets and d is not None
    sent = sum(l.qty for s in sheets for l in s.lines)
    assert sent == 5 and float(d.lines[0].qty) == need - 5


def test_a_product_without_a_supplier_cannot_be_ordered(client, session, sync, fake):
    prod, ids = _setup(client, session, sync, fake, 0)
    prod.supplier_id = None; session.commit()
    r = client.post("/replenishment/proceed", data={"csrf": csrf_of(client, "/replenishment/sheet"), "ids": ids})
    assert r.status_code == 303 and "err=" in r.headers["location"]
    assert session.scalars(select(PoDraft)).first() is None and session.scalars(select(TransferSheet)).first() is None
