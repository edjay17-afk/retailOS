"""Stock side by side: every location in one row per product, with how worrying each number is."""
from datetime import timedelta

from sqlalchemy import select

from app.models import InventoryCurrent, Location, Product
from app.timeutil import today_local
from tests.test_services import seeded
from tests.test_web import add_user, client, csrf_of, login  # noqa: F401


def _page(client, session, sync, fake, qs="/inventory/compare?view=all"):
    seeded(sync, fake, session)
    add_user(session, "boss", "admin")
    login(client, "boss")
    return client.get(qs)


def test_plain_stock_page_compares_the_stores_and_filters_keep_the_list(client, sync, fake, session):
    seeded(sync, fake, session)
    add_user(session, "boss", "admin"); login(client, "boss")
    r = client.get("/inventory")
    assert r.status_code == 303 and r.headers["location"] == "/inventory/compare"
    assert client.get("/inventory?view=list").status_code == 200
    assert "Compare stores" in client.get("/inventory?view=list").text


def test_cells_show_each_location_and_the_right_level(client, sync, fake, session):
    r = _page(client, session, sync, fake)
    html = r.text
    assert r.status_code == 200 and "Cola 500ml" in html and "Chips" in html
    # seeded: Alpha sells Cola (10 on hand, 4 a day = 2.5 days left: low); Chips on hand -3 in Alpha (negative); warehouse has plenty
    assert 'lv-low' in html and 'lv-neg' in html and 'lv-ok' in html
    assert ">-3<" in html.replace("\n", "")


def test_attention_filter_and_csv(client, sync, fake, session):
    _page(client, session, sync, fake)
    only = client.get("/inventory/compare?view=all&attention=1").text
    assert "Cola 500ml" in only and "Chips" in only
    csv = client.get("/inventory/compare?export=csv")
    assert csv.status_code == 200 and "Product,SKU,Barcode,Category" in csv.text and "Cola 500ml" in csv.text
    none = client.get("/inventory/compare?view=all&q=zzzz-no-such-product").text
    assert "No products match." in none


def test_a_store_user_with_one_location_keeps_the_list(client, sync, fake, session):
    seeded(sync, fake, session)
    alpha = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    add_user(session, "onestore", "store_manager", [alpha.id]); login(client, "onestore")
    assert client.get("/inventory").status_code == 200       # one location: no comparison to make


def test_mobile_compare_opens_stock_list_but_desktop_keeps_compare(client, session, sync, fake):
    from urllib.parse import urlsplit
    from playwright.sync_api import sync_playwright
    seeded(sync, fake, session)
    add_user(session, 'stock_view_admin', 'admin'); login(client, 'stock_view_admin')

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
        page = browser.new_page(viewport={'width': 1440, 'height': 844})
        page.route('http://qa.test/**', serve)
        page.route('https://**', lambda route: route.abort())
        page.goto('http://qa.test/inventory/compare?view=all&q=Cola')
        assert '/inventory/compare' in page.url and page.locator('.sc-wrap').is_visible()
        for width in (320, 390, 700):
            page.set_viewport_size({'width': width, 'height': 844})
            page.goto('http://qa.test/inventory/compare?view=all&q=Cola&category=Drinks')
            page.wait_for_url('**/inventory?**')
            assert 'view=list' in page.url and 'q=Cola' in page.url and 'category=Drinks' in page.url
            assert page.locator('.phone-stock-list').is_visible()
        browser.close()


def test_move_view_lists_what_the_warehouse_can_send_and_makes_a_transfer_sheet(client, sync, fake, session):
    from sqlalchemy import select
    from app.models import InventoryCurrent, Location, Product, SalesDayProduct, TransferSheet
    from app.timeutil import today_local
    _page(client, session, sync, fake)
    wh = session.scalar(select(Location).where(Location.type == "warehouse"))
    shop = session.scalar(select(Location).where(Location.type == "store", Location.source == "storehub"))
    prod = session.scalar(select(Product).where(Product.in_api.is_(True), Product.source == "storehub"))
    for loc, qty in ((wh, 500), (shop, -4)):
        row = session.scalar(select(InventoryCurrent).where(InventoryCurrent.location_id == loc.id, InventoryCurrent.product_id == prod.id))
        row.on_hand = qty
    session.commit()
    from app.services import stock_moves
    found = stock_moves.candidates(session, wh.id, shop.id)
    assert [x["p"].id for x in found] == [prod.id] and found[0]["send"] >= 1                  # sold here lately, the warehouse has it, the store is out
    page = client.get(f"/inventory/compare?view=move&store={shop.id}").text
    assert 'id="mv-form"' in page and prod.name in page and f'value="{found[0]["send"]}"' in page
    r = client.post("/inventory/compare/transfer", data={"csrf": csrf_of(client, f"/inventory/compare?view=move&store={shop.id}"), "store_id": shop.id, "ids": [prod.id], f"q_{prod.id}": "7"})
    assert r.status_code == 303 and r.headers["location"].startswith("/transfer-sheets/")
    sheet = session.scalar(select(TransferSheet))
    assert sheet.target_location_id == shop.id and [(l.product_id, l.qty) for l in sheet.lines] == [(prod.id, 7.0)]
    assert client.post("/inventory/compare/transfer", data={"csrf": csrf_of(client, "/inventory/compare"), "store_id": shop.id}).headers["location"].find("err=") > 0   # nothing ticked


