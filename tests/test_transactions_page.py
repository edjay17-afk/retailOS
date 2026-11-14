"""The Transactions page: sales-family header, compact toolbar, a stat strip that totals everything the
filters match (with previous-period deltas), and the feed grouped by day that still opens the receipt drawer."""
import re
from datetime import timedelta

from app.timeutil import today_local
from tests.conftest import tx
from tests.test_services import iso
from tests.test_custom_select import _page
from tests.test_sales_tx_pages import _seed_sales, add_user, client, login  # noqa: F401


def test_the_strip_totals_everything_the_filters_match(client, session, sync, fake):
    add_user(session, "tx_mgr", "warehouse")
    _seed_sales(sync, fake, session)
    login(client, "tx_mgr")
    page = client.get("/sales/transactions").text
    assert 'class="tx-stat-val">₱140.00</span>' in page                        # 70 + 20 + 50 of live sales
    assert 'class="tx-stat-val">3</span>' in page and '₱46.67' in page         # three sales, average basket 140 / 3
    assert 'Cancelled' in page and '(25.0%)' in page                           # 1 of 4 cancelled
    assert 'Cash share' in page and '>100%</b>' in page                        # every seed payment is cash
    assert page.count('class="tx-dayhead"') == 2 and page.count('<b class="n">₱70.00</b>') == 2
    assert 'is-cancelled' in page                                              # shown, but faded and not counted
    assert re.search(r'class="tx-row[^"]*" href="/sales/tx/\d+" data-href="/sales/tx/\d+" data-invoice="T3"', page)


def test_previous_period_deltas(client, session, sync, fake):
    sync.sync_stores(); sync.sync_employees(); sync.sync_products(); sync.sync_inventory()
    t = today_local()
    fake.data["transactions"] = [
        tx("T1", "S_A", iso(t - timedelta(days=1)), [("P1", 2, 40.0), ("P2", 3, 30.0)]),
        tx("T3", "S_A", iso(t), [("P1", 1, 20.0)]),
        tx("T8", "S_A", iso(t - timedelta(days=16)), [("P1", 1, 60.0)]),       # lands in the previous 14-day window
    ]
    sync.sync_transactions(t - timedelta(days=20), t)
    session.expire_all()
    add_user(session, "tx_prev", "warehouse")
    login(client, "tx_prev")
    page = client.get("/sales/transactions").text
    assert '▲ 50.0%' in page and 'Prev' in page and '₱60.00' in page           # prev net 60 vs 90 now
    assert 'class="delta down"' in page                                        # average basket fell 45 vs 60


def test_the_figures_follow_the_filters_not_just_the_page(client, session, sync, fake):
    add_user(session, "tx_mgr2", "warehouse")
    _seed_sales(sync, fake, session)
    login(client, "tx_mgr2")
    page = client.get("/sales/transactions", params={"emp": "E2"}).text
    assert 'class="tx-stat-val">1</span>' in page and '₱20.00' in page         # only Bong Go's sale
    cash = client.get("/sales/transactions?pay=cash").text
    assert 'class="on"' in cash and 'name="pay" value="cash"' in cash
    assert 'href="?loc=0&amp;days=14&amp;emp=&amp;q=&amp;pay=cash"' in cash


def test_one_feed_for_phone_and_desktop(client, session, sync, fake):
    from playwright.sync_api import sync_playwright
    add_user(session, "tx_mgr3", "warehouse")
    _seed_sales(sync, fake, session)
    login(client, "tx_mgr3")
    with sync_playwright() as pw:
        browser, page = _page(pw, client, 390, 844, touch=True)
        page.goto("http://qa.test/sales/transactions")
        assert page.locator(".tx-board").is_visible() and page.locator(".tx-toolbar").is_visible()
        assert page.locator(".tx-strip").is_visible()                          # compacted, not hidden
        browser.close()
        browser, page = _page(pw, client, 1440, 900)
        page.goto("http://qa.test/sales/transactions")
        assert page.locator(".tx-board").is_visible() and page.locator(".tx-strip").is_visible()
        browser.close()
