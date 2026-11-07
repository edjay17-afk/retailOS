"""Editing Loyverse stock on the Inventory page: permission, the live re-check, the read-back and the audit trail. A pretend Loyverse is used; nothing real is touched."""
import pytest
from sqlalchemy import select
from app.loyverse import stock_edit
from app.models import AuditEvent, InventoryChange, InventoryCurrent, Location, Product
from tests.test_web import client, add_user, login, csrf_of


class FakeLoyverse:
    """Holds the stock Loyverse would show, keyed by variant id."""
    def __init__(self): self.levels = {}; self.writes = []; self.refuse = False

    def close(self): pass


@pytest.fixture
def fake(monkeypatch):
    f = FakeLoyverse()
    monkeypatch.setattr(stock_edit, "client_for", lambda cfg, location: f)
    monkeypatch.setattr(stock_edit, "read_levels", lambda cl, store, variants: {v: cl.levels[v] for v in variants if v in cl.levels})
    def write(cl, store, levels):
        if cl.refuse: raise stock_edit.LoyverseError("Loyverse did not allow this token to change stock", 403, "inventory")
        cl.writes.append((store, list(levels)))
        for v, q in levels: cl.levels[v] = q
    monkeypatch.setattr(stock_edit, "write_levels", write)
    return f


@pytest.fixture
def shop(session, fake):
    loc = Location(storehub_id="lv-s", name="Loyverse shop", type="store", source="loyverse", source_account="A", external_id="store-1", monitored=True)
    session.add(loc); session.flush()
    p1 = Product(storehub_id="lv-p1", name="Lean Meat", source="loyverse", source_account="A", external_id="var-1")
    p2 = Product(storehub_id="lv-p2", name="Siomai", source="loyverse", source_account="A", external_id="var-2")
    session.add_all([p1, p2]); session.flush()
    session.add_all([InventoryCurrent(location_id=loc.id, product_id=p1.id, on_hand=95), InventoryCurrent(location_id=loc.id, product_id=p2.id, on_hand=50)])
    session.commit()
    fake.levels = {"var-1": 95.0, "var-2": 50.0}
    return loc, p1, p2


def _login(client, session, name, role):
    add_user(session, name, role); login(client, name); client.cookies.set("pos_scope", "loyverse:A")


def test_only_admin_and_management_can_edit(client, session, shop, fake):
    loc, p1, _p2 = shop
    for role in ("staff", "store_manager", "warehouse"):
        _login(client, session, "u_" + role, role); token = csrf_of(client, "/")
        r = client.post("/inventory/loyverse/apply", data={"csrf": token, "loc": loc.id, f"old_{p1.id}": "95", f"new_{p1.id}": "96"})
        assert r.status_code == 403
        assert f'name="new_{p1.id}"' not in client.get(f"/inventory?loc={loc.id}").text
    assert fake.writes == []
    _login(client, session, "boss", "management")
    assert f'name="new_{p1.id}"' in client.get(f"/inventory?loc={loc.id}").text


def test_mobile_edit_mirrors_existing_form_and_saves_only_when_requested(client, session, shop, fake):
    from urllib.parse import urlsplit
    from playwright.sync_api import sync_playwright
    loc, p1, _ = shop
    _login(client, session, 'mobile_stock_admin', 'admin')

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
        page.route('https://**', lambda route: route.abort())
        page.goto(f'http://qa.test/inventory?loc={loc.id}')
        mobile = page.locator(f'.lv-mobile-qty[data-product="{p1.id}"]')
        assert mobile.is_visible() and mobile.input_value() == '95'
        mobile.fill('96')
        assert page.locator(f'input.lv-qty[name="new_{p1.id}"]').input_value() == '96'
        assert page.locator('#lv-bar').is_visible() and fake.writes == []
        page.locator('#lv-discard').click()
        assert mobile.input_value() == '95' and fake.writes == []
        mobile.fill('97')
        page.locator('#lv-bar button[type=submit]').click()
        page.wait_for_load_state('domcontentloaded')
        assert fake.levels['var-1'] == 97 and len(fake.writes) == 1
        browser.close()


