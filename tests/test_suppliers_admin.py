"""Manual supplier creation: permissions, validation, product assignment and CSV round-trip."""
import csv
import io

import pytest
from sqlalchemy import func, select

from app import audit
from app.models import AuditEvent, Product, Supplier
from tests.test_web import add_user, client, csrf_of, login, seeded  # noqa: F401


def test_admin_creates_supplier_and_assigns_to_product(sync, fake, session, client):
    seeded(sync, fake, session)
    admin = add_user(session, "supplier_admin", "admin")
    login(client, "supplier_admin")
    tok = csrf_of(client, "/suppliers")
    response = client.post("/suppliers/new", data={
        "csrf": tok, "name": "  New   Local Supplier  ", "contact": "orders@example.test",
        "lead": "2.5", "review": "7", "min_value": "1,200.50", "order_days": "Mon, Thu",
    })
    assert response.status_code == 303 and "ok=" in response.headers["location"]
    supplier = session.scalar(select(Supplier).where(Supplier.name == "New Local Supplier"))
    assert supplier is not None and supplier.storehub_id is None and supplier.name_source == "manual" and supplier.active
    assert (supplier.lead_time_days, supplier.review_period_days, supplier.min_order_value) == (2.5, 7, 1200.5)
    assert supplier.contact == "orders@example.test" and supplier.order_days == "Mon, Thu"
    assert f"LOCAL-{supplier.id}" in client.get("/suppliers").text
    assert f'<option value="{supplier.id}"' in client.get("/products").text
    product = session.scalar(select(Product).where(Product.storehub_id == "P1"))
    assert client.post(f"/products/{product.id}", data={"csrf": tok, "supplier": supplier.id, "replenish": "1"}).status_code == 303
    session.expire_all()
    assert session.get(Product, product.id).supplier_id == supplier.id
    assert session.get(Product, product.id).supplier_source == "manual"
    sync.sync_products()
    session.commit()
    assert session.get(Product, product.id).supplier_id == supplier.id
    event = session.scalar(select(AuditEvent).where(AuditEvent.action == "supplier.create"))
    assert event.actor_id == str(admin.id) and event.entity_id == str(supplier.id)
    assert audit.verify_chain(session)[0]


@pytest.mark.parametrize("bad", [
    {"name": "   "}, {"name": "x" * 161}, {"lead": "-1"}, {"review": "nan"},
    {"min_value": "inf"}, {"lead": "tomorrow"}, {"contact": "x" * 201}, {"order_days": "x" * 61},
])
def test_supplier_create_rejects_invalid_fields_without_writing(session, client, bad):
    add_user(session, "supplier_admin", "admin")
    login(client, "supplier_admin")
    response = client.post("/suppliers/new", data={"csrf": csrf_of(client, "/suppliers"), "name": "New supplier", **bad})
    assert response.status_code == 303 and "err=" in response.headers["location"]
    assert session.scalar(select(func.count()).select_from(Supplier)) == 0
    assert session.scalar(select(AuditEvent).where(AuditEvent.action == "supplier.create")) is None


def test_supplier_create_requires_csrf_and_rejects_duplicate_names(session, client):
    add_user(session, "supplier_admin", "admin")
    session.add(Supplier(name="Existing  Supplier", name_source="manual", active=False))
    session.commit()
    login(client, "supplier_admin")
    for token in ("", "forged"):
        assert client.post("/suppliers/new", data={"name": "New Supplier", "csrf": token}).status_code == 403
    tok = csrf_of(client, "/suppliers")
    response = client.post("/suppliers/new", data={"name": " EXISTING supplier ", "csrf": tok})
    assert "err=" in response.headers["location"]
    assert session.scalar(select(func.count()).select_from(Supplier)) == 1


def test_new_suppliers_keep_csv_name_import_usable(session, client):
    add_user(session, "supplier_admin", "admin")
    session.add(Supplier(name="Synced Supplier", storehub_id="STOREHUB-123456", name_source="auto"))
    session.commit()
    login(client, "supplier_admin")
    tok = csrf_of(client, "/suppliers")
    for name in ("Manual supplier one", "Manual supplier two"):
        assert "ok=" in client.post("/suppliers/new", data={"name": name, "csrf": tok}).headers["location"]
    rows = list(csv.reader(io.StringIO(client.get("/suppliers?export=csv").text.lstrip("\ufeff"))))
    assert len({r[0] for r in rows[1:]}) == 3 and all(r[0] for r in rows[1:])
    local = next(r for r in rows[1:] if r[1] == "Manual supplier two")
    local[5] = "Renamed local supplier"
    stream = io.StringIO()
    csv.writer(stream).writerows(rows)
    response = client.post("/suppliers/import", data={"csrf": tok}, files={"file": ("suppliers.csv", stream.getvalue(), "text/csv")})
    assert response.status_code == 303 and "ok=" in response.headers["location"]
    session.expire_all()
    assert session.scalar(select(Supplier).where(Supplier.name == "Renamed local supplier")) is not None
    assert session.scalar(select(Supplier).where(Supplier.name == "Manual supplier one")) is not None


def test_new_settings_controls_and_responses_are_translated(session, client):
    add_user(session, "supplier_admin", "admin")
    staff = add_user(session, "translated_staff", "staff")
    login(client, "supplier_admin")
    client.cookies.set("lang", "zh")
    html = client.get("/suppliers").text
    assert "添加供应商" in html and "创建供应商" in html and "供应商名称" in html
    tok = csrf_of(client, "/suppliers")
    response = client.post("/suppliers/new", data={"name": "测试供应商", "csrf": tok}, follow_redirects=True)
    assert "已添加供应商 测试供应商" in response.text
    response = client.post("/suppliers/new", data={"name": "测试供应商", "csrf": tok}, follow_redirects=True)
    assert "该供应商已存在" in response.text
    users_html = client.get("/users").text
    assert "删除用户" in users_html and "将移除其访问权限，并保留历史记录" in users_html
    response = client.post(f"/users/{staff.id}/delete", data={"csrf": tok}, follow_redirects=True)
    assert "已删除用户 translated_staff" in response.text
