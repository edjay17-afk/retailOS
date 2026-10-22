"""New-product lists pushed straight into StoreHub (POST /products): create, duplicate recovery, failure, and who may push."""
import json

import httpx
import pytest
from sqlalchemy import select

from app.models import Location, NewProductItem, NewProductList, PoDraft, Product, ProductCostHistory, StoreHubPush, Supplier, TransferSheet, User
from app.services import new_product_lists as npl
from app.services import new_products, po_drafts as pod, product_push
from app.storehub.client import StoreHubClient
from tests.test_web import add_user, client, csrf_of, login, seeded  # noqa: F401 (fixtures)


@pytest.fixture(autouse=True)
def _no_storehub_refresh_or_internet(monkeypatch):
    monkeypatch.setattr(new_products, "refresh_products", lambda: False)
    monkeypatch.setattr(new_products, "web_lookup", lambda db, b, timeout=4.0: None)


class _NoWait:
    def wait(self):
        pass


class FakeCreateStoreHub:
    """POST /products (creates, or fails with the queued statuses) plus the GET /products used for duplicate recovery."""
    def __init__(self, post_results=None, existing=None):
        self.posts: list[dict] = []
        self.post_results = list(post_results or [])
        self.products = list(existing or [])

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/products" and request.method == "POST":
            body = json.loads(request.content)
            self.posts.append(body)
            sc = self.post_results.pop(0) if self.post_results else 200
            if sc == 200:
                created = {"id": f"SH{100 + len(self.products)}", "name": body["name"], "barcode": body.get("barcode"),
                           "category": body.get("category"), "unitPrice": body.get("unitPrice"), "cost": body.get("cost"),
                           "trackStockLevel": body.get("trackStockLevel", True), "isParentProduct": False, "priceType": "Fixed"}
                self.products.append(created)
                return httpx.Response(200, json=created)
            return httpx.Response(sc, text="boom")
        if path == "/products" and request.method == "GET":
            return httpx.Response(200, json=self.products)
        return httpx.Response(404, text="unknown")

    def client(self) -> StoreHubClient:
        c = StoreHubClient(transport=httpx.MockTransport(self.handler), sleep=lambda s: None)
        c.limiter = _NoWait()
        return c


def _setup(sync, fake, session, user="np_wh", role="warehouse"):
    seeded(sync, fake, session)
    session.add(Supplier(name="Acme Foods"))
    session.commit()
    return add_user(session, user, role, locs=[1])


def _item(session, u, price=25.0, cost=15.0, barcode="880011", name="Push Candy 100g"):
    lst = npl.draft(session, u)
    npl.add_item(session, lst, barcode=barcode, name=name, supplier="Acme Foods", category="Snacks", qty=12, price=price, cost=cost)
    npl.save(session, u, lst)
    session.commit()
    return lst, lst.items[0]


def test_push_creates_the_product_and_links_it(cfg, sync, fake, session):
    u = _setup(sync, fake, session)
    lst, item = _item(session, u)
    sh = FakeCreateStoreHub()
    out = product_push.push_list(session, u, lst, client=sh.client())
    assert out == {"created": 1, "failed": 0, "skipped": 0}
    assert len(sh.posts) == 1
    body = sh.posts[0]
    assert body == {"name": "Push Candy 100g", "priceType": "fixed", "unitPrice": 25.0, "trackStockLevel": True,
                    "barcode": "880011", "category": "Snacks", "cost": 15.0}  # the live API wants the lowercase enum
    assert item.status == "created" and item.storehub_product_id == "SH100" and item.push_error is None
    p = session.scalar(select(Product).where(Product.storehub_id == "SH100"))
    assert p is not None and p.name == "Push Candy 100g" and p.unit_price == 25.0 and p.cost == 15.0 and p.in_api and p.track_stock
    assert session.scalar(select(ProductCostHistory).where(ProductCostHistory.product_id == p.id)) is not None
    assert new_products.lookup(session, "880011").id == p.id  # the next scan finds it at once
    reg = session.scalar(select(StoreHubPush))  # the registry says what went in, when and by whom
    assert reg.storehub_product_id == "SH100" and reg.name == "Push Candy 100g" and reg.barcode == "880011"
    assert reg.origin == "list" and reg.list_id == lst.id and reg.pushed_by == u.id and reg.pushed_at is not None
    out = product_push.push_list(session, u, lst, client=sh.client())  # a second run does not create it again
    assert out["skipped"] == 1 and len(sh.posts) == 1
    assert len(list(session.scalars(select(StoreHubPush)))) == 1  # and the registry still has exactly one line for it