def test_apply_changes_only_what_was_edited_and_goes_back_to_the_list(client, session, shop, fake):
    loc, p1, p2 = shop
    _login(client, session, "boss", "admin"); token = csrf_of(client, "/")
    r = client.post("/inventory/loyverse/apply", data={"csrf": token, "loc": loc.id, "back": f"/inventory?loc={loc.id}&q=meat",
                    f"old_{p1.id}": "95", f"new_{p1.id}": "96", f"old_{p2.id}": "50", f"new_{p2.id}": "50", "reason": "stock count"}, follow_redirects=False)
    assert r.status_code in (302, 303) and r.headers["location"].startswith(f"/inventory?loc={loc.id}&q=meat") and "ok=" in r.headers["location"]
    assert fake.writes == [("store-1", [("var-1", 96.0)])] and fake.levels == {"var-1": 96.0, "var-2": 50.0}
    session.expire_all()
    assert session.get(InventoryCurrent, (loc.id, p1.id)).on_hand == 96 and session.get(InventoryCurrent, (loc.id, p2.id)).on_hand == 50
    assert session.scalar(select(InventoryChange).where(InventoryChange.product_id == p1.id)).delta == 1
    ev = session.scalar(select(AuditEvent).where(AuditEvent.action == "loyverse_stock_edit"))
    assert ev is not None and ev.reason == "stock count" and ev.before == {"on_hand": 95.0} and ev.after == {"on_hand": 96.0}


def test_bad_numbers_and_empty_saves_are_refused(client, session, shop, fake):
    loc, p1, _p2 = shop
    _login(client, session, "boss", "admin"); token = csrf_of(client, "/")
    for new in ("-3", "abc", "1.2345", ""):
        r = client.post("/inventory/loyverse/apply", data={"csrf": token, "loc": loc.id, f"old_{p1.id}": "95", f"new_{p1.id}": new}, follow_redirects=False)
        assert "err=" in r.headers["location"]
    r = client.post("/inventory/loyverse/apply", data={"csrf": token, "loc": loc.id, f"old_{p1.id}": "95", f"new_{p1.id}": "95"}, follow_redirects=False)
    assert "err=" in r.headers["location"] and fake.writes == []


def test_a_changed_live_number_is_not_overwritten(client, session, shop, fake):
    loc, p1, _p2 = shop
    fake.levels["var-1"] = 90.0      # someone sold or counted since the page was opened
    _login(client, session, "boss", "admin"); token = csrf_of(client, "/")
    r = client.post("/inventory/loyverse/apply", data={"csrf": token, "loc": loc.id, f"old_{p1.id}": "95", f"new_{p1.id}": "96"}, follow_redirects=False)
    assert "err=" in r.headers["location"] and "now+90" in r.headers["location"].replace("%20", "+")
    assert fake.writes == [] and fake.levels["var-1"] == 90.0
    session.expire_all()
    assert session.get(InventoryCurrent, (loc.id, p1.id)).on_hand == 95


def test_a_refused_token_changes_nothing(client, session, shop, fake):
    loc, p1, _p2 = shop
    fake.refuse = True
    _login(client, session, "boss", "admin"); token = csrf_of(client, "/")
    r = client.post("/inventory/loyverse/apply", data={"csrf": token, "loc": loc.id, f"old_{p1.id}": "95", f"new_{p1.id}": "96"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    session.expire_all()
    assert session.get(InventoryCurrent, (loc.id, p1.id)).on_hand == 95
    assert session.scalar(select(AuditEvent).where(AuditEvent.action == "loyverse_stock_edit")) is None


def test_storehub_stores_cannot_be_edited_and_have_no_inputs(client, session, shop):
    sh = Location(storehub_id="sh-1", name="StoreHub shop", type="store", source="storehub", monitored=True); session.add(sh); session.commit()
    _login(client, session, "boss", "admin"); token = csrf_of(client, "/")
    r = client.post("/inventory/loyverse/apply", data={"csrf": token, "loc": sh.id, "new_1": "5", "old_1": "4"})
    assert r.status_code == 404
