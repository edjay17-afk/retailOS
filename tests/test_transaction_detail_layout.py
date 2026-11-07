"""Transaction layout checks use local fixtures and do not contact the POS."""
from datetime import datetime
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from app.models import Location, Payment, Product, SalesLine, SalesTx
from tests.test_web import add_user, client, login  # noqa: F401


def test_transaction_details_show_synced_amounts_on_desktop_and_mobile(client, session, monkeypatch, tmp_path):
    loc = Location(storehub_id='DETAIL_STORE', name='Test store', type='store', monitored=True)
    product = Product(storehub_id='DETAIL_PRODUCT', name='Test product', sku='SKU-123', barcode='12345')
    session.add_all([loc, product]); session.flush()
    tx = SalesTx(refid='DETAIL-REF', invoice_no='INV-123', location_id=loc.id,
                 tx_type='Sale', tx_time=datetime(2026, 11, 5, 6, 30), business_date=datetime(2026, 11, 5).date(),
                 total=115, subtotal=102.67, tax=12.33, discount=0, raw_hash='detail')
    session.add(tx); session.flush()
    session.add_all([SalesLine(tx_id=tx.id, line_no=1, product_id=product.id, qty=1,
                               unit_price=102.67, subtotal=102.67, tax=12.33, discount=0, total=115),
                     Payment(tx_id=tx.id, method='Cash', amount=115)])
    session.commit()
    add_user(session, 'tx_detail_admin', 'admin'); login(client, 'tx_detail_admin')

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
        for width in (320, 390, 700, 1024, 1440):
            page.set_viewport_size({'width': width, 'height': 900})
            page.goto(f'http://qa.test/sales/tx/{tx.id}')
            assert page.get_by_text('INV-123').is_visible()
            if width <= 700:
                page.locator('.receipt-mobile-line summary').click()
                assert page.locator('.receipt-mobile-line').get_by_text('SKU: SKU-123').is_visible()
                assert page.locator('.receipt-mobile-line').get_by_text('Tax: 12.33').is_visible()
                page.locator('.receipt-mobile-line summary').click()
            else:
                assert page.locator('.receipt-sku').is_visible()
            assert page.locator('.receipt-subtotal').inner_text() == '102.67'
            assert page.locator('.receipt-tax').inner_text() == '12.33'
            assert page.locator('.receipt-line-total').inner_text() == '115.00'
            assert page.locator('.receipt-summary').get_by_text('Cash').is_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert page.locator('.receipt-summary').bounding_box()['y'] > page.locator('.receipt-table').bounding_box()['y']
            if width in (390, 1440):
                page.screenshot(path=str(tmp_path / f'transaction-{width}.png'))
        browser.close()

    # A server that has not restarted yet still supplies the previous three-column
    # query while it reloads the updated template from disk.
    from app.web import routes_stock
    real_select = routes_stock.select

    def previous_select(*columns):
        if len(columns) == 4 and columns[0] is SalesLine:
            return real_select(*columns[:3])
        return real_select(*columns)

    monkeypatch.setattr(routes_stock, 'select', previous_select)
    response = client.get(f'/sales/tx/{tx.id}')
    assert response.status_code == 200
    assert 'Test product' in response.text and '115.00' in response.text
