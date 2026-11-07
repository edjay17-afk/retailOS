"""Stock changes from StoreHub's Audit Trail: read only, saved without duplicates, only for products that changed, and shown on the Stock changes page."""
from datetime import timedelta

from sqlalchemy import select

from app.models import InventoryChange, Location, Product, StockMovement, StockMovementRead
from app.services import stock_movements
from app.timeutil import now_utc
from tests.test_services import seeded
from tests.test_web import add_user, client, login  # noqa: F401


def _row(at, who, action, before, change, after, link="L1"):
    return {"0": at, "1": who, "2": action, "3": str(before), "4": str(change), "5": str(after), "exportTime": at, "exportTimeText": at, "type": action.lower(), "linkId": link, "employeeId": "E1"}


def _setup(sync, fake, session):
    seeded(sync, fake, session, days=2, qty=1)
    loc = session.scalar(select(Location).where(Location.name.like("%Alpha%")))
    prod = session.scalar(select(Product).where(Product.source == "storehub"))
    session.add(InventoryChange(location_id=loc.id, product_id=prod.id, old_qty=10, new_qty=8, delta=-2))
    session.commit()
    return loc, prod


def test_only_changed_products_are_asked_for_and_rows_are_saved_once(sync, fake, session):
    loc, prod = _setup(sync, fake, session)
    asked = []
    rows = [_row("2026-11-04 09:05", "Rejon Jhun Rey", "Sale", 10, -2, 8), _row("2026-11-03 16:16", "Pogoy Anamae", "Receiving", 6, 4, 10)]
    def fetch(pairs, start, end):
        asked.append(pairs)
        return {pairs[0]: rows}
    out = stock_movements.run(session, fetch=fetch)
    assert out["ok"] and out["asked"] == 1 and out["new"] == 2 and asked[0] == [(loc.storehub_id, prod.storehub_id)]
    assert session.scalar(select(StockMovementRead).where(StockMovementRead.product_id == prod.id)) is not None
    again = stock_movements.run(session, fetch=fetch)             # nothing changed since: nobody is asked
    assert again["asked"] == 0 and len(asked) == 1
    session.add(InventoryChange(location_id=loc.id, product_id=prod.id, old_qty=8, new_qty=7, delta=-1, observed_at=now_utc() + timedelta(minutes=1))); session.commit()
    rows.append(_row("2026-11-04 10:00", "Rejon Jhun Rey", "Sale", 8, -1, 7))
    third = stock_movements.run(session, fetch=fetch)
    assert third["asked"] == 1 and third["new"] == 1               # the two known rows are not saved twice
    assert session.scalar(select(__import__("sqlalchemy").func.count()).select_from(StockMovement)) == 3


def test_an_unreadable_product_stays_waiting(sync, fake, session):
    _setup(sync, fake, session)
    out = stock_movements.run(session, fetch=lambda pairs, s, e: {})
    assert out["ok"] and out["asked"] == 0 and out["waiting"] == 1


def test_the_stock_changes_page_lists_them_with_filters_and_csv(client, sync, fake, session):
    loc, prod = _setup(sync, fake, session)
    stock_movements.run(session, fetch=lambda pairs, s, e: {pairs[0]: [_row(now_utc().strftime("%Y-%m-%d %H:%M"), "Rejon Jhun Rey", "Sale", 10, -2, 8)]})
    add_user(session, "hist_admin", "admin"); login(client, "hist_admin")
    page = client.get("/stock-events").text
    assert prod.name in page and "Rejon Jhun Rey" in page and "Sale" in page and "recorded here: who, why" in page
    assert "Rejon" not in client.get("/stock-events?tab=all").text
    assert "Rejon" not in client.get("/stock-events?tab=history&action=Receiving").text
    assert "Rejon" in client.get(f"/stock-events?tab=history&q={prod.name[:4]}").text
    csv = client.get("/stock-events?tab=history&export=csv")
    assert csv.status_code == 200 and "Rejon Jhun Rey" in csv.text and "Qty before" in csv.text


def test_transfers_show_as_stock_changes_straight_away_and_the_audit_trail_row_replaces_them(sync, fake, session):
    from app.models import StockTransfer, StockTransferImport, StockTransferLine
    loc, prod = _setup(sync, fake, session)
    admin = add_user(session, "tr_admin", "admin")
    imp = StockTransferImport(token="t" * 64, filename="x.csv", sha256="0" * 64, raw_csv=b"", created_by=admin.id)
    session.add(imp); session.flush()
    got = now_utc() - timedelta(minutes=3)
    tr = StockTransfer(reference="ST9001", source_name="W", target_name=loc.name, source_location_id=None, target_location_id=loc.id, status="Completed", created_sh=got,
                       shipped_at=got, received_at=got, received_by="Adolfo", payload_hash="h", last_import_id=imp.id)
    tr.lines.append(StockTransferLine(line_no=1, product_id=prod.id, product_name=prod.name, quantity=12.0))
    session.add(tr); session.commit()
    assert stock_movements.from_transfers(session) == 1
    assert stock_movements.from_transfers(session) == 0                      # again: nothing is added twice
    row = session.scalar(select(StockMovement).where(StockMovement.link_id == "ST:ST9001"))
    assert row.action == "Stock Transfer Received" and row.change == 12.0 and row.employee == "Adolfo"
    from app.timeutil import to_local
    at = to_local(got).strftime("%Y-%m-%d %H:%M")
    stock_movements.apply(session, (loc.id, prod.id), [_row(at, "Adolfo", "Stock Transfer Received", 2, 12, 14, link="T9")], now_utc()); session.commit()
    rows = session.scalars(select(StockMovement).where(StockMovement.action == "Stock Transfer Received")).all()
    assert len(rows) == 1 and rows[0].qty_after == 14 and rows[0].kind != stock_movements.DIRECT
