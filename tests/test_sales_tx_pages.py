"""Transactions and sales-by-employee pages: item-level receipts, employee totals, location scoping."""
import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.security import hash_password
from app.models import User
from app.timeutil import today_local
from tests.conftest import tx
from tests.test_services import iso, seeded

PW = "Passw0rd-long-1"


@pytest.fixture()
def client(cfg):
    from app.web.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c


def add_user(session, name, role, locs=None):
    u = User(username=name, full_name=name, role=role, password_hash=hash_password(PW), location_ids=locs)
    session.add(u)
    session.commit()
    return u


def login(client, name, pw=PW):
    return client.post("/login", data={"username": name, "password": pw})


def _seed_sales(sync, fake, session):
    """Alpha: two sales by E1 (one cancelled) and one by a second employee; Beta: one sale."""
    sync.sync_stores(); sync.sync_employees(); sync.sync_products(); sync.sync_inventory()
    t = today_local()
    fake.data["employees"].append({"id": "E2", "firstName": "Bong", "lastName": "Go", "email": "", "phone": ""})
    sync.sync_employees()
    fake.data["transactions"] = [
        tx("T1", "S_A", iso(t - timedelta(days=1)), [("P1", 2, 40.0), ("P2", 3, 30.0)]),
        tx("T2", "S_A", iso(t), [("P2", 1, 10.0)], cancelled=True),
        tx("T3", "S_A", iso(t), [("P1", 1, 20.0)], employeeId="E2"),
        tx("T4", "S_B", iso(t), [("P2", 5, 50.0)]),
    ]
    sync.sync_transactions(t - timedelta(days=2), t)
    session.expire_all()


def test_transactions_page_lists_sales_with_employee_and_payment(client, session, sync, fake):
    add_user(session, "mgr", "warehouse")
    _seed_sales(sync, fake, session)
    login(client, "mgr")
    r = client.get("/sales/transactions")
    assert r.status_code == 200
    assert "Ana Reyes" in r.text and "Bong Go" in r.text          # both employees named
    assert "T1" in r.text and "Cash" in r.text                    # invoice number and payment method
    assert "Cancelled" in r.text                                  # the cancelled sale is flagged


def test_transaction_detail_shows_every_item_sold(client, session, sync, fake):
    add_user(session, "mgr", "warehouse")
    _seed_sales(sync, fake, session)
    login(client, "mgr")
    r = client.get("/sales/transactions?q=T1")
    m = re.search(r'data-href="(/sales/tx/\d+)"', r.text)
    assert m, "row must link to the receipt"
    d = client.get(m.group(1))
    assert d.status_code == 200
    assert "Cola 500ml" in d.text and "Chips" in d.text           # the two items sold
    assert "70.00" in d.text                                      # 40 + 30 line totals
    assert "Ana Reyes" in d.text


def test_transaction_detail_404_for_unknown_and_scope_blocks_other_stores(client, session, sync, fake):
    add_user(session, "alpha", "warehouse", locs=None)  # locations resolved below
    _seed_sales(sync, fake, session)
    from sqlalchemy import select
    from app.models import Location
    beta = session.scalar(select(Location).where(Location.storehub_id == "S_B"))
    u = session.scalar(select(User).where(User.username == "alpha"))
    u.location_ids = [beta.id]
    session.commit()
    login(client, "alpha")
    assert client.get("/sales/tx/99999").status_code == 404
    r = client.get("/sales/transactions")
    assert "T4" in r.text and "T1" not in re.sub(r'value="[^"]*"', "", r.text)                  # only Beta's sale is visible


def test_sales_by_employee_aggregates_and_excludes_cancelled(client, session, sync, fake):
    add_user(session, "mgr", "warehouse")
    _seed_sales(sync, fake, session)
    login(client, "mgr")
    r = client.get("/sales/employees")
    assert r.status_code == 200
    assert "Ana Reyes" in r.text and "Bong Go" in r.text
    assert "120.00" in r.text                                     # Ana: T1 70 + T4 50 (T2's 10 excluded)
    assert "130.00" not in r.text                                 # cancelled sale is not in the figures
    assert "20.00" in r.text                                      # Bong: 20
    row_link = re.search(r'data-href="(/sales/transactions\?[^"]*emp=[^"]*)"', r.text)
    assert row_link, "each employee row drills into their transactions"


def test_employee_filter_narrows_the_transaction_list(client, session, sync, fake):
    add_user(session, "mgr", "warehouse")
    _seed_sales(sync, fake, session)
    login(client, "mgr")
    r = client.get("/sales/transactions", params={"emp": "E2"})
    assert "T3" in r.text and "T1" not in re.sub(r'value="[^"]*"', "", r.text)


def test_custom_range_and_csv_export(client, session, sync, fake):
    add_user(session, "mgr", "warehouse")
    _seed_sales(sync, fake, session)
    login(client, "mgr")
    t = today_local()
    r = client.get("/sales/transactions", params={"days": "custom", "frm": (t - timedelta(days=1)).isoformat(), "to": t.isoformat()})
    assert r.status_code == 200 and "T1" in r.text
    bad = client.get("/sales/transactions", params={"days": "custom", "frm": "2026-10-10", "to": "2026-10-01"})
    assert bad.status_code == 400                                 # invalid ranges must be rejected
    csvr = client.get("/sales/transactions", params={"export": "csv"})
    assert csvr.status_code == 200 and "Invoice" in csvr.text and "T4" in csvr.text


def test_only_admin_and_warehouse_see_transactions_and_employee_sales(client, session):
    add_user(session, "sm", "store_manager")
    login(client, "sm")
    for path in ("/sales/transactions", "/sales/employees", "/sales/tx/1", "/planogram"):
        assert client.get(path).status_code == 403
    assert client.get("/sales").status_code == 200  # daily sales stay visible to managers


def test_daily_sales_and_employee_exports_follow_the_filter(client, session):
    add_user(session, "wh9", "warehouse")
    login(client, "wh9")
    for url in ("/sales?days=7&export=csv", "/sales/employees?days=custom&frm=2026-10-01&to=2026-10-07&export=csv"):
        r = client.get(url)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    assert "2026-10-01-to-2026-10-07" in r.headers["content-disposition"]
