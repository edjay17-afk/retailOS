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


def test_the_what_to_do_filter_keeps_only_matching_rows(client, session, sync, fake):
    prod, ids = _setup(client, session, sync, fake, 0)           # nothing in the warehouse: the product is "order"
    assert "Cola 500ml" in client.get("/replenishment/sheet?todo=order").text
    assert "Cola 500ml" not in client.get("/replenishment/sheet?todo=send").text
    assert "Cola 500ml" not in client.get("/replenishment/sheet?todo=both").text
    assert "Cola 500ml" in client.get("/replenishment/sheet?todo=nonsense").text   # an unknown value is no filter


def test_the_supplier_filter_uses_the_products_supplier(client, session, sync, fake):
    prod, ids = _setup(client, session, sync, fake, 0)
    html = client.get(f"/replenishment/sheet?supplier={prod.supplier_id}").text
    assert "Cola 500ml" in html
    other = Supplier(storehub_id="SUP2", name="Other Supplier", source="storehub", active=True, name_source="manual")
    session.add(other); session.commit()
    assert "Cola 500ml" not in client.get(f"/replenishment/sheet?supplier={other.id}").text


def test_a_closed_store_is_left_out_of_auto_replenishment_and_inspections(client, session, sync, fake):
    from datetime import date
    from app.models import Inspection
    from app.services import inspections
    prod, ids = _setup(client, session, sync, fake, 1000)
    stores = session.scalars(select(Location).where(Location.type == "store", Location.source == "storehub", Location.monitored.is_(True))).all()
    assert len(stores) >= 1
    shut = stores[0]
    assert session.scalar(select(Recommendation).where(Recommendation.location_id == shut.id, Recommendation.status == "pending")) is not None
    inspections.generate_tasks(session, date.today()); session.commit()
    assert session.scalar(select(Inspection).where(Inspection.location_id == shut.id)) is not None
    shut.closed = True; session.commit()
    replenishment.run(session, force=True); inspections.generate_tasks(session, date.today()); session.commit()
    assert session.scalar(select(Recommendation).where(Recommendation.location_id == shut.id, Recommendation.status == "pending")) is None   # its waiting suggestions are gone, none new
    assert session.scalar(select(Inspection).where(Inspection.location_id == shut.id)) is None                                              # its waiting inspection tasks are gone, none new
    html = client.get("/replenishment/sheet").text
    assert shut.name.replace("The Daily Centro - ", "") not in html.split('class="rs"')[1].split("</thead>")[0]                        # and its column is not on the sheet


def test_throwing_a_transfer_sheet_away_puts_its_suggestions_back_to_waiting(client, session, sync, fake):
    from app.services import transfer_sheets
    prod, ids = _setup(client, session, sync, fake, 1000)
    client.post("/replenishment/proceed", data={"csrf": csrf_of(client, "/replenishment/sheet"), "ids": ids})
    session.expire_all()
    assert session.scalar(select(Recommendation).where(Recommendation.id == ids[0])).status == "ordered"
    sheet = session.scalars(select(TransferSheet)).first()
    from app.models import User
    transfer_sheets.cancel(session, session.scalar(select(User)), sheet); session.commit()
    rec = session.scalar(select(Recommendation).where(Recommendation.id == ids[0]))
    assert rec.status == "pending" and rec.transfer_sheet_id is None and rec.approved_qty is None


def test_the_warehouse_in_the_store_filter_shows_what_must_be_ordered(client, session, sync, fake):
    prod, ids = _setup(client, session, sync, fake, 0)            # nothing in the warehouse: the product is "order"
    wh = session.scalar(select(Location).where(Location.type == "warehouse"))
    page = client.get("/replenishment/sheet").text
    assert f'<option value="{wh.id}"' in page                      # the warehouse is in the store list
    view = client.get(f"/replenishment/sheet?loc={wh.id}").text
    assert "Cola 500ml" in view and "To order" in view
    row = session.scalar(select(InventoryCurrent).where(InventoryCurrent.location_id == wh.id, InventoryCurrent.product_id == prod.id))
    row.on_hand = 100000; session.commit()                         # the warehouse can send everything: nothing to order
    assert "Cola 500ml" not in client.get(f"/replenishment/sheet?loc={wh.id}").text


def test_the_warehouse_view_hides_the_store_columns(client, session, sync, fake):
    prod, ids = _setup(client, session, sync, fake, 0)
    wh = session.scalar(select(Location).where(Location.type == "warehouse"))
    assert 'class="rs rs-wh-view"' in client.get(f"/replenishment/sheet?loc={wh.id}").text
    assert 'class="rs rs-wh-view"' not in client.get("/replenishment/sheet").text


def test_the_warehouse_view_only_ever_says_order(client, session, sync, fake):
    prod, ids = _setup(client, session, sync, fake, 5)            # the warehouse has 5, the stores need more: "send and order" in the normal view
    wh = session.scalar(select(Location).where(Location.type == "warehouse"))
    normal = client.get("/replenishment/sheet").text
    assert "Send 5, order" in normal or "Send 5," in normal
    view = client.get(f"/replenishment/sheet?loc={wh.id}&todo=order").text
    assert "Cola 500ml" in view and "Send " not in view.split('class="rs rs-wh-view"')[1].split("</table>")[0] and "from supplier" in view
    assert "Cola 500ml" not in client.get(f"/replenishment/sheet?loc={wh.id}&todo=send").text


def test_an_excluded_category_is_left_out_of_auto_replenishment(client, session, sync, fake):
    from app.models import Setting
    prod, ids = _setup(client, session, sync, fake, 1000)
    assert session.scalar(select(Recommendation).where(Recommendation.product_id == prod.id, Recommendation.status == "pending")) is not None
    prod.category = "Food Service (Meals, Drinks & Pastries)"; session.commit()
    replenishment.run(session, force=True); session.commit()
    assert session.scalar(select(Recommendation).where(Recommendation.product_id == prod.id, Recommendation.status == "pending")) is None     # its waiting suggestions are gone, none new
    prod.category = "Drinks"; session.commit()
    replenishment.run(session, force=True); session.commit()
    assert session.scalar(select(Recommendation).where(Recommendation.product_id == prod.id, Recommendation.status == "pending")) is not None   # another category is not affected
