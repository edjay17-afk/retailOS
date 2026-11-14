"""The desktop stock event pages: the list, the record form and the StoreHub history with its totals."""
from datetime import timedelta

from sqlalchemy import select

from app.models import InventoryChange, Location, Product, StockEvent, User
from app.services import stock_events, stock_movements
from app.timeutil import now_utc, to_local
from tests.test_custom_select import _page
from tests.test_services import seeded
from tests.test_stock_movements import _row
from tests.test_web import add_user, client, csrf_of, login  # noqa: F401


def _world(sync, fake, session):
    seeded(sync, fake, session, days=2, qty=20)
    user = add_user(session, "se_admin", "admin")
    loc = session.scalar(select(Location).where(Location.name.like("%Alpha%")))
    prod = session.scalar(select(Product).where(Product.source == "storehub"))
    return user, loc, prod


def test_list_shows_what_is_waiting_for_storehub_and_groups_by_day(client, sync, fake, session):
    user, loc, prod = _world(sync, fake, session)
    waiting = stock_events.create(session, user, kind="write_off", location_id=loc.id, product_id=prod.id, qty=3, reason="spoiled", note="Leaking cans")
    done = stock_events.create(session, user, kind="unplanned_receipt", location_id=loc.id, product_id=prod.id, qty=24, reason="delivery without PO")
    done.verified_at = now_utc()
    old = stock_events.create(session, user, kind="write_off", location_id=loc.id, product_id=prod.id, qty=1, reason="damaged")
    old.created_at = now_utc() - timedelta(days=3)
    session.commit()
    login(client, "se_admin")
    page = client.get("/stock-events?tab=all").text
    assert 'class="se-card se-waiting ' in page and '<b>2</b><small>1 verified</small>' in page      # two not yet seen in StoreHub, one verified
    assert page.count('class="se-day"') == 2 and 'Leaking cans' in page
    assert '<span class="se-count">3</span>' in page                                                    # the tab counts
    assert 'class="se-status ok"' in page and 'class="se-status wait"' in page


def test_the_record_form_still_saves_with_a_reason_chip(client, sync, fake, session):
    user, loc, prod = _world(sync, fake, session)
    login(client, "se_admin")
    page = client.get("/stock-events/new?kind=unplanned_receipt").text
    assert 'name="reason" value="delivery without PO" checked' in page and 'id="sef-prev-qty"' in page
    r = client.post("/stock-events/new", data={"csrf": csrf_of(client, "/stock-events/new"), "kind": "unplanned_receipt", "location_id": str(loc.id), "search": prod.barcode or prod.name,
                                               "qty": "12", "reason": "transfer in", "note": "from the warehouse"}, follow_redirects=False)
    assert r.status_code == 303
    ev = session.scalar(select(StockEvent).order_by(StockEvent.id.desc()))
    assert ev.qty == 12 and ev.reason == "transfer in" and ev.kind == "unplanned_receipt"


def test_history_totals_cover_the_whole_period_and_each_day(client, sync, fake, session):
    user, loc, prod = _world(sync, fake, session)
    session.add(InventoryChange(location_id=loc.id, product_id=prod.id, old_qty=10, new_qty=8, delta=-2))
    session.commit()
    today = to_local(now_utc()).strftime("%Y-%m-%d")
    yesterday = (to_local(now_utc()) - timedelta(days=1)).strftime("%Y-%m-%d")
    rows = [_row(today + " 10:00", "Ana Perez", "Sale", 10, -3, 7, "s1"), _row(today + " 09:00", "Jun Reyes", "Receiving", 7, 24, 31, "s2"), _row(yesterday + " 21:00", "Ana Perez", "Stock Take", 31, -2, 29, "s3")]
    stock_movements.run(session, fetch=lambda pairs, s, e: {pairs[0]: rows})
    login(client, "se_admin")
    page = client.get("/stock-events?tab=history").text
    assert '<b class="in">+24</b>' in page and '<b class="out">−5</b>' in page              # units in and out over all days
    assert '<i class="in">+24</i><i class="out">−3</i>' in page and '<i class="in">+0</i><i class="out">−2</i>' in page   # and per day
    assert 'class="se-act sale"' in page and 'class="se-act recv"' in page and 'class="se-act take"' in page
    narrowed = client.get("/stock-events?tab=history&action=Sale").text
    assert '<b class="in">+0</b>' in narrowed and '<b class="out">−3</b>' in narrowed


def test_a_phone_keeps_the_compact_lists(client, sync, fake, session):
    from playwright.sync_api import sync_playwright
    user, loc, prod = _world(sync, fake, session)
    stock_events.create(session, user, kind="write_off", location_id=loc.id, product_id=prod.id, qty=3, reason="spoiled")
    session.commit()
    login(client, "se_admin")
    with sync_playwright() as pw:
        browser, page = _page(pw, client, 390, 844, touch=True)
        page.goto("http://qa.test/stock-events?tab=all")
        assert not page.locator(".se-feed").is_visible() and not page.locator(".se-cards").is_visible()
        assert page.locator(".stock-change-table").is_visible() and page.locator(".stock-change-actions").is_visible()
        browser.close()
        browser, page = _page(pw, client, 1440, 900)
        page.goto("http://qa.test/stock-events?tab=all")
        assert page.locator(".se-feed").is_visible() and page.locator(".se-cards").is_visible()
        assert not page.locator(".stock-change-table").is_visible() and not page.locator(".stock-change-tabs").is_visible()
        browser.close()


def test_history_search_filters_while_typing_without_enter_or_reload(client, sync, fake, session):
    from playwright.sync_api import sync_playwright
    user, loc, prod = _world(sync, fake, session)
    for p in session.scalars(select(Product).where(Product.source == 'storehub')):
        session.add(InventoryChange(location_id=loc.id, product_id=p.id, old_qty=10, new_qty=8, delta=-2))
    session.commit()
    today = to_local(now_utc()).strftime("%Y-%m-%d")
    stock_movements.run(session, fetch=lambda pairs, s, e: {pair: [_row(today + " 10:00", "Ana Perez", "Sale", 10, -2, 8, "k%d" % i)] for i, pair in enumerate(pairs)})
    login(client, "se_admin")
    with sync_playwright() as pw:
        browser, page = _page(pw, client, 1440, 900)
        page.goto("http://qa.test/stock-events?tab=history")
        assert page.locator(".se-hist").get_by_text("Cola 500ml").count() and page.locator(".se-hist").get_by_text("Untracked Service").count()
        page.evaluate("window.__same_page = true")
        page.locator("#sh-q-desk").press_sequentially("Untra", delay=40)       # only typing: no Enter, no button
        page.wait_for_function("()=>!document.querySelector('.se-hist').textContent.includes('Cola 500ml')")
        assert page.locator(".se-hist").get_by_text("Untracked Service").count() == 1
        assert page.evaluate("window.__same_page === true")                    # the page did not reload
        assert "q=Untra" in page.url
        page.locator("#sh-q-desk").fill("")
        page.wait_for_function("()=>document.querySelector('.se-hist').textContent.includes('Cola 500ml')")
        browser.close()
