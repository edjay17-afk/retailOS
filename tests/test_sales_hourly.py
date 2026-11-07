"""A single day on the Sales page is charted by hour (one bar for a whole day says very little); longer ranges stay per day."""
from datetime import timedelta

from app.timeutil import today_local
from tests.conftest import tx
from tests.test_services import iso, seeded
from tests.test_web import add_user, client, login  # noqa: F401


def test_one_day_is_shown_by_hour_and_more_days_by_day(client, sync, fake, session):
    seeded(sync, fake, session, days=5, qty=2)
    t = today_local()
    fake.data["transactions"] += [tx(f"HR{i}", "S_A", iso(t, h), [("P1", 1, 20.0)]) for i, h in enumerate([8, 8, 9, 11, 11, 11])]
    sync.sync_transactions(t - timedelta(days=1), t)
    add_user(session, "hourly_admin", "admin"); login(client, "hourly_admin")
    from sqlalchemy import select
    from app.models import Location
    loc = session.scalar(select(Location).where(Location.name.like("%Alpha%")))
    one = client.get(f"/sales?loc={loc.id}&days=1").text
    assert "Net sales by hour" in one and "Net sales per day" not in one
    assert "8am" in one and "11am" in one
    week = client.get(f"/sales?loc={loc.id}&days=7").text
    assert "Net sales per day" in week and "Net sales by hour" not in week


def test_all_stores_combines_every_store_and_the_warehouse_is_not_listed(client, sync, fake, session):
    from sqlalchemy import select
    from app.models import Location, SalesDay
    seeded(sync, fake, session, days=3, qty=2)
    t = today_local()
    add_user(session, "all_admin", "admin"); login(client, "all_admin")
    stores = [l for l in session.scalars(select(Location)) if l.type == "store" and l.monitored]
    assert len(stores) > 1
    expect = sum(r.net_sales for r in session.scalars(select(SalesDay).where(SalesDay.location_id.in_([l.id for l in stores]), SalesDay.business_date == t - timedelta(days=1))))
    page = client.get("/sales?days=7").text
    assert 'value="0" selected' in page and "All stores" in page
    assert "Zenwok" not in page.split('id="sales-location"')[1].split("</select>")[0]
    one = client.get(f"/sales?loc={stores[0].id}&days=7").text
    assert 'value="0" selected' not in one
    csv = client.get("/sales?days=7&export=csv")
    assert csv.status_code == 200 and "All stores" in csv.text
    assert f"{expect:.2f}" in csv.text


def test_data_warning_banner_reads_in_hours_and_is_served_on_its_own(client, session):
    from datetime import timedelta
    from sqlalchemy import select
    from app.models import SyncState
    from app.timeutil import now_utc
    add_user(session, "warn_admin", "admin"); login(client, "warn_admin")
    for domain in ("sales", "inventory", "purchase_orders"):
        st = session.get(SyncState, domain) or SyncState(domain=domain)
        st.last_success_at = now_utc() - timedelta(minutes=870); session.add(st)
    session.commit()
    banner = client.get("/data-warning").text
    assert "14 h 30" in banner and "clears by itself" in banner
    assert 'id="data-warning"' in client.get("/").text
    for domain in ("sales", "inventory", "purchase_orders"):
        session.get(SyncState, domain).last_success_at = now_utc()
    session.commit()
    assert client.get("/data-warning").text.strip() == ""


def test_transactions_can_be_filtered_by_how_they_were_paid(client, sync, fake, session):
    seeded(sync, fake, session, days=3, qty=1)
    t = today_local()
    fake.data["transactions"] += [tx("PAYC1", "S_A", iso(t, 9), [("P1", 1, 20.0)], pay="Cash"), tx("PAYG1", "S_A", iso(t, 10), [("P1", 1, 20.0)], pay="GCASH"),
                                  tx("PAYG2", "S_A", iso(t, 11), [("P1", 1, 20.0)], pay="GCash"), tx("PAYF1", "S_A", iso(t, 12), [("P1", 1, 20.0)], pay="Food Panda")]
    sync.sync_transactions(t - timedelta(days=1), t)
    add_user(session, "pay_admin", "admin"); login(client, "pay_admin")
    page = client.get("/sales/transactions?days=7").text
    assert 'name="pay"' in page and "Food Panda" in page and page.count("GCASH") + page.count("GCash") >= 1
    gcash = client.get("/sales/transactions?days=7&pay=gcash").text            # GCASH and GCash are the same method
    assert "PAYG1" in gcash and "PAYG2" in gcash and "PAYC1" not in gcash and "PAYF1" not in gcash
    other = client.get("/sales/transactions?days=7&pay=!cash").text
    assert all(x in other for x in ("PAYG1", "PAYG2", "PAYF1")) and "PAYC1" not in other
    cash = client.get("/sales/transactions?days=7&pay=cash").text
    assert "PAYC1" in cash and "PAYG1" not in cash
    assert "PAYG1" in client.get("/sales/transactions?days=7&pay=gcash&export=csv").text


def test_transactions_csv_is_one_row_per_invoice_with_every_product_and_quantity(client, sync, fake, session):
    import csv, io
    seeded(sync, fake, session, days=3, qty=1)
    t = today_local()
    fake.data["transactions"] += [tx("ITM1", "S_A", iso(t, 9), [("P1", 2, 40.0), ("P2", 1, 25.0)], pay="Cash")]
    sync.sync_transactions(t - timedelta(days=1), t)
    add_user(session, "items_admin", "admin"); login(client, "items_admin")
    rows = list(csv.DictReader(io.StringIO(client.get("/sales/transactions?days=7&export=csv").text.lstrip("\ufeff"))))
    mine = [r for r in rows if r["Invoice"] == "ITM1"]
    assert len(mine) == 1 and mine[0]["Total"] == "65.00"                          # one invoice, one row
    parts = mine[0]["Products"].split("; ")
    assert len(parts) == 2 and parts[0].endswith(" x2 (40.00)") and parts[1].endswith(" x1 (25.00)")   # product, its quantity, then the total of that product
