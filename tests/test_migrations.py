"""Schema versioning: an older database is upgraded in place, a new one is stamped current."""
from sqlalchemy import inspect, select, text

from app.db import get_engine
from app.dbinit import CURRENT_VERSION, init_db
from app.models import SchemaVersion
from app.db import SessionLocal


def cols():
    return {c["name"] for c in inspect(get_engine()).get_columns("inspection")}


def test_fresh_database_is_stamped_current(cfg):
    with SessionLocal() as db:
        assert db.scalar(select(SchemaVersion.version).order_by(SchemaVersion.version.desc())) == CURRENT_VERSION


def test_v10_inspection_photos_survive_multi_photo_migration(cfg, sync, session):
    from app.models import Inspection, InspectionResult
    from app.services import inspections
    from app.timeutil import today_local
    sync.sync_stores()
    inspections.generate_tasks(session, today_local())
    session.flush()
    inspection = session.scalar(select(Inspection))
    result = InspectionResult(inspection_id=inspection.id, item_id=inspection.template.items[0].id, photo_path="2026/10/old.png")
    session.add(result)
    session.commit()
    result_id = result.id
    with get_engine().begin() as connection:
        connection.execute(text("ALTER TABLE inspection_result DROP COLUMN photo_paths"))
        connection.execute(text("DELETE FROM schema_version WHERE version >= 11"))
    init_db()
    init_db()
    assert "photo_paths" in {c["name"] for c in inspect(get_engine()).get_columns("inspection_result")}
    with get_engine().connect() as connection:
        row = connection.execute(text("SELECT photo_path, photo_paths FROM inspection_result WHERE id=:id"), {"id": result_id}).one()
        assert row.photo_path == "2026/10/old.png" and row.photo_paths is None
    assert "created_at" in cols()


def test_old_database_is_migrated_and_data_survives(cfg):
    with get_engine().begin() as c:
        c.execute(text("ALTER TABLE inspection DROP COLUMN created_at"))
        c.execute(text("ALTER TABLE recommendation DROP COLUMN received_qty"))
        c.execute(text("ALTER TABLE order_batch DROP COLUMN received_at"))
        c.execute(text("DELETE FROM schema_version WHERE version >= 2"))
    assert "created_at" not in cols()
    init_db()
    assert "created_at" in cols()
    assert "received_qty" in {x["name"] for x in inspect(get_engine()).get_columns("recommendation")}
    assert "received_at" in {x["name"] for x in inspect(get_engine()).get_columns("order_batch")}
    init_db()  # running again is harmless
    with SessionLocal() as db:
        assert db.scalar(select(SchemaVersion.version).order_by(SchemaVersion.version.desc())) == CURRENT_VERSION


def test_v9_users_gain_deletion_marker_without_losing_accounts(cfg):
    from app.models import User
    with SessionLocal() as db:
        db.add(User(username="migration_user", role="staff", password_hash="existing-hash"))
        db.commit()
    with get_engine().begin() as c:
        c.execute(text("ALTER TABLE user DROP COLUMN deleted_at"))
        c.execute(text("DELETE FROM schema_version WHERE version >= 10"))
        c.execute(text("INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (9, CURRENT_TIMESTAMP)"))
    init_db()
    init_db()
    assert "deleted_at" in {c["name"] for c in inspect(get_engine()).get_columns("user")}
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "migration_user"))
        assert user.active and user.deleted_at is None and user.password_hash == "existing-hash"
        assert db.scalar(select(SchemaVersion.version).order_by(SchemaVersion.version.desc())) == CURRENT_VERSION