def test_duplicate_barcode_recovers_instead_of_duplicating(cfg, sync, fake, session):
    u = _setup(sync, fake, session)
    lst, item = _item(session, u)
    sh = FakeCreateStoreHub(post_results=[400], existing=[{"id": "SHOLD", "name": "Push Candy 100g", "barcode": "880011",
                                                           "unitPrice": 25.0, "trackStockLevel": True}])
    product_push.push_item(session, item, client=sh.client())
    assert item.status == "created" and item.storehub_product_id == "SHOLD"  # matched by barcode after the refused create
    assert len(sh.posts) == 1
    assert session.scalar(select(Product).where(Product.storehub_id == "SHOLD")) is not None


def test_a_storehub_failure_is_kept_on_the_item(cfg, sync, fake, session):
    u = _setup(sync, fake, session)
    lst, item = _item(session, u)
    sh = FakeCreateStoreHub(post_results=[500] * 5)  # every attempt fails; recovery finds nothing
    out = product_push.push_list(session, u, lst, client=sh.client())
    assert out == {"created": 0, "failed": 1, "skipped": 0}
    assert item.status == "failed" and "500" in item.push_error
    assert len(sh.posts) == StoreHubClient.MAX_ATTEMPTS
    sh2 = FakeCreateStoreHub()  # StoreHub is back: the retry creates it
    out = product_push.push_list(session, u, lst, client=sh2.client())
    assert out["created"] == 1 and item.status == "created"


def test_an_item_without_a_price_cannot_be_pushed(cfg, sync, fake, session):
    u = _setup(sync, fake, session)
    _, item = _item(session, u, price=None, cost=None)
    sh = FakeCreateStoreHub()
    try:
        product_push.push_item(session, item, client=sh.client())
        assert False
    except product_push.PushError as e:
        assert "no selling price" in str(e)
    assert not sh.posts  # nothing was ever sent


def test_price_and_cost_are_checked_when_adding(sync, fake, session, client):
    _setup(sync, fake, session)
    login(client, "np_wh")
    csrf = csrf_of(client, "/new-product")
    good = {"csrf": csrf, "barcode": "880011", "name": "A", "supplier": "Acme Foods", "category": "Snacks", "qty": "3"}
    for bad, why in (({"price": "abc"}, "selling price as a number"), ({"price": "-1"}, "cannot be negative"),
                     ({"cost": "-2"}, "cannot be negative"), ({"price": "5", "cost": "9"}, "below the cost")):
        r = client.post("/new-product/add", data={**good, **bad})
        assert r.status_code == 400 and why in r.json()["error"], (bad, r.text)
    r = client.post("/new-product/add", data={**good, "price": "25", "cost": "15"}).json()
    assert r["ok"] and r["lines"][0]["price"] == 25.0 and r["lines"][0]["cost"] == 15.0


def test_save_pushes_straight_away_for_hq_roles(cfg, sync, fake, session, client, monkeypatch):
    _setup(sync, fake, session)
    sh = FakeCreateStoreHub()
    monkeypatch.setattr(product_push, "StoreHubClient", lambda: sh.client())
    login(client, "np_wh")
    csrf = csrf_of(client, "/new-product")
    client.post("/new-product/add", data={"csrf": csrf, "barcode": "880011", "name": "HQ Snack", "supplier": "Acme Foods",
                                          "category": "Snacks", "qty": "12", "price": "25", "cost": "15"})
    r = client.post("/new-product/save", data={"csrf": csrf})
    assert r.status_code == 303 and "/new-product/lists/" in r.headers["location"]  # the save lands on the list with its StoreHub status
    assert len(sh.posts) == 1
    item = session.scalar(select(NewProductItem))
    assert item.status == "created" and item.storehub_product_id == "SH100"
    page = client.get(r.headers["location"]).text
    assert "Created" in page and "₱25" in page


