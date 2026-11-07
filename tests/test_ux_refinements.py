"""UX uses the existing data and local workflows; all fixtures are isolated."""
from datetime import timedelta
from sqlalchemy import select

from app import settings_service
from app.models import Location, PurchaseOrder, Receiving, SyncState
from app.services import replenishment
from app.timeutil import now_utc
from tests.test_services import seeded
from tests.test_web import add_user, client, login  # noqa: F401


def test_data_status_and_delivery_progress(client, session, sync, fake):
    seeded(sync, fake, session)
    add_user(session, 'ux_admin', 'admin'); login(client, 'ux_admin')
    sales = session.get(SyncState, 'sales')
    sales.last_success_at = now_utc() - timedelta(days=1)
    session.commit()
    html = client.get('/').text
    assert 'Your work today' in html and 'id="data-health"' not in html
    assert html.index('class="b-stores"') < html.index('class="desktop-work-queue"')
    assert 'id="data-health"' not in client.get('/sales').text
    assert 'id="data-health"' not in client.get('/counts').text

    loc = session.scalar(select(Location).where(Location.storehub_id == 'S_A'))
    po = PurchaseOrder(sh_po_id=77001, location_id=loc.id, status='open')
    session.add(po); session.commit()
    html = client.get(f'/purchase-orders/{po.id}').text
    assert 'No local count yet' in html
    session.add(Receiving(po_id=po.id, location_id=po.location_id, status='open'))
    session.commit()
    html = client.get(f'/purchase-orders/{po.id}').text
    assert 'Counted locally' in html and 'Open count' in html
    assert 'A local count does not receive the PO in StoreHub.' in html


def test_sales_only_explanation_uses_the_actual_rule(client, session, sync, fake):
    seeded(sync, fake, session)
    settings_service.set_(session, 'repl.mode', 'sales_only')
    replenishment.run(session); session.commit()
    add_user(session, 'ux_replenishment_admin', 'admin'); login(client, 'ux_replenishment_admin')
    response = client.get('/replenishment/sheet')
    assert response.status_code == 200
    assert 'Raw suggestion (sales target minus incoming)' in response.text
    assert 'Lead time + review period' not in response.text


def test_mobile_replenishment_reviews_one_product_at_a_time(client, session, sync, fake):
    from urllib.parse import urlsplit
    from playwright.sync_api import sync_playwright
    seeded(sync, fake, session)
    replenishment.run(session); session.commit()
    add_user(session, 'ux_phone_admin', 'admin'); login(client, 'ux_phone_admin')

    def serve(route):
        request = route.request
        url = urlsplit(request.url)
        response = client.request(request.method, url.path + ('?' + url.query if url.query else ''),
                                  content=request.post_data_buffer,
                                  headers={k: v for k, v in request.all_headers().items()
                                           if k in ('content-type', 'x-requested-with')})
        route.fulfill(status=response.status_code, body=response.content,
                      headers={k: v for k, v in response.headers.items()
                               if k.lower() not in ('content-length', 'content-encoding', 'transfer-encoding')})

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        page.route('http://qa.test/**', serve)
        page.route('https://**', lambda r: r.abort())
        page.goto('http://qa.test/replenishment/sheet')
        assert page.locator('#data-health').count() == 0
        rows = page.locator('.rs-row')
        assert rows.count() >= 1
        for width in (320, 390, 700):
            page.set_viewport_size({'width': width, 'height': 844})
            card = rows.nth(0).bounding_box()
            control = rows.nth(0).locator('.rs-phone-open').bounding_box()
            assert control['x'] + control['width'] <= card['x'] + card['width'] - 5
            assert control['y'] - card['y'] < 20 and control['height'] >= 44
            tabs = page.locator('.rs-seg a')
            assert tabs.count() == 4
            assert all(tab.is_visible() for tab in tabs.all())
            tab_boxes = [tab.bounding_box() for tab in tabs.all()]
            assert all(box['x'] >= 0 and box['x'] + box['width'] <= width for box in tab_boxes)
            assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth')
        assert rows.nth(0).locator('.rs-why').is_visible()
        rows.nth(0).locator('.rs-phone-open').click()
        assert not rows.nth(0).locator('.rs-why').is_visible()
        rows.nth(0).locator('.rs-phone-open').click()
        assert rows.nth(0).locator('.rs-why').is_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.set_viewport_size({'width': 1440, 'height': 900})
        assert rows.nth(0).locator('.rs-why').is_visible()
        browser.close()
