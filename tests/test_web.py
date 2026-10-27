"""Web layer: authentication, CSRF, server-side permissions, location scope, uploads, end-to-end flows."""
import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import Inspection, Issue, Location, Product, Recommendation, User
from app.security import hash_password
from app.timeutil import today_local
from tests.conftest import tx
from tests.test_services import iso, seeded

PW = "Passw0rd-long-1"


@pytest.fixture()
def client(cfg):
    from app.web.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c


def add_user(session, name, role, locs=None, must_change=False):
    u = User(username=name, full_name=name, role=role, password_hash=hash_password(PW), location_ids=locs, must_change_password=must_change)
    session.add(u); session.commit()
    return u


def login(client, name, pw=PW):
    r = client.post("/login", data={"username": name, "password": pw})
    return r


def csrf_of(client, path="/"):
    html = client.get(path).text
    m = re.search(r'name="csrf" value="([^"]+)"', html)
    assert m, f"no csrf token on {path}"
    return m.group(1)


def test_requires_login(client):
    r = client.get("/inventory")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert client.get("/healthz").status_code == 200  # health check is public and reveals nothing sensitive


def test_login_logout_and_wrong_password(client, session):
    add_user(session, "ann", "admin")
    assert "Wrong username" in login(client, "ann", "nope").text
    assert login(client, "ann").status_code == 303
    assert client.get("/").status_code == 200
    r = client.post("/logout", data={"csrf": csrf_of(client)})
    assert r.status_code == 303
    assert client.get("/").status_code == 303


def test_lockout_after_five_failures(client, session):
    add_user(session, "bob", "admin")
    for _ in range(5):
        login(client, "bob", "wrong")
    assert "Too many failed attempts" in login(client, "bob", PW).text


def test_forced_password_change(client, session):
    add_user(session, "new", "admin", must_change=True)
    login(client, "new")
    assert client.get("/inventory").headers["location"] == "/password"
    tok = csrf_of(client, "/password")
    r = client.post("/password", data={"current": PW, "new": "abc", "confirm": "abc", "csrf": tok})
    assert "err=" in r.headers["location"]
    r = client.post("/password", data={"current": PW, "new": "Better-Passw0rd9", "confirm": "Better-Passw0rd9", "csrf": tok})
    assert r.headers["location"].startswith("/?ok=")
    assert client.get("/inventory").status_code in (200,)


def test_staff_cannot_reach_admin_or_manager_pages(client, session):
    add_user(session, "stf", "staff")
    login(client, "stf")
    for path in ("/settings", "/users", "/suppliers", "/products", "/audit", "/sales", "/sync", "/issues", "/expiry", "/purchase-orders", "/performance", "/data-quality"):
        assert client.get(path).status_code == 403, path
    for path in ("/", "/inspections", "/counts", "/stock-events", "/replenishment?view=list", "/inventory", "/assistant"):
        assert client.get(path).status_code == 200, path
    tok = csrf_of(client, "/")
    assert client.post("/sync/run", data={"job": "sales", "csrf": tok}).status_code == 403
    assert client.post("/users/new", data={"username": "x", "role": "admin", "csrf": tok}).status_code == 403
    assert client.post("/replenishment/1/decide", data={"action": "approve", "csrf": tok}).status_code == 404  # staff may decide, but this record does not exist


def test_csrf_is_required_on_every_post(client, session):
    add_user(session, "adm", "admin")
    login(client, "adm")
    assert client.post("/sync/run", data={"job": "sales"}).status_code == 403
    assert client.post("/sync/run", data={"job": "sales", "csrf": "forged"}).status_code == 403
    assert client.post("/settings/value", data={"key": "po.stale_days", "value": "9"}).status_code == 403


def test_settings_change_is_validated_and_audited(client, session):
    add_user(session, "adm", "admin")
    login(client, "adm")
    tok = csrf_of(client, "/settings")
    assert "err=" in client.post("/settings/value", data={"key": "po.stale_days", "value": "abc", "csrf": tok}).headers["location"]
    assert client.post("/settings/value", data={"key": "bogus.key", "value": "1", "csrf": tok}).status_code == 400
    assert "ok=" in client.post("/settings/value", data={"key": "po.stale_days", "value": "5", "csrf": tok}).headers["location"]
    from app.models import AuditEvent
    session.expire_all()
    ev = session.scalars(select(AuditEvent).where(AuditEvent.action == "setting.update")).all()
    assert len(ev) == 1 and ev[0].after == {"value": 5} and ev[0].before == {"value": 7}


