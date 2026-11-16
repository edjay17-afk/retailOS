"""The product image robot: real barcodes only, a local copy of the picture, nothing retried too soon."""
import io

import httpx
from PIL import Image

from app.models import Product
from app.services import product_images
from app.timeutil import now_utc


def _jpeg(size=(300, 300)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, "JPEG")
    return buf.getvalue()


def _product(session, pid, barcode, **kw):
    p = Product(storehub_id=f"sh{pid}", name=f"Item {pid}", barcode=barcode, in_api=True, **kw)
    session.add(p)
    session.commit()
    return p


def _client(known: dict[str, str], seen: list | None = None):
    """A fake public database: `known` maps barcode -> picture path; anything else is 'not found'."""
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        if request.url.path.startswith("/api/v2/product/"):
            code = request.url.path.rsplit("/", 1)[1].removesuffix(".json")
            if request.url.host == "world.openfoodfacts.org" and code in known:
                return httpx.Response(200, json={"status": 1, "product": {"image_front_url": "https://images.example/" + known[code]}})
            return httpx.Response(404, json={"status": 0})
        return httpx.Response(200, content=_jpeg(), headers={"content-type": "image/jpeg"})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_gtin_check_digit():
    assert product_images.valid_gtin("9312013047608")
    assert not product_images.valid_gtin("9312013047609")
    assert not product_images.valid_gtin("1340")
    assert product_images.gtins("100026161784, 9312013047608 ,9312013047608") == ["9312013047608"]


def test_finds_and_stores_a_picture(session, cfg):
    p = _product(session, 1, "9312013047608")
    out = product_images.run(session, client=_client({"9312013047608": "a.jpg"}), pause=0)
    session.refresh(p)
    assert out["found"] == 1
    assert p.image_url.startswith("/uploads/products/1.jpg") and p.image_source == "world.openfoodfacts.org"
    stored = Image.open(cfg.upload_dir / "products" / "1.jpg")
    assert max(stored.size) <= product_images.SIZE


def test_internal_codes_make_no_network_call_and_unknown_barcodes_are_not_retried_soon(session):
    internal = _product(session, 2, "100098209736")   # shop-internal code (check digit does not match)
    unknown = _product(session, 3, "6977318633319")
    seen: list = []
    out = product_images.run(session, client=_client({}, seen), pause=0)
    assert out["skipped_no_gtin"] == 1 and out["found"] == 0
    assert all("100098209736" not in u for u in seen)
    session.refresh(internal), session.refresh(unknown)
    assert internal.image_checked_at and unknown.image_checked_at and not unknown.image_url
    seen.clear()
    product_images.run(session, client=_client({}, seen), pause=0)
    assert seen == []   # looked up recently: left alone


def test_an_unreachable_database_does_not_mark_the_product_as_looked_up(session):
    p = _product(session, 4, "9312013047608")
    def down(request):
        raise httpx.ConnectError("offline")
    out = product_images.run(session, client=httpx.Client(transport=httpx.MockTransport(down)), pause=0)
    session.refresh(p)
    assert out["unavailable"] and p.image_checked_at is None


def test_products_that_already_have_a_picture_or_come_from_loyverse_are_left_alone(session):
    _product(session, 5, "9312013047608", image_url="/uploads/products/5.jpg")
    _product(session, 6, "9312013047608", source="loyverse", source_account="x", external_id="e6")
    seen: list = []
    product_images.run(session, client=_client({"9312013047608": "a.jpg"}, seen), pause=0)
    assert seen == []
    assert now_utc()


# ---------------------------------------------------------------- pasted / uploaded pictures
from tests.test_web import add_user, client, csrf_of, login  # noqa: E402,F401