def test_the_page_lists_what_went_to_storehub_when_and_by_whom(cfg, sync, fake, session, client, monkeypatch):
    _setup(sync, fake, session)
    add_user(session, "np_staff2", "staff", locs=[2])
    sh = FakeCreateStoreHub()
    monkeypatch.setattr(product_push, "StoreHubClient", lambda: sh.client())
    login(client, "np_wh")
    csrf = csrf_of(client, "/new-product")
    client.post("/new-product/add", data={"csrf": csrf, "barcode": "880011", "name": "Push Candy 100g", "supplier": "Acme Foods",
                                          "category": "Snacks", "qty": "12", "price": "25", "cost": "15"})
    client.post("/new-product/save", data={"csrf": csrf})
    page = client.get("/new-product").text
    assert "Added to StoreHub" in page and "Push Candy 100g" in page and "NP-00001" in page and "np_wh" in page
    client.cookies.clear()
    login(client, "np_staff2")  # staff see only what they pushed themselves
    page = client.get("/new-product").text
    assert "Added to StoreHub" in page and "Push Candy 100g" not in page


def test_staff_lists_wait_for_a_push_and_staff_cannot_push(cfg, sync, fake, session, client, monkeypatch):
    _setup(sync, fake, session)
    add_user(session, "np_staff", "staff", locs=[2])
    sh = FakeCreateStoreHub()
    monkeypatch.setattr(product_push, "StoreHubClient", lambda: sh.client())
    login(client, "np_staff")
    csrf = csrf_of(client, "/new-product")
    client.post("/new-product/add", data={"csrf": csrf, "barcode": "880011", "name": "Staff Snack", "supplier": "Acme Foods",
                                          "category": "Snacks", "qty": "3", "price": "25"})
    client.post("/new-product/save", data={"csrf": csrf})
    assert not sh.posts  # staff saving never writes to StoreHub
    item = session.scalar(select(NewProductItem))
    assert item.status == "pending"
    lid = session.scalar(select(NewProductList.id))
    r = client.post(f"/new-product/lists/{lid}/push", data={"csrf": csrf})
    assert r.status_code == 403  # staff may not push
    page = client.get(f"/new-product/lists/{lid}").text
    assert "Pending" in page and "Push to StoreHub" not in page
    client.cookies.clear()
    login(client, "np_wh")  # the warehouse picks up the staff list and pushes it
    csrf = csrf_of(client, f"/new-product/lists/{lid}")
    page = client.get(f"/new-product/lists/{lid}").text
    assert "Push to StoreHub" in page
    r = client.post(f"/new-product/lists/{lid}/push", data={"csrf": csrf})
    assert r.status_code == 303 and len(sh.posts) == 1
    session.expire_all()
    assert session.get(NewProductItem, item.id).status == "created"
    assert "cost" not in sh.posts[0]  # no cost typed: the payload simply has none