def test_location_scope_is_enforced(client, session, sync, fake):
    seeded(sync, fake, session)
    from app.services import replenishment
    replenishment.run(session); session.commit()
    a = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    b = session.scalar(select(Location).where(Location.storehub_id == "S_B"))
    add_user(session, "mgrb", "store_manager", locs=[b.id])
    login(client, "mgrb")
    tok = csrf_of(client, "/")
    rec_a = session.scalar(select(Recommendation).where(Recommendation.location_id == a.id))
    assert client.post(f"/replenishment/{rec_a.id}/decide", data={"action": "approve", "csrf": tok}).status_code == 403
    assert client.get(f"/inventory?loc={a.id}").status_code == 403
    assert client.get(f"/inventory?loc={b.id}").status_code == 200
    html = client.get("/replenishment?status=all").text
    assert "Alpha" not in html  # the other store's suggestions are not even listed


def test_manager_decides_and_batches_via_the_ui(client, session, sync, fake):
    seeded(sync, fake, session)
    from app.services import replenishment
    replenishment.run(session); session.commit()
    add_user(session, "boss", "management")
    login(client, "boss")
    tok = csrf_of(client, "/replenishment?view=list")
    rec = session.scalar(select(Recommendation).where(Recommendation.status == "pending", Recommendation.source_kind == "transfer"))
    r = client.post(f"/replenishment/{rec.id}/decide", data={"action": "modify", "qty": "4", "reason": "Other", "csrf": tok})
    assert "ok=" in r.headers["location"]
    r = client.post(f"/replenishment/{rec.id}/decide", data={"action": "reject", "reason": "Other", "csrf": tok})
    assert "err=" in r.headers["location"]  # already decided
    assert "ok=" in client.post("/replenishment/batches/create", data={"csrf": tok}).headers["location"]
    # warehouse-to-store transfer batches are only for the warehouse team and admins
    assert "Batch #" not in client.get("/replenishment/batches").text and client.get("/replenishment/batches/1.csv").status_code == 404
    assert client.get("/stock-transfers").status_code == 200  # management can view transfers (store receipt loop)
    add_user(session, "whse_b", "warehouse")
    login(client, "whse_b")
    assert "Batch #" in client.get("/replenishment/batches").text
    csv = client.get("/replenishment/batches/1.csv")
    assert csv.status_code == 200 and "Cola 500ml" in csv.text
    assert client.get("/stock-transfers").status_code == 200


def test_inspection_submit_with_failure_and_photo_upload(client, session, sync):
    sync.sync_stores(); session.expire_all()
    from app.services import inspections
    inspections.generate_tasks(session, today_local()); session.commit()
    add_user(session, "boss", "management")
    login(client, "boss")
    insp = session.scalar(select(Inspection).join(Inspection.template).where(Inspection.template.has(code="store_daily")))
    html = client.get(f"/inspections/{insp.id}").text
    tok = re.search(r'name="csrf" value="([^"]+)"', html).group(1)
    items = insp.template.items
    data = {"csrf": tok}
    for it in items:
        if it.kind == "number":
            data[f"v_{it.id}"] = "24"
        elif it.kind == "check":
            data[f"p_{it.id}"] = "pass"
    first = items[0]
    video = next(i for i in items if i.kind == "video")
    mp4 = bytes.fromhex("0000001866747970") + b"mp42" + b"0" * 300
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 200
    r = client.post(f"/inspections/{insp.id}/submit", data={"csrf": tok}, files={f"f_{first.id}": ("p.png", png, "image/png")})
    assert "err=" in r.headers["location"]  # the walkthrough video is required
    # the store daily inspection is reviewed from its photos: no pass/fail and no comment, so nothing can "fail" and no issue is opened
    data[f"p_{first.id}"] = "fail"; data[f"n_{first.id}"] = "ignored"
    r = client.post(f"/inspections/{insp.id}/submit", data=data, files={f"f_{first.id}": ("p.png", png, "image/png"), f"f_{video.id}": ("walk.mp4", mp4, "video/mp4")})
    assert "ok=" in r.headers["location"], r.headers["location"]
    session.expire_all()
    assert session.scalar(select(Issue).where(Issue.source == "inspection")) is None
    done = session.get(Inspection, insp.id)
    assert done.fail_count == 0 and all(r_.passed is None for r_ in done.results if r_.item.kind == "check") and all(r_.note is None for r_ in done.results)
    assert any(r_.photos for r_ in done.results if r_.item_id == first.id)
    # a disguised non-image is rejected
    insp2 = session.scalar(select(Inspection).join(Inspection.template).where(Inspection.template.has(code="warehouse_daily")))
    bad = client.post(f"/inspections/{insp2.id}/submit", data={"csrf": tok, **{f"p_{i.id}": "pass" for i in insp2.template.items if i.kind == 'check'}},
                      files={f"f_{insp2.template.items[0].id}": ("x.png", b"<html>not an image</html>", "image/png")})
    assert bad.status_code in (400, 303)


def test_uploads_require_login_and_block_traversal(client, session, cfg):
    f = cfg.upload_dir / "2026" / "10"
    f.mkdir(parents=True, exist_ok=True)
    (f / "a.png").write_bytes(b"\x89PNG" + b"0" * 20)
    assert client.get("/uploads/2026/10/a.png").status_code == 303
    add_user(session, "u1", "staff")
    login(client, "u1")
    assert client.get("/uploads/2026/10/a.png").status_code == 200
    assert client.get("/uploads/../retailos.db").status_code == 404
    assert client.get("/uploads/..%2fretailos.db").status_code == 404


