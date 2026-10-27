"""The simplified phone shell stays compact without changing existing workflows."""
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from tests.test_replenishment_sheets import setup_recs, client
from tests.test_web import add_user, login


def test_mobile_simplification_and_desktop_isolation(sync, fake, session, client):
    setup_recs(session, sync, fake)
    add_user(session, "simple_admin", "admin")
    login(client, "simple_admin")

    def serve(route):
        req = route.request
        url = urlsplit(req.url)
        response = client.request(
            req.method,
            url.path + ("?" + url.query if url.query else ""),
            content=req.post_data_buffer,
            headers={k: v for k, v in req.all_headers().items() if k in ("content-type", "x-requested-with")},
        )
        route.fulfill(
            status=response.status_code,
            body=response.content,
            headers={k: v for k, v in response.headers.items() if k.lower() not in ("content-length", "content-encoding", "transfer-encoding")},
        )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.route("http://qa.test/**", serve)
        page.route("https://**", lambda route: route.abort())

        for width in (320, 390, 700):
            page.set_viewport_size({"width": width, "height": 844})

            page.goto("http://qa.test/sales")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert not page.locator(".sales-head h1").is_visible()
            assert page.evaluate(
                """() => {
                  const grid = document.querySelector('.sales-page>.grid.g4').getBoundingClientRect();
                  const main = document.querySelector('.sales-page>.grid.g4>.card.stat:first-child').getBoundingClientRect();
                  return Math.abs(grid.width - main.width) < 2;
                }"""
            )
            assert page.evaluate(
                """() => [...document.querySelectorAll('.sales-page .stat .value')].every(el =>
                  getComputedStyle(el).whiteSpace === 'nowrap' && el.scrollWidth <= el.clientWidth + 1
                )"""
            )

            page.goto("http://qa.test/inventory?view=list")
            assert page.locator(".phone-stock-row").first.is_visible()
            assert page.locator(".phone-stock-row").first.bounding_box()["y"] < 500
            page.get_by_role("button", name="More filters", exact=True).click()
            assert page.locator("#inventory-category").is_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

            page.goto("http://qa.test/replenishment?view=list")
            assert not page.locator(".rep-filter-panel").evaluate("el => el.open")
            assert page.locator(".repl tbody tr").first.bounding_box()["y"] < 650
            page.locator(".rep-filter-panel>summary").click()
            assert page.locator(".rep-filter-panel").evaluate("el => el.open")
            assert page.locator(".rep-filter-panel select[name=status]").is_visible()

            page.goto("http://qa.test/inspections")
            date_buttons = page.locator(".inspection-date-nav>a.btn")
            assert date_buttons.count() == 2
            boxes = [date_buttons.nth(i).bounding_box() for i in range(2)]
            assert abs(boxes[0]["y"] - boxes[1]["y"]) < 2
            assert all(box["height"] >= 44 for box in boxes)

            page.goto("http://qa.test/assistant")
            assert page.locator("#chat-panel").count() == 1
            assert page.locator("#chat-panel.chat-workspace").is_visible()
            assert not page.locator("#chat-fab").is_visible()
            assert not page.locator("main>.page-head").is_visible()
            chat_box = page.locator("#chat-panel.chat-workspace").bounding_box()
            assert abs(chat_box["x"]) < 1
            widths = page.evaluate(
                """() => ({
                  panel: document.querySelector('#chat-panel.chat-workspace').getBoundingClientRect().width,
                  main: document.querySelector('.main').getBoundingClientRect().width,
                })"""
            )
            assert abs(widths["panel"] - widths["main"]) < 1, widths

            page.goto("http://qa.test/")
            page.locator("#mt-more").click()
            assert not page.locator(".more-all").evaluate("el => el.open")
            assert 0 < page.locator(".more-quick a").count() <= 6
            page.locator(".more-all>summary").click()
            assert page.locator(".more-all").evaluate("el => el.open")
            assert page.locator(".more-all a").first.is_visible()

        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto("http://qa.test/replenishment?view=list")
        assert page.locator(".rep-filter-panel").evaluate("el => el.open")
        assert page.locator(".replenishment-page .page-head h1").is_visible()
        page.goto("http://qa.test/assistant")
        assert page.locator("main>.page-head").is_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors, errors
        browser.close()
