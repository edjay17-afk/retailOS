"""The desktop Transactions page: one big number for the period, a payment switch, and a list grouped by day that still opens the receipt drawer."""
import re

from tests.test_custom_select import _page
from tests.test_sales_tx_pages import _seed_sales, add_user, client, login  # noqa: F401


def test_the_period_shows_net_sales_count_average_and_day_totals(client, session, sync, fake):
    add_user(session, "tx_mgr", "warehouse")
    _seed_sales(sync, fake, session)
    login(client, "tx_mgr")
    page = client.get("/sales/transactions").text
    assert '<p class="tx-big"><span>PHP</span><b>140.00</b></p>' in page          # 70 + 20 + 50; the cancelled 10 is left out
    assert '<b>3</b> transactions' in page and 'PHP 46.67</b>' in page               # three live sales, average basket 140 / 3
    assert page.count('class="tx-day"') == 2 and page.count('<b class="tx-day-n">PHP 70.00</b>') == 2
    assert 'is-cancelled' in page and 'Cancelled' in page                            # shown, but faded and not counted
    assert re.search(r'class="tx-row[^"]*" href="/sales/tx/\d+" data-href="/sales/tx/\d+" data-invoice="T3"', page)


def test_the_figures_follow_the_filters_not_just_the_page(client, session, sync, fake):
    add_user(session, "tx_mgr2", "warehouse")
    _seed_sales(sync, fake, session)
    login(client, "tx_mgr2")
    page = client.get("/sales/transactions", params={"emp": "E2"}).text
    assert '<b>20.00</b></p>' in page and '<b>1</b> transactions' in page
    cash = client.get("/sales/transactions?pay=cash").text
    assert 'class="on"' in cash and 'name="pay" value="cash"' in cash
    assert 'href="?loc=0&amp;days=14&amp;emp=&amp;q=&pay=cash"' in cash


def test_phone_keeps_the_table_and_desktop_the_list(client, session, sync, fake):
    from playwright.sync_api import sync_playwright
    add_user(session, "tx_mgr3", "warehouse")
    _seed_sales(sync, fake, session)
    login(client, "tx_mgr3")
    with sync_playwright() as pw:
        browser, page = _page(pw, client, 390, 844, touch=True)
        page.goto("http://qa.test/sales/transactions")
        assert not page.locator(".tx-feed").is_visible() and not page.locator(".tx-big").is_visible()
        assert page.locator(".tw.tx-phone table").is_visible()
        browser.close()
        browser, page = _page(pw, client, 1440, 900)
        page.goto("http://qa.test/sales/transactions")
        assert page.locator(".tx-feed").is_visible() and page.locator(".tx-big").is_visible()
        assert not page.locator(".tw.tx-phone").is_visible()
        browser.close()