def test_user_admin_cannot_remove_last_admin(client, session):
    u = add_user(session, "only", "admin")
    login(client, "only")
    tok = csrf_of(client, "/users")
    r = client.post(f"/users/{u.id}", data={"role": "staff", "csrf": tok, "active": "1"})
    assert "err=" in r.headers["location"]


def test_every_page_renders_for_admin(client, session, sync, fake):
    seeded(sync, fake, session)
    from app.services import dq, replenishment, inspections
    replenishment.run(session); dq.run_all(session); inspections.generate_tasks(session, today_local()); session.commit()
    add_user(session, "adm", "admin")
    login(client, "adm")
    for path in ["/", "/issues", "/issues/new", "/inspections", "/replenishment/sheet", "/replenishment?view=list", "/replenishment?status=all", "/replenishment/batches", "/inventory?view=list", "/inventory/compare", "/inventory?status=negative",
                 "/inventory?export=csv", "/sales", "/purchase-orders", "/purchase-orders?view=all", "/counts", "/expiry", "/data-quality", "/sync", "/settings",
                 "/suppliers", "/products", "/products?export=csv", "/users", "/audit", "/notifications", "/compliance", "/performance", "/assistant", "/assistant/metrics"]:
        r = client.get(path)
        assert r.status_code == 200, (path, r.status_code)


def test_security_headers_and_no_caching(client, session):
    r = client.get("/login")
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"
    assert client.get("/static/style.css").status_code == 200


def test_dashboard_does_not_claim_all_clear_when_items_exist(client, session, sync, fake):
    seeded(sync, fake, session)
    from app.services import replenishment
    replenishment.run(session); session.commit()
    add_user(session, "adm", "admin")
    login(client, "adm")
    html = client.get("/").text
    assert "order suggestions are ready" in html and "Nothing needs your attention right now." not in html


def test_password_policy_is_four_characters_of_any_kind():
    from app.security import password_problem
    assert password_problem("123") is not None and password_problem("ab") is not None
    for ok in ("1234", "abcd", "!@#$", "pass word1", "Ab1!Ab1!", "12345678"):
        assert password_problem(ok) is None, ok
    assert password_problem(" 1234") is not None  # no leading or trailing space


# ---------------------------------------------------------------- AI screens
def test_ai_screens_permissions_flow_and_csrf(client, session, sync, fake, monkeypatch):
    import json
    from app.ai import service
    from app.ai.provider import LlmResult, Provider
    from app.models import AiAction, AiOutput

    class FM(Provider):
        name, model, available = "fake", "fake-1", True
        def __init__(self): self.n = 0
        def complete(self, system, messages, max_tokens=0, timeout=0):
            self.n += 1
            if "QUESTION" in messages[0]["content"]:
                body = {"answer": [{"type": "hypothesis", "text": "It may be a quiet day", "refs": []}],
                        "suggested_actions": [{"tool": "send_reminder", "args": {"role": "store_manager", "message": "Check shelves"}, "why": "quiet sales", "confidence": 0.9}]}
            else:
                body = {"headline": "All calm", "items": []}
            return LlmResult(json.dumps(body), "fake", "fake-1", 3, 0.0)

    monkeypatch.setattr(service, "get_provider", lambda *a, **k: FM())
    seeded(sync, fake, session)
    add_user(session, "stf", "staff"); add_user(session, "boss", "management")
    login(client, "stf")
    assert client.get("/assistant").status_code == 200 and client.post("/assistant/ask", data={"question": "x", "csrf": "t"}).status_code == 403  # staff may use the assistant; bad CSRF is rejected
    client.post("/logout", data={"csrf": csrf_of(client)})
    login(client, "boss")
    page = client.get("/assistant")
    assert page.status_code == 200 and "Daily briefing" in page.text and "Connected" in page.text
    tok = csrf_of(client, "/assistant")
    assert client.post("/assistant/ask", data={"question": "How are we doing?"}).status_code == 403  # CSRF
    r = client.post("/assistant/ask", data={"question": "How are we doing?", "csrf": tok})
    assert r.status_code == 303
    out = session.scalar(select(AiOutput).where(AiOutput.kind == "chat")); oid = out.id
    html = client.get(f"/assistant/output/{oid}").text
    assert "It may be a quiet day" in html and "send reminder" in html.lower() and "Supporting details" in html
    act = session.scalar(select(AiAction)); session.expire_all()
    assert client.post(f"/assistant/action/{act.id}/run", data={"csrf": "bad"}).status_code == 403
    r = client.post(f"/assistant/action/{act.id}/run", data={"csrf": tok, "back": f"/assistant/output/{oid}"})
    assert "ok=" in r.headers["location"]
    session.expire_all()
    assert session.get(AiAction, act.id).status == "executed"
    assert "ok=" in client.post(f"/assistant/output/{oid}/rate", data={"useful": "1", "csrf": tok}).headers["location"]
    assert client.get("/assistant/metrics").status_code == 200
    r = client.post("/assistant/brief", data={"csrf": tok}); assert "ok=" in r.headers["location"]
    assert "All calm" in client.get("/assistant").text and "AI briefing" in client.get("/").text


