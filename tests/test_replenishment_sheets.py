import re
import pytest
from sqlalchemy import func, select, text

from app.models import Location, Recommendation, TransferSheet
from app.services import replenishment, replenishment_sheets, transfer_sheets
from tests.test_services import seeded
from tests.test_web import add_user, client, csrf_of, login


def setup_recs(session, sync, fake):
    seeded(sync, fake, session)
    replenishment.run(session)
    session.commit()
    return session.scalar(select(Recommendation).where(
        Recommendation.source_kind == "transfer", Recommendation.status == "pending"))


def test_save_stays_pending_and_proceed_creates_new_sheet_once(client, session, sync, fake):
    rec = setup_recs(session, sync, fake)
    user = add_user(session, "sheet-admin", "admin")
    existing = transfer_sheets.create(session, user, rec.location_id)
    session.commit()
    login(client, user.username)
    csrf = csrf_of(client, "/replenishment")
    response = client.post(f"/replenishment/{rec.id}/decide", data={"csrf": csrf, "action": "save", "qty": "17"},
                           headers={"X-Requested-With": "fetch"})
    assert response.status_code == 200 and response.json()["quantity"] == 17
    session.refresh(rec)
    assert rec.saved_qty == 17 and rec.status == "pending" and rec.approved_qty is None
    replenishment.run(session)
    session.commit()
    session.refresh(rec)
    assert rec.saved_qty == 17
    response = client.post("/replenishment/transfer-sheets", data={"csrf": csrf, "ids": [rec.id, rec.id]})
    assert response.status_code == 303 and response.headers["location"].startswith("/transfer-sheets/")
    session.refresh(rec)
    sheet = session.get(TransferSheet, rec.transfer_sheet_id)
    assert sheet.id != existing.id and sheet.status == "draft"
    assert sheet.target_location_id == rec.location_id
    assert [(line.product_id, line.qty) for line in sheet.lines] == [(rec.product_id, 17)]
    assert rec.status == "ordered" and rec.approved_qty == 17
    count = session.scalar(select(func.count()).select_from(TransferSheet))
    repeat = client.post("/replenishment/transfer-sheets", data={"csrf": csrf, "ids": [rec.id]})
    assert "err=" in repeat.headers["location"]
    assert session.scalar(select(func.count()).select_from(TransferSheet)) == count
    assert replenishment.create_batches(session, user) == []


def test_group_by_store_and_rollback_invalid_selection(client, session, sync, fake):
    rec = setup_recs(session, sync, fake)
    other_store = session.scalar(select(Location).where(Location.storehub_id == "S_B"))
    other = Recommendation(run_id=rec.run_id, product_id=rec.product_id, location_id=other_store.id,
                           business_date=rec.business_date, calc_version=rec.calc_version, source_kind="transfer",
                           final_qty=8, status="pending", unit_cost=rec.unit_cost, explanation=rec.explanation)
    session.add(other)
    user = add_user(session, "group-admin", "admin")
    login(client, user.username)
    csrf = csrf_of(client, "/replenishment")
    other.status = "needs_review"
    session.commit()
    response = client.post("/replenishment/transfer-sheets", data={"csrf": csrf, "ids": [rec.id, other.id]})
    assert "err=" in response.headers["location"]
    assert session.scalar(select(func.count()).select_from(TransferSheet)) == 0
    session.refresh(rec)
    assert rec.status == "pending" and rec.transfer_sheet_id is None
    other.status = "pending"
    session.commit()
    response = client.post("/replenishment/transfer-sheets", data={"csrf": csrf, "ids": [rec.id, other.id]})
    assert response.headers["location"].startswith("/transfer-sheets?")
    sheets = list(session.scalars(select(TransferSheet)))
    assert {s.target_location_id for s in sheets} == {rec.location_id, other.location_id}
    assert len(sheets) == 2 and all(len(s.lines) == 1 for s in sheets)


@pytest.mark.parametrize("qty", ["", "0", "-1", "1.5", "nan", "inf", "100001"])
def test_save_rejects_invalid_quantities(client, session, sync, fake, qty):
    rec = setup_recs(session, sync, fake)
    add_user(session, "qty-admin", "admin")
    login(client, "qty-admin")
    response = client.post(f"/replenishment/{rec.id}/decide",
                           data={"csrf": csrf_of(client), "action": "save", "qty": qty},
                           headers={"X-Requested-With": "fetch"})
    assert response.status_code == 400
    session.refresh(rec)
    assert rec.saved_qty is None and rec.status == "pending"


def test_csrf_and_scope_and_transfer_permission(client, session, sync, fake):
    rec = setup_recs(session, sync, fake)
    other_store = session.scalar(select(Location).where(Location.storehub_id == "S_B"))
    add_user(session, "scoped", "staff", locs=[other_store.id])
    login(client, "scoped")
    csrf = csrf_of(client)
    assert client.post(f"/replenishment/{rec.id}/decide", data={"csrf": csrf, "action": "save", "qty": 5}).status_code == 403
    assert client.post("/replenishment/transfer-sheets", data={"csrf": csrf, "ids": [rec.id]}).status_code == 403
    add_user(session, "csrf-admin", "admin")
    login(client, "csrf-admin")
    assert client.post("/replenishment/transfer-sheets", data={"ids": [rec.id]}).status_code == 403
    assert session.scalar(select(func.count()).select_from(TransferSheet)) == 0


def test_v38_migration_is_repeatable(cfg, session):
    from app.dbinit import init_db
    schema = session.scalar(text("SELECT sql FROM sqlite_master WHERE name = 'recommendation'"))
    schema = re.sub(r'\s*saved_qty FLOAT,', '', schema)
    schema = re.sub(r'\s*transfer_sheet_id INTEGER,', '', schema)
    schema = re.sub(r',\s*FOREIGN KEY\(transfer_sheet_id\) REFERENCES transfer_sheet \(id\) ON DELETE SET NULL', '', schema)
    session.execute(text("DROP TABLE recommendation"))
    session.execute(text(schema))
    session.execute(text("DELETE FROM schema_version WHERE version = 38"))
    session.commit()
    init_db()
    init_db()
    cols = {r[1] for r in session.execute(text("PRAGMA table_info(recommendation)"))}
    assert {"saved_qty", "transfer_sheet_id"} <= cols