# ---------------------------------------------------------------- quick create from the purchase-order page
def test_quick_create_makes_the_product_on_the_spot(cfg, sync, fake, session):
    u = _setup(sync, fake, session)
    sh = FakeCreateStoreHub()
    p = product_push.quick_create(session, u, barcode="770001", name="Quick Snack 80g", category="Snacks", price="30", cost="18", client=sh.client())
    assert p.name == "Quick Snack 80g" and p.in_api and p.unit_price == 30.0 and p.cost == 18.0
    assert sh.posts[0] == {"name": "Quick Snack 80g", "priceType": "fixed", "unitPrice": 30.0, "trackStockLevel": True,
                           "barcode": "770001", "category": "Snacks", "cost": 18.0}
    assert new_products.lookup(session, "770001").id == p.id
    reg = session.scalar(select(StoreHubPush))  # a quick create is registered too, without a list
    assert reg.storehub_product_id == "SH100" and reg.origin == "quick" and reg.list_id is None and reg.pushed_by == u.id
    try:  # the same barcode again is refused before StoreHub is even asked
        product_push.quick_create(session, u, barcode="770001", name="Twin", price="5", client=sh.client())
        assert False
    except product_push.PushError as e:
        assert "Already in StoreHub" in str(e)
    assert len(sh.posts) == 1
    for kw, word in (({"price": None}, "selling price"), ({"price": "abc"}, "as numbers"), ({"price": "4", "cost": "9"}, "below the cost"),
                     ({"barcode": ""}, "barcode"), ({"name": " "}, "product name")):
        base = dict(barcode="770002", name="X", price="10")
        base.update(kw)
        try:
            product_push.quick_create(session, u, **base, client=sh.client())
            assert False, kw
        except product_push.PushError as e:
            assert word in str(e), (kw, str(e))


def test_quick_create_storehub_refusal_comes_back_as_the_error(cfg, sync, fake, session):
    u = _setup(sync, fake, session)
    sh = FakeCreateStoreHub(post_results=[409] * 1 + [])  # one refused create, recovery finds nothing
    try:
        product_push.quick_create(session, u, barcode="770003", name="Refused", price="10", client=sh.client())
        assert False
    except product_push.PushError as e:
        assert "409" in str(e)


def _start_order(client, session):
    session.add(Supplier(name="Basic Essential Marketing Inc.", name_source="manual"))
    session.commit()
    login(client, "po_qp")
    csrf = csrf_of(client, "/purchase-orders/new")
    r = client.post("/purchase-orders/new/start", data={"csrf": csrf, "supplier": "basic essential marketing inc."})
    assert r.status_code == 303 and "err=" not in r.headers["location"], r.headers["location"]
    return csrf


def test_quick_product_from_the_order_page_goes_straight_onto_the_order(cfg, sync, fake, session, client, monkeypatch):
    seeded(sync, fake, session)
    add_user(session, "po_qp", "warehouse", locs=[1])
    sh = FakeCreateStoreHub()
    monkeypatch.setattr(product_push, "StoreHubClient", lambda: sh.client())
    csrf = _start_order(client, session)
    r = client.post("/purchase-orders/new/quick-product",
                    data={"csrf": csrf, "barcode": "770004", "name": "PO Quick Candy", "category": "Snacks", "price": "25", "cost": "15"})
    assert r.status_code == 200 and r.json()["ok"], r.text
    pid, name, sku, barcodes, suggested, po = r.json()["product"]
    assert name == "PO Quick Candy" and barcodes == ["770004"] and suggested == "15" and po is None
    # and it orders like any StoreHub product: quantity + price, done
    a = client.post("/purchase-orders/new/add", data={"csrf": csrf, "product_id": pid, "qty": "12", "price": "15"})
    assert a.json()["ok"] and a.json()["lines"][0]["name"] == "PO Quick Candy" and a.json()["lines"][0]["qty"] == "12"
    dup = client.post("/purchase-orders/new/quick-product", data={"csrf": csrf, "barcode": "770004", "name": "Twin", "price": "5"})
    assert dup.status_code == 400 and "Already in StoreHub" in dup.json()["error"]
    assert len(sh.posts) == 1  # the duplicate never reached StoreHub
    reg = session.scalar(select(StoreHubPush))  # and it shows where it was made: on a purchase order
    assert reg.origin == "purchase_order" and reg.list_id is None and reg.name == "PO Quick Candy"


# ---------------------------------------------------------------- new products on a saved order are split between the stores
def _three_stores(session):
    """The fake StoreHub has the warehouse plus Alpha and Beta; add Charlie so there are three stores to split between."""
    session.add(Location(storehub_id="S_C", name="The Daily Centro - Charlie", type="store", monitored=True))
    session.commit()


