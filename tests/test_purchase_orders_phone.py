"""Check the purchase-order card layout with isolated test data."""
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from app.models import PurchaseOrder
from app.timeutil import now_utc
from tests.test_count_scanner import scan_setup  # noqa: F401
from tests.test_web import client  # noqa: F401


def test_receive_button_stays_in_purchase_order_card(client, session, scan_setup):
    _, _, _, location_id = scan_setup
    order = PurchaseOrder(sh_po_id=77001, location_id=location_id, status='open',
                          created_sh=now_utc(), total=1234.56)
    session.add(order)
    session.commit()

    def serve(route):
        req = route.request
        url = urlsplit(req.url)
        response = client.request(req.method, url.path + ('?' + url.query if url.query else ''),
                                  content=req.post_data_buffer,
                                  headers={k: v for k, v in req.all_headers().items()
                                           if k in ('content-type', 'x-requested-with')})
        route.fulfill(status=response.status_code, body=response.content,
                      headers={k: v for k, v in response.headers.items()
                               if k.lower() not in ('content-length', 'content-encoding', 'transfer-encoding')})

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        page.route('http://qa.test/**', serve)
        page.route('https://**', lambda route: route.abort())
        page.goto('http://qa.test/purchase-orders')
        desktop = page.screenshot(animations='disabled')
        page.locator('link[href*="purchase_orders_phone.css"]').evaluate('(el) => el.disabled = true')
        assert page.screenshot(animations='disabled') == desktop

        for width in (320, 390, 700):
            page.set_viewport_size({'width': width, 'height': 844})
            page.goto('http://qa.test/purchase-orders')
            card = page.locator('table.mcards tr').filter(has=page.get_by_role('link', name='#77001'))
            button = card.get_by_role('button', name='Receive')
            card_box, button_box = card.bounding_box(), button.bounding_box()
            total_box = card.locator('td:nth-child(9)').bounding_box()
            assert button.is_visible() and button_box['height'] >= 44
            assert button_box['x'] >= total_box['x'] + total_box['width']
            assert button_box['x'] + button_box['width'] <= card_box['x'] + card_box['width']
            assert button_box['y'] >= card_box['y']
            assert button_box['y'] + button_box['height'] <= card_box['y'] + card_box['height']
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        browser.close()