def test_ai_unavailable_is_explained_not_broken(client, session, monkeypatch):
    from app.ai import service
    from app.ai.provider import Provider
    monkeypatch.setattr(service, "get_provider", lambda *a, **k: Provider())
    add_user(session, "boss", "management"); login(client, "boss")
    assert "not connected" in client.get("/assistant").text.lower()
    tok = csrf_of(client, "/assistant")
    r = client.post("/assistant/ask", data={"question": "net sales yesterday", "csrf": tok})
    assert "err=" in r.headers["location"]
    assert client.get("/").status_code == 200  # the dashboard works without AI


def test_receiving_photo_is_stored(client, session, sync, fake):
    from tests.test_services import _po
    from app.models import Receiving
    from app.services import receiving as recsvc
    po = _po(sync, fake, session)
    add_user(session, "wh", "warehouse")
    login(client, "wh")
    u = session.scalar(select(User).where(User.username == "wh"))
    rec = recsvc.start(session, u, po); session.commit()
    tok = csrf_of(client, f"/receiving/{rec.id}")
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    data = {"csrf": tok, f"c_{rec.lines[0].id}": "10", f"d_{rec.lines[0].id}": "0"}
    r = client.post(f"/receiving/{rec.id}/save", data=data, files={"photo": ("p.png", png, "image/png")})
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    session.expire_all()
    assert session.get(Receiving, rec.id).photo_path


def test_replenishment_row_details_open_in_the_drawer(client, session, sync, fake):
    seeded(sync, fake, session)
    from app.services import replenishment
    replenishment.run(session); session.commit()
    add_user(session, "adm2", "admin")
    login(client, "adm2")
    html = client.get("/replenishment?view=list").text
    # detail and decision content live in inert <template> blocks, opened in the slide-over drawer
    assert 'id="drawer"' in html and 'id="drawer-body"' in html
    assert 'data-drawer="why-' in html and '<template id="why-' in html
    assert 'class="decide"' not in html and 'data-drawer="decide-' not in html  # no per-row approve/reject: rows are ticked and approved together above the table
    # no inline dropdowns inflating rows
    assert "<details><summary" not in html


def test_store_bound_user_is_limited_to_their_store(client, session, sync, fake):
    """A store user assigned to one location: no issues/performance/DQ/sync pages, and other stores' data is closed."""
    seeded(sync, fake, session)
    alpha = session.scalar(select(Location).where(Location.name.like("%Alpha%")))
    beta = session.scalar(select(Location).where(Location.name.like("%Beta%")))
    add_user(session, "storemgr", "store_manager", locs=[alpha.id])
    login(client, "storemgr")
    for page in ("/issues", "/performance", "/data-quality", "/sync"):
        assert client.get(page).status_code == 403, page
    for page in ("/", "/replenishment?view=list", "/sales", "/inventory", "/inspections"):
        assert client.get(page).status_code == 200, page
    assert client.get(f"/sales?loc={beta.id}").status_code == 403  # another store is off limits
    home = client.get("/").text
    assert 'href="/issues"' not in home and 'href="/data-quality"' not in home  # hidden from the nav, not just blocked
    # an HQ manager (no assigned locations) keeps full access
    add_user(session, "hqmgr", "store_manager")
    login(client, "hqmgr")
    assert client.get("/issues").status_code == 200 and client.get("/data-quality").status_code == 200


def test_counts_can_be_deleted_and_the_audit_keeps_a_record(client, session):
    from app.models import AuditEvent, CycleCount
    loc = Location(storehub_id="S_DEL", name="Del Store", type="store", monitored=True)
    session.add(loc); session.commit()
    mgr = add_user(session, "mgr_del", "store_manager", [loc.id])
    cc = CycleCount(location_id=loc.id, business_date=today_local(), created_by=mgr.id)
    session.add(cc); session.commit()
    cid = cc.id
    login(client, "mgr_del")
    assert f"/counts/{cid}/delete" in client.get("/counts").text
    assert client.post(f"/counts/{cid}/delete", data={"csrf": "bad"}).status_code == 403
    r = client.post(f"/counts/{cid}/delete", data={"csrf": csrf_of(client, "/counts")})
    assert r.status_code == 303 and r.headers["location"].startswith("/counts")
    session.expire_all()
    assert session.get(CycleCount, cid) is None
    assert session.scalar(select(AuditEvent).where(AuditEvent.action == "count.delete", AuditEvent.entity_id == str(cid))) is not None


