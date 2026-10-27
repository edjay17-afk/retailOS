"""Stock side by side: every location in one row per product, with how worrying each number is."""
from datetime import timedelta

from sqlalchemy import select

from app.models import InventoryCurrent, Location, Product
from app.timeutil import today_local
from tests.test_services import seeded
from tests.test_web import add_user, client, login  # noqa: F401


def _page(client, session, sync, fake, qs="/inventory/compare"):
    seeded(sync, fake, session)
    add_user(session, "boss", "admin")
    login(client, "boss")
    return client.get(qs)


def test_plain_stock_page_compares_the_stores_and_filters_keep_the_list(client, sync, fake, session):
    seeded(sync, fake, session)
    add_user(session, "boss", "admin"); login(client, "boss")
    r = client.get("/inventory")
    assert r.status_code == 303 and r.headers["location"] == "/inventory/compare"
    assert client.get("/inventory?view=list").status_code == 200
    assert "Compare stores" in client.get("/inventory?view=list").text


def test_cells_show_each_location_and_the_right_level(client, sync, fake, session):
    r = _page(client, session, sync, fake)
    html = r.text
    assert r.status_code == 200 and "Cola 500ml" in html and "Chips" in html
    # seeded: Alpha sells Cola (10 on hand, 4 a day = 2.5 days left: low); Chips on hand -3 in Alpha (negative); warehouse has plenty
    assert 'lv-low' in html and 'lv-neg' in html and 'lv-ok' in html
    assert ">-3<" in html.replace("\n", "")


def test_attention_filter_and_csv(client, sync, fake, session):
    _page(client, session, sync, fake)
    only = client.get("/inventory/compare?attention=1").text
    assert "Cola 500ml" in only and "Chips" in only
    csv = client.get("/inventory/compare?export=csv")
    assert csv.status_code == 200 and "Product,SKU,Barcode,Category" in csv.text and "Cola 500ml" in csv.text
    none = client.get("/inventory/compare?q=zzzz-no-such-product").text
    assert "No products match." in none


def test_a_store_user_with_one_location_keeps_the_list(client, sync, fake, session):
    seeded(sync, fake, session)
    alpha = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    add_user(session, "onestore", "store_manager", [alpha.id]); login(client, "onestore")
    assert client.get("/inventory").status_code == 200       # one location: no comparison to make
