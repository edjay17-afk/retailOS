"""Wastage typed into the Lark Wastage Record table is copied into Retail OS wastage, once, and is never sent back to Lark."""
from datetime import datetime

from sqlalchemy import select

from app.models import LarkLink, Location, Product, WastageEntry
from app.services import wastage_lark
from tests.test_web import add_user, client, login, seeded  # noqa: F401


class FakeLark:
    configured = True

    def __init__(self, records):
        self.records = records

    def _base(self):
        return "/base"

    def tables(self):
        return {"损耗记录 Wastage Record": "tbl1"}

    def field_map(self, tid, wanted):
        return {k: k for k in wanted}

    def call(self, method, path, **kw):
        assert method == "GET"          # reading only
        return {"items": self.records, "has_more": False}


def _ms(y, m, d, h=0):
    return int((datetime(y, m, d, h) - datetime(1970, 1, 1)).total_seconds() * 1000)


def test_lark_rows_become_wastage_once_and_unmatched_ones_are_reported(sync, fake, session):
    seeded(sync, fake, session)
    admin = add_user(session, "waste_admin", "admin")
    store = session.scalar(select(Location).where(Location.type == "store", Location.source == "storehub"))
    prod = session.scalar(select(Product).where(Product.in_api.is_(True), Product.source == "storehub"))
    recs = [
        {"record_id": "rec1", "fields": {"Store 店铺": store.name.replace(" - ", " "), "Product 产品": prod.name, "Quantity 数量": 2, "Reason 原因": "B.O - buslot", "Date Created 创建日期": _ms(2026, 11, 9, 1)}},
        {"record_id": "rec2", "fields": {"Store 店铺": store.name, "Product 产品": "No such product", "Quantity 数量": 1, "Reason 原因": "x", "Date Created 创建日期": _ms(2026, 11, 9, 1)}},
        {"record_id": "rec3", "fields": {"Store 店铺": "Nowhere", "Product 产品": prod.name, "Quantity 数量": 1, "Reason 原因": "x"}},
    ]
    out = wastage_lark.run(session, admin, FakeLark(recs)); session.commit()
    assert out["added"] == 1 and out["already"] == 0 and len(out["skipped"]) == 2 and out["total"] == 3
    e = session.scalar(select(WastageEntry))
    assert e.qty == 2 and e.reason == "B.O - buslot" and e.location_id == store.id and e.product_id == prod.id and str(e.entry_date) == "2026-11-09"
    link = session.scalar(select(LarkLink).where(LarkLink.entity_type == "wastage"))
    assert link.lark_record_id == "rec1" and link.entity_id == str(e.id)          # the mirror sees it as already in Lark
    again = wastage_lark.run(session, admin, FakeLark(recs)); session.commit()
    assert again["added"] == 0 and again["already"] == 1 and len(session.scalars(select(WastageEntry)).all()) == 1


def test_the_import_button_is_only_for_admin_and_management(client, session):
    add_user(session, "waste_staff", "staff"); login(client, "waste_staff")
    assert client.post("/wastage/import-lark", data={"csrf": "x"}).status_code in (403, 303)
    assert "Import from Lark" not in client.get("/wastage").text


def test_wastage_shows_in_stock_changes_as_a_minus(sync, fake, session):
    from app.models import StockMovement
    from app.services import stock_movements
    seeded(sync, fake, session)
    admin = add_user(session, "waste_admin2", "admin")
    store = session.scalar(select(Location).where(Location.type == "store", Location.source == "storehub"))
    prod = session.scalar(select(Product).where(Product.in_api.is_(True), Product.source == "storehub"))
    recs = [{"record_id": "recA", "fields": {"Store 店铺": store.name, "Product 产品": prod.name, "Quantity 数量": 3, "Reason 原因": "expired", "Date Created 创建日期": _ms(2026, 11, 9, 1)}}]
    wastage_lark.run(session, admin, FakeLark(recs)); session.commit()
    row = session.scalar(select(StockMovement).where(StockMovement.kind == stock_movements.WASTE))
    assert row.action == "Wastage" and row.change == -3 and row.location_id == store.id and row.product_id == prod.id and row.qty_after == row.qty_before - 3
    assert stock_movements.from_wastage(session) == 0                     # not added twice