def test_expiry_upload_csv_and_excel(client, session):
    import io
    from datetime import date
    from openpyxl import Workbook
    from app.models import ExpiryBatch
    loc = Location(storehub_id="S_EXP", name="Exp Store", type="store", monitored=True)
    p1 = Product(storehub_id="P_E1", name="Milk 1L", barcode="480001", sku="MILK")
    p2 = Product(storehub_id="P_E2", name="Bread", barcode="480002")
    session.add_all([loc, p1, p2]); session.commit()
    add_user(session, "mgr_exp", "store_manager", [loc.id])
    login(client, "mgr_exp")
    tok = csrf_of(client, "/expiry")
    csv_text = "Product,Qty,Expiry date,Batch\n480001,5,2027-01-31,B1\nBread,3,12/31/2027,\nNope,1,2027-01-01,\n480001,0,2027-01-01,\n"
    r = client.post("/expiry/import", data={"csrf": tok, "location_id": str(loc.id)}, files={"file": ("e.csv", csv_text.encode(), "text/csv")})
    assert r.status_code == 303 and "ok=" in r.headers["location"] and "skipped%202" in r.headers["location"]
    session.expire_all()
    rows = list(session.scalars(select(ExpiryBatch).order_by(ExpiryBatch.id)))
    assert [(b.product_id, b.qty, b.expiry_date, b.batch_no, b.source) for b in rows] == [(p1.id, 5.0, date(2027, 1, 31), "B1", "import"), (p2.id, 3.0, date(2027, 12, 31), None, "import")]
    # the same file again adds nothing
    r = client.post("/expiry/import", data={"csrf": tok, "location_id": str(loc.id)}, files={"file": ("e.csv", csv_text.encode(), "text/csv")})
    session.expire_all()
    assert len(list(session.scalars(select(ExpiryBatch)))) == 2 and "already%20recorded" in r.headers["location"]
    # Excel with real date cells and a location column
    wb = Workbook(); ws = wb.active
    ws.append(["Barcode", "Quantity", "Expiration date", "Store"]); ws.append(["480002", 7, date(2027, 6, 1), "Exp Store"])
    buf = io.BytesIO(); wb.save(buf)
    r = client.post("/expiry/import", data={"csrf": tok, "location_id": "0"}, files={"file": ("e.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    session.expire_all()
    assert len(list(session.scalars(select(ExpiryBatch)))) == 3 and "ok=" in r.headers["location"]
    bad = client.post("/expiry/import", data={"csrf": tok, "location_id": "0"}, files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")})
    assert "err=" in bad.headers["location"]
    assert client.get("/expiry/template.csv").status_code == 200


def test_sales_days_filter_has_today(client, session):
    session.add(Location(storehub_id="S_SD", name="Sales Store", type="store", monitored=True)); session.commit()
    add_user(session, "boss_s", "admin")
    login(client, "boss_s")
    today_page = client.get("/sales?days=1").text
    one = re.search(r'class="drp" data-from="([\d-]+)" data-to="([\d-]+)" data-today="([\d-]+)"', today_page)
    assert one and one.group(1) == one.group(2) == one.group(3) and "Net sales (Today)" in today_page  # the period picker shows just today
    week = re.search(r'class="drp" data-from="([\d-]+)" data-to="([\d-]+)"', client.get("/sales?days=7").text)
    assert week and week.group(1) != week.group(2)
    custom = re.search(r'class="drp" data-from="([\d-]+)" data-to="([\d-]+)"', client.get("/sales?days=custom&frm=2026-01-05&to=2026-03-20").text)
    assert custom and custom.groups() == ("2026-01-05", "2026-03-20")  # a long range (the picker's "This year") is accepted


def test_assistant_answers_greetings_directly_without_the_model(client, session):
    from app.ai import service
    u = add_user(session, "boss_ai", "admin")
    for q in ("hi", "Hello!", "good morning", "thanks", "你好", "what can you do"):
        assert service.smalltalk(q, u), q
    for q in ("hi, what were sales yesterday?", "Which products are low on stock?", "net sales today"):
        assert service.smalltalk(q, u) is None, q
    row = service.ask(session, u, "hey")
    assert row.provider == "built-in" and row.output_json["answer"][0]["type"] == "message" and "Hi" in row.output_json["answer"][0]["text"]
    assert service.status(session)["used_today"] == 0  # greetings do not use up the daily AI allowance


def test_permits_page_records_processed_and_expiry_dates_only(client, session):
    from datetime import timedelta
    from app.models import ComplianceItem
    add_user(session, "boss_pm", "management")
    login(client, "boss_pm")
    html = client.get("/compliance").text
    assert "Add a permit" in html and 'name="processed"' in html and 'name="expiry"' in html
    assert 'name="owner_id"' not in html and 'name="kind"' not in html and "Permits and maintenance" not in html and "Equipment" not in html
    tok = csrf_of(client, "/compliance")
    t = today_local()
    r = client.post("/compliance/add", data={"csrf": tok, "name": "Mayor's permit", "processed": str(t), "expiry": str(t + timedelta(days=365))})
    assert "ok=" in r.headers["location"]
    bad = client.post("/compliance/add", data={"csrf": tok, "name": "Bad dates", "processed": str(t), "expiry": str(t - timedelta(days=1))})
    assert "err=" in bad.headers["location"]  # it cannot expire before it was processed
    session.expire_all()
    it = session.scalar(select(ComplianceItem))
    assert it.kind == "permit" and it.owner_id is None and it.processed_date == t and it.due_date == t + timedelta(days=365)
    page = client.get("/compliance").text
    assert "Mayor&#39;s permit" in page and str(t) in page and str(t + timedelta(days=365)) in page
    before_permit = page.split(f'data-modal="permit-{it.id}"', 1)[0]
    assert f'data-modal="permit-{it.id}"' in page and f'<dialog class="modal" id="permit-{it.id}"' in page and "<details" not in before_permit  # Update opens a modal, not a dropdown
    assert f'action="/compliance/{it.id}/renew"' in page and 'name="new_due"' in page


def test_permit_without_expiry_is_added_as_no_expiry_and_never_alerts(client, session):
    from app.models import ComplianceItem
    from app.services import compliance
    add_user(session, "boss_ne", "management")
    login(client, "boss_ne")
    tok = csrf_of(client, "/compliance")
    r = client.post("/compliance/add", data={"csrf": tok, "name": "Sanitary permit", "processed": "", "expiry": ""})
    assert "ok=" in r.headers["location"]
    session.expire_all()
    it = session.scalar(select(ComplianceItem).where(ComplianceItem.name == "Sanitary permit"))
    assert it.due_date is None
    assert "No Expiry" in client.get("/compliance").text
    assert compliance.check(session) == 0


def test_staff_get_the_visual_dashboard_and_managers_keep_the_sales_overview(client, session):
    loc = Location(storehub_id="S_DB", name="The Daily Centro - Dash", type="store", monitored=True)
    session.add(loc); session.commit()
    add_user(session, "staff_db", "staff", [loc.id])
    login(client, "staff_db")
    html = client.get("/").text
    assert 'class="sd-hero"' in html and 'class="sd-tiles"' in html and "Stock health" in html and "Net sales today" not in html
    add_user(session, "boss_db", "management")
    login(client, "boss_db")
    html = client.get("/").text
    assert 'class="sd-hero"' not in html and "All stores, net sales today" in html


def test_data_entry_pages_guard_unsaved_work_and_show_progress(client, session, sync):
    from app.services import inspections
    sync.sync_stores(); session.expire_all()
    inspections.generate_tasks(session, today_local()); session.commit()
    add_user(session, "boss_ux", "management")
    login(client, "boss_ux")
    insp = session.scalar(select(Inspection).join(Inspection.template).where(Inspection.template.has(code="store_daily")))
    html = client.get(f"/inspections/{insp.id}").text
    assert "data-guard" in html and "data-photo-only" in html and 'id="chk-prog"' in html and "chkbar" in html  # unsaved-work guard + progress for the photo checklist
    js = client.get("/static/app.js").text
    assert "wantsBusy" in js and "beforeunload" in js  # every POST form acknowledges the press and cannot be sent twice


def test_sales_page_shows_the_average_net_sales_per_day(client, session):
    from app.models import SalesDay
    loc = Location(storehub_id="S_AVG", name="Avg Store", type="store", monitored=True)
    session.add(loc); session.flush()
    t = today_local()
    for d in range(1, 15):  # 14 trading days of 100.00 each => average 100.00 a day
        session.add(SalesDay(location_id=loc.id, business_date=t - timedelta(days=d), tx_count=5, net_sales=100.0, gross_sales=100.0, cash_sales=60.0, other_sales=40.0))
    session.commit()
    add_user(session, "boss_avg", "admin")
    login(client, "boss_avg")
    html = client.get("/sales?days=14").text
    assert "Avg / day" in html and "Net sales (14 days)" in html
    assert "Avg / day" not in client.get("/sales?days=1").text  # a single day has no average


def test_stock_counts_and_stock_changes_upload_csv_and_excel(client, session):
    import io
    from openpyxl import Workbook
    from app.models import CycleCount, InventoryCurrent, Issue, StockEvent
    loc = Location(storehub_id="S_IMP", name="The Daily Centro - Imp", type="store", monitored=True)
    p1 = Product(storehub_id="P_I1", name="Milk 1L", barcode="480101", sku="MILK1")
    p2 = Product(storehub_id="P_I2", name="Bread", barcode="480102")
    session.add_all([loc, p1, p2]); session.flush()
    session.add(InventoryCurrent(location_id=loc.id, product_id=p1.id, on_hand=10.0))
    session.commit()
    add_user(session, "mgr_imp", "store_manager", [loc.id])
    login(client, "mgr_imp")
    tok = csrf_of(client, "/counts")
    # ---- stock counts
    csv_text = "Product,Counted,Note\n480101,7,two broken\nBread,3,miscount\nNope,1,\n480101,-2,\n"
    r = client.post("/counts/import", data={"csrf": tok, "location_id": str(loc.id)}, files={"file": ("c.csv", csv_text.encode(), "text/csv")})
    assert r.status_code == 303 and "ok=" in r.headers["location"] and "skipped%202" in r.headers["location"]
    session.expire_all()
    cc = session.scalar(select(CycleCount))
    lines = {l.product_id: l for l in cc.lines}
    assert cc.status == "open" and lines[p1.id].counted_qty == 7 and lines[p1.id].variance == -3 and lines[p1.id].note == "two broken" and lines[p2.id].counted_qty == 3
    # a second file adds to the same open count instead of opening another
    client.post("/counts/import", data={"csrf": tok, "location_id": str(loc.id), "submit": "1"}, files={"file": ("c2.csv", b"Product,Counted,Note\n480101,9,recount\n", "text/csv")})
    session.expire_all()
    assert session.query(CycleCount).count() == 1 and session.scalar(select(CycleCount)).status == "submitted"
    assert client.get("/counts/template.csv").status_code == 200 and client.get("/counts").status_code == 200
    # ---- stock changes (Excel, with the type and location in the file)
    wb = Workbook(); ws = wb.active
    ws.append(["Barcode", "Quantity", "Type", "Reason", "Note", "Store"])
    ws.append(["480101", 2, "Write-off", "Damaged", "dropped", "The Daily Centro - Imp"])
    ws.append(["480102", 5, "unplanned receipt", "driver dropped extra", "", "The Daily Centro - Imp"])
    ws.append(["480102", 1, "gift", "", "", "The Daily Centro - Imp"])
    buf = io.BytesIO(); wb.save(buf)
    tok = csrf_of(client, "/stock-events")
    r = client.post("/stock-events/import", data={"csrf": tok, "location_id": "0", "kind": ""}, files={"file": ("e.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert "ok=" in r.headers["location"] and "skipped%201" in r.headers["location"]
    session.expire_all()
    evs = {e.kind: e for e in session.scalars(select(StockEvent))}
    assert evs["write_off"].reason == "damaged" and evs["unplanned_receipt"].reason == "other" and "driver dropped extra" in evs["unplanned_receipt"].note
    assert session.query(Issue).filter(Issue.source_ref.like("stock_event:%")).count() == 2  # each still needs keying into StoreHub
    # the same file again records nothing new; a file marked "already in StoreHub" creates no to-do
    again = client.post("/stock-events/import", data={"csrf": tok, "location_id": "0", "kind": ""}, files={"file": ("e.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert "already%20recorded" in again.headers["location"]
    client.post("/stock-events/import", data={"csrf": tok, "location_id": str(loc.id), "kind": "write_off", "in_storehub": "1"}, files={"file": ("w.csv", b"Product,Qty\n480101,4\n", "text/csv")})
    session.expire_all()
    done = [e for e in session.scalars(select(StockEvent)) if e.qty == 4]
    assert len(done) == 1 and done[0].verified_at is not None
    assert session.query(Issue).filter(Issue.source_ref.like("stock_event:%")).count() == 2
    assert client.get("/stock-events/template.csv").status_code == 200 and client.get("/stock-events").status_code == 200


def test_operations_charts_are_for_admin_and_warehouse_only(client, session):
    session.add(Location(storehub_id="S_OPS", name="The Daily Centro - Ops", type="store", monitored=True)); session.commit()
    for name, role in (("ops_admin", "admin"), ("ops_whse", "warehouse"), ("ops_boss", "management"), ("ops_mgr", "store_manager")):
        add_user(session, name, role)
    for name in ("ops_admin", "ops_whse"):
        login(client, name)
        html = client.get("/").text
        assert "Operations at a glance" in html and "Auto replenishment today" in html and "Expiry exposure" in html, name
    for name in ("ops_boss", "ops_mgr"):
        login(client, name)
        assert "Operations at a glance" not in client.get("/").text, name


def test_inspection_due_times_say_am_or_pm():
    from datetime import datetime
    from app.timeutil import fmt_clock
    # stored times are UTC; the stores run on Philippine time (UTC+8): noon is 04:00 UTC, midnight is 16:00 UTC the day before
    assert fmt_clock(datetime(2026, 10, 12, 4, 0)) == "12:00 PM"
    assert fmt_clock(datetime(2026, 10, 11, 16, 0)) == "12:00 AM"
    assert fmt_clock(datetime(2026, 10, 12, 1, 5)) == "9:05 AM" and fmt_clock(datetime(2026, 10, 12, 13, 30)) == "9:30 PM" and fmt_clock(None) == ""


def test_inspection_list_and_form_show_the_due_time_with_am_pm(client, session, sync):
    from app.services import inspections
    sync.sync_stores(); session.expire_all()
    inspections.generate_tasks(session, today_local()); session.commit()
    add_user(session, "boss_pm2", "management")
    login(client, "boss_pm2")
    insp = session.scalar(select(Inspection).join(Inspection.template).where(Inspection.template.has(code="store_daily")))
    assert "12:00 PM" in client.get("/inspections").text  # the Store Daily Inspection is due at noon
    assert "12:00 PM" in client.get(f"/inspections/{insp.id}").text


def test_assigned_stores_are_saved_and_shown_checked(client, session):
    a = Location(storehub_id="S_A", name="Store A", type="store", monitored=True)
    b = Location(storehub_id="S_B", name="Store B", type="store", monitored=True)
    session.add_all([a, b]); session.commit()
    add_user(session, "boss_loc", "admin")
    staff = add_user(session, "staff_loc", "staff")
    login(client, "boss_loc")
    tok = csrf_of(client, "/users")
    r = client.post(f"/users/{staff.id}", data={"csrf": tok, "role": "staff", "full_name": "S", "location_ids": [b.id], "active": "1"})
    assert "ok=" in r.headers["location"]
    session.expire_all()
    assert session.get(User, staff.id).location_ids == [b.id]
    html = client.get("/users").text
    import re
    form_html = re.search(rf'<form[^>]+action="/users/{staff.id}"[^>]*>(.*?)</form>', html, re.S).group(1)
    boxes = re.findall(r'name="location_ids" value="(\d+)" (checked)?', form_html)
    assert [(int(v), bool(ck)) for v, ck in boxes][:2] == [(a.id, False), (b.id, True)]


def test_users_can_be_filtered_by_store(client, session):
    a = Location(storehub_id="S_FA", name="Filter A", type="store", monitored=True)
    b = Location(storehub_id="S_FB", name="Filter B", type="store", monitored=True)
    session.add_all([a, b]); session.commit()
    add_user(session, "boss_f", "admin")
    add_user(session, "only_a", "staff", locs=[a.id])
    add_user(session, "only_b", "staff", locs=[b.id])
    login(client, "boss_f")
    page = client.get(f"/users?loc={a.id}").text
    assert "only_a" in page and "only_b" not in page
    assert "boss_f" in client.get("/users?loc=all").text and "only_a" not in client.get("/users?loc=all").text
    assert "only_b" in client.get("/users?q=only").text


def test_order_suggestions_have_a_product_search_and_the_header_has_none(client, session):
    add_user(session, "boss_s", "admin")
    login(client, "boss_s")
    page = client.get("/replenishment?search=milk").text
    assert 'name="search"' in page and 'value="milk"' in page
    assert 'class="search"' not in page and "Search products, barcodes" not in page


def test_planogram_page_is_login_only_and_serves_nothing_outside_its_folder(client, session):
    assert client.get("/planogram", follow_redirects=False).status_code in (302, 303)
    assert client.get("/planogram-app/", follow_redirects=False).status_code in (302, 303)
    add_user(session, "boss_pl", "admin")
    login(client, "boss_pl")
    page = client.get("/planogram")
    assert page.status_code == 200 and "Planogram" in page.text
    assert client.get("/planogram-app/../app/config.py").status_code in (404, 400)
    assert client.get("/planogram-app/%2e%2e/.env").status_code == 404


def test_wastage_is_recorded_scoped_and_ready_for_lark(client, session):
    from app.models import Product, WastageEntry
    from app import lark
    loc = Location(storehub_id="S_W", name="The Daily Centro - Waste", type="store", monitored=True)
    other = Location(storehub_id="S_W2", name="The Daily Centro - Other", type="store", monitored=True)
    prod = Product(storehub_id="P_W", name="Siopao Asado", barcode="W123", category="Snack Meals", in_api=True)
    session.add_all([loc, other, prod]); session.commit()
    add_user(session, "waste_staff", "staff", locs=[loc.id])
    login(client, "waste_staff")
    tok = csrf_of(client, "/wastage")
    r = client.post("/wastage/add", data={"csrf": tok, "location_id": loc.id, "product": "W123", "qty": "3", "reason": "Spoiled"})
    assert "ok=" in r.headers["location"]
    bad = client.post("/wastage/add", data={"csrf": tok, "location_id": loc.id, "product": "W123", "qty": "0", "reason": "Spoiled"})
    assert "err=" in bad.headers["location"]
    assert client.post("/wastage/add", data={"csrf": tok, "location_id": other.id, "product": "W123", "qty": "1", "reason": "Spoiled"}).status_code == 403
    session.expire_all()
    e = session.scalar(select(WastageEntry))
    assert e.qty == 3 and e.category == "Snack Meals" and e.reason == "Spoiled"
    assert "Siopao Asado" in client.get("/wastage").text
    rows = lark.rows_wastage(session)
    assert rows[0][1]["Store 店铺"] == "The Daily Centro Waste" and rows[0][1]["Quantity 数量"] == 3 and rows[0][1]["Reason 原因"] == "Spoiled"
