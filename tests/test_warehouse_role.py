"""Warehouse operational access and server-side administration exclusions."""
from sqlalchemy import select

from app.models import AiOutput, Inspection, Location, Recommendation
from app.security import PERMS, allowed_location_ids, can_open, has_perm
from app.services import inspections, replenishment
from app.timeutil import today_local
from tests.test_web import add_user, client, csrf_of, login, seeded


def test_warehouse_has_admin_permissions_except_settings_and_audit(session):
    warehouse = add_user(session, "warehouse_permissions", "warehouse")
    admin = add_user(session, "admin_permissions", "admin")
    denied = {"settings.manage", "users.manage", "audit.view", "transfer.confirm"}  # the warehouse never confirms its own deliveries
    assert {p for p in PERMS if has_perm(admin, p) and not has_perm(warehouse, p)} == denied
    assert all(not has_perm(warehouse, p) for p in denied)
    assert all(not can_open(warehouse, path) for path in ("/settings", "/users", "/suppliers", "/products", "/audit"))


def test_warehouse_pages_navigation_and_administration_guards(sync, fake, session, client):
    seeded(sync, fake, session)
    replenishment.run(session)
    session.commit()
    add_user(session, "warehouse_pages", "warehouse")
    login(client, "warehouse_pages")
    for path in ("/", "/sales", "/sales/products", "/performance", "/replenishment",
                 "/replenishment/batches", "/inventory", "/purchase-orders",
                 "/stock-transfers", "/counts", "/stock-events", "/inspections", "/issues",
                 "/expiry", "/compliance", "/data-quality", "/sync", "/assistant", "/assistant/metrics"):
        assert client.get(path).status_code == 200, path
    html = client.get("/").text
    for path in ("/sales", "/performance", "/replenishment", "/sync"):
        assert f'href="{path}"' in html
    for path in ("/settings", "/users", "/suppliers", "/products", "/audit"):
        assert f'href="{path}"' not in html
        assert client.get(path).status_code == 403, path
    token = csrf_of(client)
    for path, data in (
        ("/settings/value", {"key": "app.company", "value": "Forbidden"}),
        ("/settings/locations/1", {"monitored": "1"}),
        ("/users/new", {"username": "forbidden", "password": "testpass", "role": "admin"}),
        ("/suppliers/new", {"name": "Forbidden supplier"}),
        ("/sync/backup", {}),
    ):
        assert client.post(path, data={**data, "csrf": token}).status_code == 403, path


def test_warehouse_can_refresh_and_decide_replenishment(sync, fake, session, client):
    seeded(sync, fake, session)
    add_user(session, "warehouse_orders", "warehouse")
    login(client, "warehouse_orders")
    token = csrf_of(client, "/replenishment")
    response = client.post("/replenishment/run", data={"csrf": token})
    assert response.status_code == 303 and "err=" not in response.headers["location"]
    session.expire_all()
    rec = session.scalar(select(Recommendation).where(Recommendation.status == "pending"))
    assert rec is not None
    html = client.get("/replenishment").text
    assert f'action="/replenishment/{rec.id}/decide"' in html
    response = client.post(f"/replenishment/{rec.id}/decide", data={"action": "approve", "csrf": token})
    assert response.status_code == 303 and "err=" not in response.headers["location"]
    session.expire_all()
    assert session.get(Recommendation, rec.id).status == "approved"


def test_warehouse_location_scope_is_preserved(sync, fake, session, client):
    seeded(sync, fake, session)
    replenishment.run(session)
    rec = session.scalar(select(Recommendation).where(Recommendation.status == "pending"))
    other = session.scalar(select(Location).where(Location.monitored.is_(True), Location.id != rec.location_id))
    warehouse = add_user(session, "warehouse_scoped", "warehouse", locs=[other.id])
    assert allowed_location_ids(session, warehouse) == [other.id]
    login(client, "warehouse_scoped")
    token = csrf_of(client, "/replenishment")
    assert client.post(f"/replenishment/{rec.id}/decide", data={"action": "approve", "csrf": token}).status_code == 403


def test_warehouse_can_open_assistant_outputs_like_admin(session, client):
    owner = add_user(session, "output_owner", "staff")
    add_user(session, "warehouse_output", "warehouse")
    output = AiOutput(kind="chat", user_id=owner.id, status="ok", output_json={"answer": []},
                      validation={"statements": 0, "unsupported": 0, "derived": 0})
    session.add(output)
    session.commit()
    login(client, "warehouse_output")
    assert client.get(f"/assistant/output/{output.id}").status_code == 200


def test_warehouse_can_start_inspection_without_pos_clock_in(sync, session):
    sync.sync_stores()
    sync.sync_employees()
    inspections.generate_tasks(session, today_local())
    session.commit()
    warehouse = add_user(session, "warehouse_inspection", "warehouse")
    warehouse.storehub_employee_id = "E1"
    insp = session.scalar(select(Inspection).join(Inspection.template).where(Inspection.template.has(code="warehouse_daily")))
    assert inspections.clocked_in(session, warehouse) is False
    inspections.start(session, insp, warehouse)
    assert insp.status == "in_progress" and insp.assigned_to == warehouse.id
