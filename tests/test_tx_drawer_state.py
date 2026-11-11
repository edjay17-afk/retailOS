"""Exercise request ordering in a real browser without a server or external data."""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright, expect


@pytest.fixture
def drawer_page():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = browser.new_page()
        page.route("http://drawer.test/**", lambda route: route.fulfill(body="Fallback receipt"))
        page.goto("http://drawer.test/list")
        page.set_content('''<table><tr data-href="/sales/tx/1"><td>1</td><td>First</td></tr>
          <tr data-href="/sales/tx/2"><td>2</td><td>Second</td></tr></table>
          <dialog id="drawer" hidden><h2 id="drawer-title"></h2><div id="drawer-body"></div></dialog>
          <div id="drawer-scrim" hidden></div>''')
        page.evaluate('''() => {
          window.requests = [];
          window.fetch = () => new Promise((resolve, reject) => requests.push({resolve, reject}));
          window.finish = (index, fail) => {
            if (fail) requests[index].reject(new Error('offline'));
            else requests[index].resolve(new Response('<div class="txp">Receipt ' + index + '</div>'));
          };
        }''')
        page.add_script_tag(path=str(Path(__file__).resolve().parents[1] / "app/web/static/tx_drawer.js"))
        yield page
        browser.close()


def open_row(page, number):
    # Dispatch allows exercising a second selection while the modal is open.
    page.locator(f'tr[data-href="/sales/tx/{number}"]').dispatch_event("click", {"button": 0})


@pytest.mark.parametrize("fail", [False, True])
def test_superseded_response_cannot_paint_or_navigate(drawer_page, fail):
    page = drawer_page
    open_row(page, 1)
    expect(page.locator("#drawer-body")).to_have_attribute("aria-busy", "true")
    open_row(page, 2)
    page.evaluate("finish(1, false)")
    expect(page.locator("#drawer")).to_have_attribute("data-state", "ready")
    page.evaluate("fail => finish(0, fail)", fail)
    expect(page.locator("#drawer-body")).to_have_text("Receipt 1")
    expect(page.locator("#drawer-body")).to_have_attribute("aria-busy", "false")
    assert page.url == "http://drawer.test/list"


@pytest.mark.parametrize("fail", [False, True])
def test_closed_drawer_ignores_pending_response(drawer_page, fail):
    page = drawer_page
    open_row(page, 1)
    page.evaluate("document.getElementById('drawer').close()")
    expect(page.locator("#drawer")).to_have_attribute("data-state", "idle")
    page.evaluate("fail => finish(0, fail)", fail)
    expect(page.locator("#drawer .txp")).to_have_count(0)
    assert page.url == "http://drawer.test/list"


def test_current_failure_keeps_full_page_fallback(drawer_page):
    page = drawer_page
    open_row(page, 1)
    page.evaluate("finish(0, true)")
    page.wait_for_url("http://drawer.test/sales/tx/1")
    expect(page.locator("body")).to_have_text("Fallback receipt")