def _quick_line(client, csrf, barcode, name, qty, cost="15"):
    r = client.post("/purchase-orders/new/quick-product",
                    data={"csrf": csrf, "barcode": barcode, "name": name, "category": "Snacks", "price": "25", "cost": cost})
    assert r.json()["ok"], r.text
    a = client.post("/purchase-orders/new/add", data={"csrf": csrf, "product_id": r.json()["product"][0], "qty": qty, "price": cost})
    assert a.json()["ok"], a.text


def test_a_new_product_is_split_between_the_stores_on_save(cfg, sync, fake, session, client, monkeypatch):
    seeded(sync, fake, session)
    session.add(Supplier(name="Basic Essential Marketing Inc.", name_source="manual"))
    add_user(session, "po_split", "warehouse", locs=[1])
    _three_stores(session)
    sh = FakeCreateStoreHub()
    monkeypatch.setattr(product_push, "StoreHubClient", lambda: sh.client())
    login(client, "po_split")
    csrf = csrf_of(client, "/purchase-orders/new")
    r = client.post("/purchase-orders/new/start", data={"csrf": csrf, "supplier": "basic essential marketing inc."})
    assert r.status_code == 303 and "err=" not in r.headers["location"]
    _quick_line(client, csrf, "770010", "New Candy", "36")
    pid = session.scalar(select(Product.id).where(Product.barcode == "111"))  # a product StoreHub already had: NOT split
    r = client.post("/purchase-orders/new/add", data={"csrf": csrf, "product_id": pid, "qty": "60", "price": "12"})
    assert r.json()["ok"]
    save = client.post("/purchase-orders/new/save", data={"csrf": csrf})
    assert save.status_code == 303 and "err=" not in save.headers["location"], save.headers["location"]
    assert "TS-00001" in save.headers["location"]  # the confirmation names the sheets the new products went to
    session.expire_all()
    ro = session.scalar(select(PoDraft.id))
    sheets = list(session.scalars(select(TransferSheet)))
    assert len(sheets) == 3 and all(s.status == "draft" for s in sheets)
    assert {s.target.name for s in sheets} == {"The Daily Centro - Alpha", "The Daily Centro - Beta", "The Daily Centro - Charlie"}
    for s in sheets:
        assert len(s.lines) == 1 and s.lines[0].qty == 12 and s.lines[0].product.barcode == "770010"  # 36 / 3
        assert s.note == f"From purchase order RO-{ro:05d}"

    # the next order with another new product adds to the same three open sheets instead of starting new ones
    csrf = csrf_of(client, "/purchase-orders/new")
    r = client.post("/purchase-orders/new/start", data={"csrf": csrf, "supplier": "basic essential marketing inc."})
    assert r.status_code == 303
    _quick_line(client, csrf, "770011", "New Gum", "35")
    save = client.post("/purchase-orders/new/save", data={"csrf": csrf})
    assert save.status_code == 303 and "err=" not in save.headers["location"]
    session.expire_all()
    sheets = list(session.scalars(select(TransferSheet).order_by(TransferSheet.id)))
    assert len(sheets) == 3  # still the same three sheets
    by_store = {s.target.name: s for s in sheets}
    alpha, charlie = by_store["The Daily Centro - Alpha"], by_store["The Daily Centro - Charlie"]
    assert len(alpha.lines) == 2
    gum = next(l for l in alpha.lines if l.product.barcode == "770011")
    assert gum.qty == 12  # 35 = 12 + 12 + 11: the extra pieces go to the first stores
    assert next(l for l in charlie.lines if l.product.barcode == "770011").qty == 11