def _png(size=(50, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, (0, 120, 200, 255)).save(buf, "PNG")
    return buf.getvalue()


def test_a_pasted_picture_is_saved_shown_and_can_be_removed(client, session, cfg):
    add_user(session, "boss", "admin")
    p = _product(session, 10, "100098209736", track_stock=True)
    login(client, "boss")
    csrf = csrf_of(client, "/products")
    r = client.post(f"/products/{p.id}/image", data={"csrf": csrf}, files={"file": ("image.png", _png(), "image/png")})
    assert r.status_code == 200 and r.json()["url"].startswith(f"/uploads/products/{p.id}.jpg")
    session.refresh(p)
    assert p.image_source == "manual" and (cfg.upload_dir / "products" / f"{p.id}.jpg").is_file()
    page = client.get("/products").text
    assert f'data-pid="{p.id}"' in page and f"/uploads/products/{p.id}.jpg" in page
    assert client.get(p.image_url).status_code == 200
    # the robot never replaces a picture someone put there
    seen: list = []
    product_images.run(session, client=_client({}, seen), pause=0)
    assert seen == []
    r = client.post(f"/products/{p.id}/image/remove", data={"csrf": csrf})
    assert r.status_code == 200
    session.refresh(p)
    assert p.image_url is None and not (cfg.upload_dir / "products" / f"{p.id}.jpg").exists()


def test_a_non_picture_or_a_missing_token_is_refused(client, session):
    add_user(session, "boss", "admin")
    p = _product(session, 11, None, track_stock=True)
    login(client, "boss")
    csrf = csrf_of(client, "/products")
    assert client.post(f"/products/{p.id}/image", data={"csrf": csrf}, files={"file": ("x.png", b"not a picture", "image/png")}).status_code == 400
    assert client.post(f"/products/{p.id}/image", data={"csrf": "wrong"}, files={"file": ("x.png", _png(), "image/png")}).status_code in (400, 403)


def test_uploaded_picture_appears_across_sales_inventory_and_ops_pages(client, session, cfg):
    from datetime import timedelta
    from sqlalchemy import select
    from app.models import (
        InventoryCurrent, InventoryEvent, Location, SalesDay, SalesDayProduct, SalesLine,
        SalesTx, StockTransfer, StockTransferImport, StockTransferLine, User, WastageEntry,
    )
    from app.timeutil import today_local

    add_user(session, "img_viewer", "admin")
    user = session.scalar(select(User).where(User.username == "img_viewer"))
    store = Location(storehub_id="LOC1", name="The Daily Centro - Test", type="store", monitored=True)
    wh = Location(storehub_id="WH1", name="Warehouse", type="warehouse", monitored=True)
    session.add_all([store, wh])
    session.flush()
    p = _product(session, 21, "4800016068010", track_stock=True, cost=10.0, unit_price=18.0)

    today = today_local()
    session.add(InventoryCurrent(location_id=store.id, product_id=p.id, on_hand=5, warning_stock=10))
    session.add(SalesDay(location_id=store.id, business_date=today, net_sales=54.0, gross_sales=54.0, tx_count=1, cash_sales=54.0, other_sales=0.0))
    session.add(SalesDayProduct(location_id=store.id, product_id=p.id, business_date=today, units=3, net_sales=54.0))
    tx = SalesTx(refid="REF-IMG-1", invoice_no="INV-IMG-1", location_id=store.id, tx_type="Sale", tx_time=now_utc(), business_date=today, total=54.0, subtotal=48.21, tax=5.79, cancelled=False, raw_hash="rh-img-1")
    session.add(tx)
    session.flush()
    session.add(SalesLine(tx_id=tx.id, line_no=1, product_id=p.id, product_sh_id=p.storehub_id, qty=3, unit_price=18.0, total=54.0, subtotal=48.21, tax=5.79))
    session.add(WastageEntry(location_id=store.id, product_id=p.id, entry_date=today, qty=1, reason="Damaged", created_by=user.id))
    session.add(InventoryEvent(event_uid="ev-img-1", event_type="write_off", product_id=p.id, location_id=store.id, quantity=1, direction="-", reconciliation_status="pending"))
    imp = StockTransferImport(token="tok-img-1", filename="t.csv", sha256="abc123", raw_csv=b"", created_by=user.id)
    session.add(imp)
    session.flush()
    st = StockTransfer(reference="ST-IMG-1", source_name="Warehouse", target_name=store.name, source_location_id=wh.id, target_location_id=store.id, status="Created", created_sh=now_utc(), payload_hash="ph-img-1", last_import_id=imp.id)
    session.add(st)
    session.flush()
    session.add(StockTransferLine(transfer_id=st.id, line_no=1, product_id=p.id, product_name=p.name, sku="SKU21", quantity=2))
    session.commit()

    login(client, "img_viewer")
    csrf = csrf_of(client, "/products")
    up = client.post(f"/products/{p.id}/image", data={"csrf": csrf}, files={"file": ("item.png", _png(), "image/png")})
    assert up.status_code == 200
    session.refresh(p)
    img_path = f"/uploads/products/{p.id}.jpg"

    assert img_path in client.get("/products/images.json").json()[str(p.id)]
    assert client.get("/wastage/products?q=Item").json()[0]["image_url"] == p.image_url

    for url in (
        f"/inventory?loc={store.id}",
        "/inventory/compare",
        f"/sales?loc={store.id}&days=7",
        f"/sales/day-panel?day={today.isoformat()}&loc={store.id}",
        f"/sales/products?loc={store.id}&days=7",
        f"/sales/product/{p.id}?loc={store.id}",
        f"/sales/tx/{tx.id}",
        f"/sales/tx/{tx.id}/panel",
        "/wastage",
        "/traceability",
        f"/stock-transfers/{st.id}",
        f"/stock-events/new?kind=write_off&location_id={store.id}&product_id={p.id}",
        "/",
    ):
        resp = client.get(url)
        assert resp.status_code == 200, url
        assert img_path in resp.text, f"Missing image on {url}"

    prod_detail = client.get(f"/sales/product/{p.id}?loc={store.id}").text
    assert 'hidden onload="this.hidden=false"' not in prod_detail


def test_product_settings_can_be_filtered_by_category(client, session):
    add_user(session, "boss", "admin")
    _product(session, 20, None, track_stock=True, category="Snacks")
    _product(session, 21, None, track_stock=True, category="Drinks")
    _product(session, 22, None, track_stock=True)
    login(client, "boss")
    page = client.get("/products").text
    assert "Item 20" in page and "Item 21" in page and 'name="cat"' in page and '<option value="Snacks"' in page
    snacks = client.get("/products?cat=Snacks").text
    assert "Item 20" in snacks and "Item 21" not in snacks and "Item 22" not in snacks
    none = client.get("/products?cat=__none__").text
    assert "Item 22" in none and "Item 20" not in none
