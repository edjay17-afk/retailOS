"""Auto replenishment order sheet: one row per product, a column per store, the warehouse's stock, and what is left to order."""
import re

from sqlalchemy import select

from app.models import Recommendation
from app.services import replenishment
from tests.test_services import seeded
from tests.test_web import add_user, client, csrf_of, login  # noqa: F401


def _ready(client, session, sync, fake):
    seeded(sync, fake, session)
    replenishment.run(session); session.commit()
    add_user(session, "boss", "admin")
    login(client, "boss")


def test_plain_page_is_the_sheet_and_filters_keep_the_list(client, sync, fake, session):
    _ready(client, session, sync, fake)
    r = client.get("/replenishment")
    assert r.status_code == 303 and r.headers["location"] == "/replenishment/sheet"
    kept = client.get("/replenishment?ok=Done")
    assert kept.status_code == 303 and kept.headers["location"] == "/replenishment/sheet?ok=Done"
    assert client.get("/replenishment?view=list").status_code == 200
    assert client.get("/replenishment?status=all").status_code == 200


def test_the_sheet_shows_each_store_and_the_warehouse_plan(client, sync, fake, session):
    _ready(client, session, sync, fake)
    html = client.get("/replenishment/sheet").text
    assert 'class="rs"' in html and "Cola 500ml" in html
    assert "Warehouse has" in html and "Stores need" in html and "What to do" in html
    rec = session.scalar(select(Recommendation).where(Recommendation.status == "pending"))
    assert f'id="repl-quantity-{rec.id}"' in html                       # the number can be edited and saves by itself
    assert f'name="ids" value="{rec.id}"' in html                        # and the row can be ticked to proceed


def test_a_changed_number_is_kept_and_shown(client, sync, fake, session):
    _ready(client, session, sync, fake)
    rec = session.scalar(select(Recommendation).where(Recommendation.status == "pending"))
    csrf = csrf_of(client, "/replenishment/sheet")
    r = client.post(f"/replenishment/{rec.id}/decide", data={"action": "save", "qty": "7", "csrf": csrf}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200 and r.json()["quantity"] == 7
    html = client.get("/replenishment/sheet").text
    assert re.search(rf'id="repl-quantity-{rec.id}".*?value="7"', html, re.S)


def test_the_status_tabs_and_empty_search(client, sync, fake, session):
    _ready(client, session, sync, fake)
    assert client.get("/replenishment/sheet?status=needs_review").status_code == 200
    assert "No suggestions here." in client.get("/replenishment/sheet?search=zzzz-nothing").text


def test_why_this_number_stays_on_the_sheet(client, sync, fake, session):
    _ready(client, session, sync, fake)
    html = client.get("/replenishment/sheet").text
    assert 'data-drawer="why-p' in html and 'id="drawer"' in html          # a drawer on the same page, not a link to the old list
    assert "Target stock" in html and "Suggested quantity" in html          # the same explanation as before, per store
    assert 'href="/replenishment?view=list&status=all' not in html