def test_two_new_products_in_one_order_share_the_same_three_sheets(cfg, sync, fake, session, client, monkeypatch):
    """Two products created from the same order are both split on save, and both land on the same three sheets."""
    seeded(sync, fake, session)
    session.add(Supplier(name="Basic Essential Marketing Inc.", name_source="manual"))
    add_user(session, "po_multi", "warehouse", locs=[1])
    _three_stores(session)
    sh = FakeCreateStoreHub()
    monkeypatch.setattr(product_push, "StoreHubClient", lambda: sh.client())
    login(client, "po_multi")
    csrf = csrf_of(client, "/purchase-orders/new")
    r = client.post("/purchase-orders/new/start", data={"csrf": csrf, "supplier": "basic essential marketing inc."})
    assert r.status_code == 303 and "err=" not in r.headers["location"]
    _quick_line(client, csrf, "770014", "New Candy", "36")
    _quick_line(client, csrf, "770015", "New Chips", "20")
    save = client.post("/purchase-orders/new/save", data={"csrf": csrf})
    assert save.status_code == 303 and "err=" not in save.headers["location"], save.headers["location"]
    session.expire_all()
    sheets = list(session.scalars(select(TransferSheet).order_by(TransferSheet.id)))
    assert len(sheets) == 3  # one sheet per store, not one per product
    for s in sheets:
        assert len(s.lines) == 2
        assert next(l for l in s.lines if l.product.barcode == "770014").qty == 12  # 36 / 3
    chips = sorted(next(l for l in s.lines if l.product.barcode == "770015").qty for s in sheets)
    assert chips == [6, 7, 7]  # 20 = 7 + 7 + 6: the extra pieces go to the first stores


def test_a_submitted_sheet_is_followed_by_a_new_one(cfg, sync, fake, session, client, monkeypatch):
    from app.services import transfer_sheets as ts
    seeded(sync, fake, session)
    session.add(Supplier(name="Basic Essential Marketing Inc.", name_source="manual"))
    add_user(session, "po_split2", "warehouse", locs=[1])
    sh = FakeCreateStoreHub()
    monkeypatch.setattr(product_push, "StoreHubClient", lambda: sh.client())
    login(client, "po_split2")
    csrf = csrf_of(client, "/purchase-orders/new")
    client.post("/purchase-orders/new/start", data={"csrf": csrf, "supplier": "basic essential marketing inc."})
    _quick_line(client, csrf, "770012", "New Juice", "10")
    client.post("/purchase-orders/new/save", data={"csrf": csrf})
    session.expire_all()
    alpha_sheet = next(s for s in session.scalars(select(TransferSheet)) if s.target.name == "The Daily Centro - Alpha")
    u = session.scalar(select(User).where(User.username == "po_split2"))
    ts.submit(session, u, alpha_sheet)  # Alpha's sheet is sent; the next split starts a fresh sheet for Alpha
    session.commit()
    csrf = csrf_of(client, "/purchase-orders/new")
    client.post("/purchase-orders/new/start", data={"csrf": csrf, "supplier": "basic essential marketing inc."})
    _quick_line(client, csrf, "770013", "New Water", "10")
    client.post("/purchase-orders/new/save", data={"csrf": csrf})
    session.expire_all()
    sheets = list(session.scalars(select(TransferSheet).order_by(TransferSheet.id)))
    assert len(sheets) == 3 and alpha_sheet.id == sheets[0].id and sheets[0].status == "submitted"
    assert {s.status for s in sheets[1:]} == {"draft"}


def test_deleted_storehub_products_disappear_from_added_list(sync, fake, session, client):
    from app.models import Product, StoreHubPush
    from types import SimpleNamespace
    _setup(sync, fake, session)
    login(client, "np_wh")
    product = Product(storehub_id="removed-product", name="Removed catalog item", barcode="984321", in_api=True)
    push = StoreHubPush(storehub_product_id="removed-product", name="Removed catalog item", barcode="984321")
    session.add_all([product, push])
    session.commit()
    assert "Removed catalog item" in client.get("/new-product").text
    catalog = [{"id": p.storehub_id, "name": p.name} for p in session.scalars(select(Product)) if p.storehub_id != "removed-product"]
    sync._products(session, SimpleNamespace(products=lambda: catalog))
    session.commit()
    assert "Removed catalog item" not in client.get("/new-product").text
    assert session.get(StoreHubPush, push.id) is not None
    catalog.append({"id": "removed-product", "name": "Removed catalog item"})
    sync._products(session, SimpleNamespace(products=lambda: catalog))
    session.commit()
    assert "Removed catalog item" in client.get("/new-product").text