def test_triage_cards_chips_and_next_step(client, sync, fake, session):
    _page(client, session, sync, fake)
    page = client.get("/inventory/compare").text
    assert 'class="ta-cards"' in page and 'class="ta-chips"' in page and "Next step" in page
    neg = client.get("/inventory/compare?status=neg").text         # seeded data has a negative cell (-3): only products with one stay
    assert "Chips" in neg and "Cola 500ml" not in neg
    assert "No products match." in client.get("/inventory/compare?status=send&q=zzzz-none").text
    assert client.get("/inventory/compare?status=nonsense").status_code == 200


def test_search_filters_automatically_without_pressing_enter(client, session, sync, fake):
    from urllib.parse import urlsplit
    from playwright.sync_api import sync_playwright
    seeded(sync, fake, session)
    add_user(session, 'live_search_admin', 'admin'); login(client, 'live_search_admin')

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
        page = browser.new_page(viewport={'width': 1440, 'height': 500})
        page.route('http://qa.test/**', serve)
        page.route('https://**', lambda route: route.abort())
        page.goto('http://qa.test/inventory/compare?view=all')
        search = page.locator('.ta-filters input[name=q]')
        search.scroll_into_view_if_needed()
        scroll_before = page.evaluate('window.scrollY')
        assert scroll_before > 0
        search.fill('Cola')
        page.wait_for_url('**/inventory/compare?*q=Cola*', timeout=2000)
        assert page.locator('.sc-p', has_text='Cola 500ml').count() == 1
        assert page.locator('.sc-p', has_text='Chips').count() == 0
        assert page.evaluate('window.scrollY') == scroll_before
        assert page.evaluate('document.activeElement.id') == 'stock-compare-search'
        page.keyboard.type(' 500')
        page.wait_for_url('**/inventory/compare?*q=Cola+500*', timeout=2000)
        assert page.locator('.sc-p', has_text='Cola 500ml').count() == 1
        assert page.locator('.ta-filters .search-clear').count() == 1
        page.locator('.ta-filters .search-clear').click()
        page.wait_for_url('**/inventory/compare?*q=&*', timeout=2000)
        assert page.locator('.sc-p', has_text='Chips').count() == 1
        assert page.evaluate('document.activeElement.id') == 'stock-compare-search'
        browser.close()


def test_native_search_clear_is_hidden_in_favor_of_custom_button(client, session, sync, fake):
    _page(client, session, sync, fake)
    css = client.get('/static/stock_compare_a.css').text
    assert 'input[type=search]::-webkit-search-cancel-button' in css


def test_every_active_product_is_listed_even_without_a_stock_record_and_inactive_ones_are_not(client, sync, fake, session):
    from app.models import Product
    _page(client, session, sync, fake)
    new = Product(storehub_id="NEW_NO_STOCK", name="Brand new product", in_api=True, source="storehub")
    gone = Product(storehub_id="GONE_FROM_SH", name="Archived product", in_api=False, source="storehub")
    session.add_all([new, gone]); session.commit()
    page = client.get("/inventory/compare").text
    assert "Brand new product" in page and "No stock record" in page and "Archived product" not in page


def test_estimated_stock_counts_open_transfers_as_if_completed(client, sync, fake, session):
    from datetime import timedelta
    from sqlalchemy import select
    from app.models import InventoryCurrent, Location, Product, StockTransfer, StockTransferImport, StockTransferLine, User
    from app.services import stock_estimate
    from app.timeutil import now_utc
    _page(client, session, sync, fake)
    wh = session.scalar(select(Location).where(Location.type == "warehouse"))
    shop = session.scalar(select(Location).where(Location.type == "store", Location.source == "storehub"))
    prod = session.scalar(select(Product).where(Product.in_api.is_(True), Product.source == "storehub"))
    admin = session.scalar(select(User))
    imp = StockTransferImport(token="e" * 64, filename="x.csv", sha256="0" * 64, raw_csv=b"", created_by=admin.id); session.add(imp); session.flush()
    for ref, status, qty in (("ST7001", "Created", 10.0), ("ST7002", "Shipped", 4.0), ("ST7003", "Completed", 99.0), ("ST7004", "Cancelled", 50.0)):
        t = StockTransfer(reference=ref, source_name="W", target_name="S", source_location_id=wh.id, target_location_id=shop.id, status=status, created_sh=now_utc(), payload_hash=ref, last_import_id=imp.id)
        t.lines.append(StockTransferLine(line_no=1, product_id=prod.id, product_name=prod.name, quantity=qty)); session.add(t)
    session.commit()
    adj = stock_estimate.adjustments(session)
    assert adj[(shop.id, prod.id)]["in"] == 14.0                        # created + shipped arrive; completed and cancelled do not count
    assert adj[(wh.id, prod.id)]["out"] == 10.0                         # only the one that has not shipped yet still has to leave the sender
    assert stock_estimate.estimate(5.0, adj[(shop.id, prod.id)]) == 19.0
    page = client.get("/inventory/compare?est=1").text
    assert "Estimated = StoreHub stock" in page and "transfers in" in page
    shop.closed = True; session.commit()                               # a closed store gets nothing more: its estimate stays at its own stock
    shut = stock_estimate.adjustments(session)
    assert (shop.id, prod.id) not in shut and stock_estimate.estimate(0.0, shut.get((shop.id, prod.id))) == 0.0
    assert shut[(wh.id, prod.id)]["out"] == 10.0                        # the sender's side is unchanged
    assert "Estimated = StoreHub stock" not in client.get("/inventory/compare").text
    assert client.get(f"/inventory?view=list&loc={shop.id}").status_code == 200
