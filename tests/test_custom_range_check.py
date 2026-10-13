import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import Product
from app.web.main import create_app
from tests.test_web import seeded, add_user, login


@pytest.fixture
def client(cfg):
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c


def test_product_detail_custom_range(sync, fake, session, client):
    seeded(sync, fake, session)
    add_user(session, "adm", "admin")
    login(client, "adm")
    pid = session.scalar(select(Product.id))
    r = client.get(f"/sales/product/{pid}?days=custom&frm=2026-09-01&to=2026-09-15")
    assert r.status_code == 200, r.status_code
    assert b"2026-09-01" in r.content and b"2026-09-15" in r.content
    r2 = client.get(f"/sales/product/{pid}?days=custom&frm=2026-09-15&to=2026-09-01")
    assert r2.status_code == 200
    r3 = client.get(f"/sales/product/{pid}?days=banana")
    assert r3.status_code == 200


def test_purchase_orders_hidden_from_store_roles(sync, fake, session, client):
    from app.security import PERMS
    assert PERMS["po.view"] == {"warehouse", "admin"}
    seeded(sync, fake, session)
    add_user(session, "mgr", "store_manager")
    login(client, "mgr")
    assert client.get("/purchase-orders").status_code in (302, 303, 403)
    assert client.get("/purchase-orders/1").status_code in (302, 303, 403, 404)
    add_user(session, "wh", "warehouse")
    login(client, "wh")
    assert client.get("/purchase-orders").status_code == 200
