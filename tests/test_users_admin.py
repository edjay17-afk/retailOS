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
