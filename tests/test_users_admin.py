"""Admin user management: passwords set directly (no temporary passwords), visible to admins, and store-scoped AI briefs."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ai import service as ai_service
from app.models import AiOutput, AuditEvent, Location, User, WebSession
from app.security import verify_password
from app.web.main import create_app
from tests.test_web import PW, add_user, csrf_of, login, seeded


@pytest.fixture
def client(cfg):
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c


def test_admin_sets_password_directly_and_sees_it(sync, fake, session, client):
    seeded(sync, fake, session)
    add_user(session, "adm", "admin")
    login(client, "adm")
    tok = csrf_of(client, "/users")
    r = client.post("/users/new", data={"username": "bantayan1", "full_name": "Store One", "password": "store1", "role": "store_manager", "csrf": tok})
    assert r.status_code in (302, 303)
    u = session.scalar(select(User).where(User.username == "bantayan1"))
    assert u is not None and not u.must_change_password and verify_password("store1", u.password_hash)
    assert u.password_plain == "store1"
    assert "store1" in client.get("/users").text  # visible to the admin on the users page
    # too-short passwords are rejected
    r = client.post("/users/new", data={"username": "badpw", "password": "abc", "role": "staff", "csrf": tok}, follow_redirects=True)
    assert "at least 4 characters" in r.text
    assert session.scalar(select(User).where(User.username == "badpw")) is None


def test_admin_changes_a_users_password(sync, fake, session, client):
    seeded(sync, fake, session)
    add_user(session, "adm", "admin")
    staff = add_user(session, "crew", "staff")
    login(client, "adm")
    tok = csrf_of(client, "/users")
    r = client.post(f"/users/{staff.id}", data={"role": "staff", "new_password": "newpass1", "active": "1", "csrf": tok})
    assert r.status_code in (302, 303)
    session.expire_all()
    u = session.get(User, staff.id)
    assert verify_password("newpass1", u.password_hash) and u.password_plain == "newpass1"


def test_admin_deletes_user_revokes_sessions_and_keeps_history(sync, fake, session, client):
    from app import audit
    from app.security import create_session

    seeded(sync, fake, session)
    admin = add_user(session, "delete_admin", "admin")
    staff = add_user(session, "delete_crew", "staff")
    staff.password_plain = PW
    staff_id = staff.id
    location = session.scalar(select(Location))
    location.manager_user_id = staff_id  # historical references must not cause FK errors or disappear
    old_session = create_session(session, staff)
    old_cookie = old_session.id
    create_session(session, staff)
    audit.log(session, actor_type="human", actor_id=staff_id, action="example.past_action", entity_type="location", entity_id=location.id)
    session.commit()
    login(client, "delete_admin")
    tok = csrf_of(client, "/users")
    assert f'action="/users/{staff_id}/delete"' in client.get("/users").text
    response = client.post(f"/users/{staff_id}/delete", data={"csrf": tok})
    assert response.status_code == 303 and "ok=" in response.headers["location"]
    session.expire_all()
    deleted = session.get(User, staff_id)
    assert deleted.deleted_at is not None and not deleted.active and deleted.password_plain is None
    assert not list(session.scalars(select(WebSession).where(WebSession.user_id == staff_id)))
    assert session.get(Location, location.id).manager_user_id == staff_id
    event = session.scalar(select(AuditEvent).where(AuditEvent.action == "user.delete"))
    assert event.actor_id == str(admin.id) and event.entity_id == str(staff_id)
    assert "password" not in str(event.before) and "password" not in str(event.after)
    assert audit.verify_chain(session)[0]
    assert "delete_crew" not in client.get("/users").text
    assert client.post(f"/users/{staff_id}", data={"csrf": tok, "role": "admin", "active": "1"}).status_code == 404
    assert client.post(f"/users/{staff_id}/delete", data={"csrf": tok}).status_code == 404
    assert client.post("/users/999999/delete", data={"csrf": tok}).status_code == 404
    client.cookies.clear()
    client.cookies.set("rsid", old_cookie)
    assert client.get("/").status_code == 303
    assert "Wrong username or password" in login(client, "delete_crew").text


def test_user_delete_requires_csrf_and_protects_self_and_last_admin(session, client):
    admin = add_user(session, "guard_admin", "admin")
    staff = add_user(session, "guard_staff", "staff")
    login(client, "guard_admin")
    for data in ({}, {"csrf": "forged"}):
        assert client.post(f"/users/{staff.id}/delete", data=data).status_code == 403
    tok = csrf_of(client, "/users")
    assert "at least one active admin" in client.post(f"/users/{admin.id}/delete", data={"csrf": tok}, follow_redirects=True).text
    second = add_user(session, "guard_admin_two", "admin")
    assert "cannot delete your own account" in client.post(f"/users/{admin.id}/delete", data={"csrf": tok}, follow_redirects=True).text
    session.expire_all()
    assert session.get(User, admin.id).active and session.get(User, admin.id).deleted_at is None
    assert session.get(User, staff.id).deleted_at is None
    assert client.post(f"/users/{second.id}/delete", data={"csrf": tok}).status_code == 303
    session.expire_all()
    assert session.get(User, second.id).deleted_at is not None


@pytest.mark.parametrize("role", ["staff", "store_manager", "warehouse", "management"])
def test_non_admin_cannot_delete_users_or_create_suppliers(session, client, role):
    target = add_user(session, "protected_user", "staff")
    add_user(session, "unauthorized", role)
    login(client, "unauthorized")
    tok = csrf_of(client, "/")
    assert client.post(f"/users/{target.id}/delete", data={"csrf": tok}).status_code == 403
    assert client.post("/suppliers/new", data={"name": "Forbidden supplier", "csrf": tok}).status_code == 403
    session.expire_all()
    assert session.get(User, target.id).deleted_at is None


def test_store_user_only_sees_their_own_brief(sync, fake, session):
    seeded(sync, fake, session)
    scoped = add_user(session, "st", "store_manager", locs=[1])
    hq = add_user(session, "hq", "management")
    db = session
    db.add(AiOutput(kind="brief", user_id=None, status="ok", output_json={"answer": []}))
    db.add(AiOutput(kind="brief", user_id=scoped.id, status="ok", output_json={"answer": []}))
    db.flush()
    assert ai_service.latest_brief(db, scoped).user_id == scoped.id
    assert ai_service.latest_brief(db, hq).user_id is None  # HQ sees the company-wide brief


def test_notifications_respect_page_permissions(sync, fake, session):
    from app.services import notifications
    seeded(sync, fake, session)
    scoped = add_user(session, "sm", "store_manager", locs=[1])
    hq = add_user(session, "mgmt", "management")
    db = session
    notifications.notify(db, role="store_manager", location_id=1, title="Issue opened", link="/issues/1", dedupe_key="t1")
    notifications.notify(db, role="store_manager", location_id=1, title="Stock event", link="/stock-events", dedupe_key="t2")
    notifications.notify(db, user_id=scoped.id, title="Personal note", link="/inventory", dedupe_key="t3")
    titles = {n.title for n in notifications.visible_for(db, scoped, 50)}
    assert "Issue opened" not in titles  # /issues is denied for store-scoped users
    assert "Personal note" in titles and "Stock event" in titles
    hq_titles = {n.title for n in notifications.visible_for(db, hq, 50)}
    assert "Issue opened" not in hq_titles  # role-targeted at store_manager, not management
    from app.security import can_open
    assert can_open(hq, "/issues/1") and not can_open(scoped, "/issues/1") and can_open(scoped, "/inventory")


def test_staff_can_run_replenishment_and_dashboard_links_are_openable(sync, fake, session, client):
    from app.models import AuditEvent, ReplRun
    from app.services import inspections as inspsvc, replenishment
    from app.timeutil import today_local
    seeded(sync, fake, session)
    replenishment.run(session)
    inspsvc.generate_tasks(session, today_local())
    session.commit()
    staff = add_user(session, "crew2", "staff", locs=[1])
    login(client, "crew2")
    html = client.get("/replenishment").text
    assert 'action="/replenishment/run"' in html
    tok = csrf_of(client, "/replenishment")
    r = client.post("/replenishment/run", data={"csrf": tok})
    assert r.status_code in (302, 303) and "err=" not in r.headers["location"]
    session.expire_all()
    run = session.scalar(select(ReplRun).order_by(ReplRun.id.desc()))
    assert run.status == "ok" and run.counts["updated"] > 0
    event = session.scalar(select(AuditEvent).where(AuditEvent.action == "repl.run").order_by(AuditEvent.id.desc()))
    assert event.actor_type == "human" and event.actor_id == str(staff.id)
    html = client.get("/").text
    assert 'href="/issues' not in html and 'href="/sync"' not in html  # no dead-end attention items
    assert "checklist(s) still to do today" in html or "checklists are late or were not sent today" in html


@pytest.mark.parametrize("action, expected", [("approve", "approved"), ("modify", "modified"), ("reject", "rejected")])
def test_staff_can_decide_replenishment_within_limit(sync, fake, session, client, action, expected):
    from sqlalchemy import select as sa_select
    from app.models import Recommendation
    from app.services import replenishment
    seeded(sync, fake, session)
    replenishment.run(session)
    session.commit()
    rec = session.scalar(sa_select(Recommendation).where(Recommendation.status == "pending"))
    assert rec is not None
    staff = add_user(session, "crew3", "staff", locs=[rec.location_id])
    login(client, "crew3")
    assert f'action="/replenishment/{rec.id}/decide"' in client.get("/replenishment").text
    tok = csrf_of(client, "/replenishment")
    r = client.post(f"/replenishment/{rec.id}/decide", data={"action": action, "qty": str(rec.final_qty) if action == "approve" else "4", "reason": "Other", "csrf": tok})
    assert r.status_code in (302, 303) and "err=" not in r.headers.get("location", "")
    session.expire_all()
    assert session.get(Recommendation, rec.id).status == expected


def test_staff_decisions_keep_location_scope_and_approval_limit(sync, fake, session, client):
    from app.models import Location, Recommendation
    from app.services import replenishment
    seeded(sync, fake, session)
    replenishment.run(session)
    rec = session.scalar(select(Recommendation).where(Recommendation.status == "pending"))
    other = session.scalar(select(Location).where(Location.id != rec.location_id, Location.monitored.is_(True)))
    add_user(session, "crew_outside", "staff", locs=[other.id])
    add_user(session, "crew_limit", "staff", locs=[rec.location_id])
    login(client, "crew_outside")
    tok = csrf_of(client, "/replenishment")
    assert client.post(f"/replenishment/{rec.id}/decide", data={"action": "approve", "csrf": tok}).status_code == 403
    login(client, "crew_limit")
    tok = csrf_of(client, "/replenishment")
    r = client.post(f"/replenishment/{rec.id}/decide", data={"action": "modify", "qty": "1000000", "reason": "Other", "csrf": tok})
    assert "err=" in r.headers["location"]
    session.expire_all()
    assert session.get(Recommendation, rec.id).status == "pending"


def test_inspection_location_filter_is_hidden_for_staff(sync, fake, session, client):
    from app.models import Location
    seeded(sync, fake, session)
    locations = list(session.scalars(select(Location).where(Location.monitored.is_(True))))
    assert len(locations) > 1
    add_user(session, "crew_inspections", "staff", locs=[l.id for l in locations])
    add_user(session, "manager_inspections", "store_manager")
    login(client, "crew_inspections")
    html = client.get("/inspections").text
    assert 'name="loc"' not in html and "All locations" not in html
    assert 'class="tw insp-list"' in html
    login(client, "manager_inspections")
    html = client.get("/inspections").text
    assert 'name="loc"' in html and "All locations" in html


def test_product_setup_saves_in_the_background_without_a_page_reload(cfg, session, sync, fake, client):
    from sqlalchemy import select
    from app.models import Product
    from tests.test_web import add_user, csrf_of, login
    sync.sync_stores(); sync.sync_products(); session.expire_all()
    add_user(session, "prod_admin", "admin"); login(client, "prod_admin")
    p = session.scalar(select(Product).where(Product.storehub_id == "P1"))
    page = client.get("/products").text
    assert "X-Requested-With" in page and f'action="/products/{p.id}"' in page  # the page saves rows with a background request
    assert "data-nobusy" in page  # the shared "sending…" spinner is for forms that reload the page; these rows show their own spinner only while saving
    csrf = csrf_of(client, "/products")
    r = client.post(f"/products/{p.id}", data={"csrf": csrf, "supplier": "0", "case_pack": "24", "order_multiple": "12", "moq": "", "safety_days": "", "max_stock": "", "back": "/products"},
                    headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200 and r.json() == {"ok": True, "name": p.name}  # JSON, not a redirect to a reloaded page
    session.expire_all()
    assert session.get(Product, p.id).case_pack == 24 and session.get(Product, p.id).order_multiple == 12
    plain = client.post(f"/products/{p.id}", data={"csrf": csrf, "supplier": "0", "case_pack": "6", "back": "/products"}, follow_redirects=False)
    assert plain.status_code == 303  # without JavaScript the old form still works
    assert client.post(f"/products/{p.id}", data={"csrf": "wrong", "case_pack": "1"}, headers={"X-Requested-With": "fetch"}).status_code in (400, 403)


def test_tab_bars_switch_in_place_script_is_loaded(cfg, session, client):
    from tests.test_web import add_user, login
    add_user(session, "tab_admin", "admin"); login(client, "tab_admin")
    for url in ("/settings", "/suppliers", "/products", "/users", "/issues", "/purchase-orders"):
        page = client.get(url).text
        assert 'class="tabs' in page and "/static/tabs.js" in page and "/static/app.js" in page, url  # every tab bar gets the in-place switching
    js = client.get("/static/tabs.js")
    assert js.status_code == 200 and "tab-pill" in js.text and "RetailOSEnhance" in js.text
    assert "RetailOSEnhance" in client.get("/static/app.js").text  # swapped-in content gets the same behaviours as a freshly loaded page


def test_product_settings_shows_cost_and_retail_price_and_keeps_removed_settings(cfg, session, sync, fake, client):
    import re
    from sqlalchemy import select
    from app.models import Product
    from tests.test_web import add_user, csrf_of, login
    sync.sync_stores(); sync.sync_products(); session.expire_all()
    add_user(session, "prod_cols", "admin"); login(client, "prod_cols")
    p = session.scalar(select(Product).where(Product.storehub_id == "P1"))  # StoreHub: cost 12.0, price 20.0 (without tax)
    p.order_multiple, p.moq, p.safety_days, p.max_stock = 6.0, 24.0, 3.0, 90.0
    session.commit()
    page = client.get("/products").text
    for gone in ("Multiple", ">MOQ<", "Safety days", "Max stock", 'name="order_multiple"', 'name="moq"', 'name="safety_days"', 'name="max_stock"'):
        assert gone not in page, gone
    assert ">Cost<" in page and ">Retail price<" in page
    row = re.search(r"<tr><td style=\"display:none\">.*?Cola 500ml.*?</tr>", page, re.S).group(0)
    assert ">12.00<" in row and ">22.40<" in row  # cost as given; retail price = the tax-free price 20.00 plus 12% tax
    r = client.post(f"/products/{p.id}", data={"csrf": csrf_of(client, "/products"), "supplier": "0", "case_pack": "24", "back": "/products"}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    session.expire_all()
    q = session.get(Product, p.id)  # saving from this page leaves the settings it no longer shows exactly as they were
    assert (q.case_pack, q.order_multiple, q.moq, q.safety_days, q.max_stock) == (24.0, 6.0, 24.0, 3.0, 90.0)


def test_product_setup_export_matches_the_table_and_still_uploads(cfg, session, sync, fake, client):
    import csv, io
    from sqlalchemy import select
    from app.models import Product
    from tests.test_web import add_user, csrf_of, login
    sync.sync_stores(); sync.sync_products(); session.expire_all()
    add_user(session, "prod_csv", "admin"); login(client, "prod_csv")
    p = session.scalar(select(Product).where(Product.storehub_id == "P1"))
    p.case_pack, p.order_multiple, p.moq = 24.0, 6.0, 48.0
    session.commit()
    r = client.get("/products?export=csv")
    rows = list(csv.reader(io.StringIO(r.text.lstrip("﻿"))))
    assert rows[0] == ["StoreHub ID", "Name", "Barcode", "Cost", "Retail price", "Supplier", "Case pack", "Perishable", "Replenish"]
    cola = next(x for x in rows if x[1] == "Cola 500ml")
    assert cola[0] == "P1" and cola[2] == "111" and cola[3] == "12.00" and cola[4] == "22.40" and cola[6] == "24"  # cost, retail price (20.00 + 12% tax), case pack
    # the file can be edited and uploaded back: the case pack changes, the settings that are not in the file stay as they were
    edited = [rows[0]] + [[*x[:6], "36", *x[7:]] if x[0] == "P1" else x for x in rows[1:]]
    out = io.StringIO(); csv.writer(out).writerows(edited)
    up = client.post("/products/import", data={"csrf": csrf_of(client, "/products")}, files={"file": ("p.csv", out.getvalue(), "text/csv")})
    assert up.status_code == 303 and "err=" not in up.headers["location"]
    session.expire_all()
    q = session.get(Product, p.id)
    assert (q.case_pack, q.order_multiple, q.moq) == (36.0, 6.0, 48.0)
