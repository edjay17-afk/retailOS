"""Sales by product: the filter bar is a big search on top and, underneath, a period switch and filter chips."""
import os
from urllib.parse import urlsplit

from tests.test_web import add_user, client, login, seeded  # noqa: F401


def _serve(client):
    def serve(route):
        u = urlsplit(route.request.url)
        r = client.get(u.path + ("?" + u.query if u.query else ""))
        route.fulfill(status=r.status_code, body=r.content, headers={k: v for k, v in r.headers.items() if k.lower() not in ("content-length", "content-encoding", "transfer-encoding")})
    return serve


def test_the_filter_bar_has_search_first_a_period_switch_and_chips(sync, fake, session, client):
    from playwright.sync_api import sync_playwright
    seeded(sync, fake, session)
    add_user(session, "bar_admin", "admin"); login(client, "bar_admin")
    html = client.get("/sales/products?days=7").text
    assert 'class="psf-seg"' in html and 'name="q"' in html and 'type="submit" form="product-sales-filters"' not in html
    assert html.index('name="q"') < html.index('class="psf-seg"')            # the search comes first
    assert 'href="?loc=0&amp;q=&amp;category=&amp;sort=sales&amp;supplier=0&days=14"' in html  # a period link keeps the other filters
    shots = os.environ.get("BAR_SHOTS")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 700}, device_scale_factor=1.5)
        page.route("http://qa.test/**", _serve(client)); page.route("https://**", lambda r: r.abort())
        for width in (1440, 1000, 390):
            page.set_viewport_size({"width": width, "height": 700})
            page.goto("http://qa.test/sales/products?days=7")
            page.wait_for_selector(".psf-chip")
            assert page.locator(".psf-seg > a.on").inner_text() == "7 days"
            box = page.locator("#product-sales-filters").bounding_box()
            assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1       # nothing runs off the screen
            for chip in page.locator(".psf-chip").all():
                cb = chip.bounding_box()
                assert cb["x"] + cb["width"] <= box["x"] + box["width"] + 1 and cb["x"] >= box["x"] - 1
            if shots and width == 1440:
                page.locator("#product-sales-filters").screenshot(path=os.path.join(shots, "bar_default.png"))
            if shots and width == 390:
                page.locator("#product-sales-filters").screenshot(path=os.path.join(shots, "bar_phone.png"))
        page.set_viewport_size({"width": 1440, "height": 700})
        page.goto("http://qa.test/sales/products?days=14")
        page.wait_for_selector(".psf-chip")
        for name in ("loc", "supplier", "category", "sort"):       # a click anywhere on a chip lands on its dropdown, not only on the word
            cb = page.locator(".psf-chip", has=page.locator(f"select[name={name}]")).bounding_box()
            for dx in (3, 20, cb["width"] / 2, cb["width"] - 6):
                hit = page.evaluate("([x, y]) => { const e = document.elementFromPoint(x, y); return e.tagName + ':' + e.name }", [cb["x"] + dx, cb["y"] + cb["height"] / 2])
                assert hit == f"SELECT:{name}", (name, dx, hit)
        for i in range(4):                                         # and anywhere on a period segment, right to its corners
            bb = page.locator(".psf-seg").locator("a, .drp-btn").nth(i).bounding_box()
            assert bb["height"] >= 37
            hit = page.evaluate("([x, y]) => { const e = document.elementFromPoint(x, y); return e.tagName }", [bb["x"] + 3, bb["y"] + 2])
            assert hit in ("A", "BUTTON"), (i, hit)
        page.goto("http://qa.test/sales/products?days=custom&frm=2026-10-29&to=2026-11-11")
        page.wait_for_selector(".psf-chip")
        assert "29/10" in page.locator(".psf-seg .drp-btn").evaluate("el => getComputedStyle(el, '::after').content")
        if shots:
            page.locator("#product-sales-filters").screenshot(path=os.path.join(shots, "bar_custom.png"))
        store = page.locator("select[name=loc] option:not(:first-child)").first.get_attribute("value")
        page.goto("http://qa.test/sales/products?days=7&loc=" + store)
        page.wait_for_selector(".psf-chip")
        assert page.locator(".psf-filter").first.evaluate("el => getComputedStyle(el).backgroundColor") != page.locator(".psf-sort").evaluate("el => getComputedStyle(el).backgroundColor")  # a picked store tints its chip
        sort_box = page.locator(".psf-sort").bounding_box(); bar = page.locator("#product-sales-filters").bounding_box()
        assert sort_box["x"] + sort_box["width"] > bar["x"] + bar["width"] - 30          # Sort sits at the far right
        if shots:
            page.locator("#product-sales-filters").screenshot(path=os.path.join(shots, "bar_picked.png"))
        browser.close()
