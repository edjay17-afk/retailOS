"""Automatic import of StockTransfer CSV exports dropped into a folder (by the StoreHub robot, or by anyone), plus a freshness check.

The same rules as a manual import apply (plan, then apply); only the preview click is skipped. Repeat files change nothing.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import shutil
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import settings_service as cfg
from ..config import get_config
from ..models import Location, Setting, StockTransferImport, User
from ..timeutil import now_utc
from . import notifications, stock_transfers

log = logging.getLogger("retailos.transfers")


def folders():
    base = get_config().transfer_inbox
    done, failed = base / "processed", base / "failed"
    for d in (base, done, failed):
        d.mkdir(parents=True, exist_ok=True)
    return base, done, failed


def _actor(db: Session) -> User | None:
    return db.scalar(select(User).where(User.role == "admin", User.active.is_(True), User.deleted_at.is_(None)).order_by(User.id))


def _move(path, folder, note: str | None = None):
    stamp = now_utc().strftime("%Y%m%d-%H%M%S")
    target = folder / f"{stamp}-{path.name}"
    shutil.move(str(path), str(target))
    if note:
        target.with_suffix(target.suffix + ".txt").write_text(note, encoding="utf-8")


def process_inbox(db: Session) -> dict:
    """Import every CSV waiting in the inbox. Returns counts; a bad file is moved to failed/ with the reason and the team is told."""
    inbox, done, failed = folders()
    out = {"files": 0, "imported": 0, "duplicate": 0, "failed": 0}
    actor = _actor(db)
    if actor is None:
        return out
    location_ids = list(db.scalars(select(Location.id)))
    for path in sorted(inbox.glob("*.csv")):
        out["files"] += 1
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        if db.scalar(select(StockTransferImport.id).where(StockTransferImport.sha256 == sha, StockTransferImport.imported_at.is_not(None)).limit(1)):
            _move(path, done, "Already imported earlier: nothing to do.")
            out["duplicate"] += 1
            continue
        try:
            source = StockTransferImport(token=secrets.token_urlsafe(32), filename=path.name[:240], sha256=sha, raw_csv=raw, created_by=actor.id)
            db.add(source)
            db.flush()
            counts = stock_transfers.apply(db, source, actor, location_ids, via="inbox")
            db.commit()
            _move(path, done, str(counts))
            out["imported"] += 1
            log.info("stock transfers imported from %s: %s", path.name, counts)
        except (stock_transfers.ImportError, IntegrityError, ValueError) as e:
            db.rollback()
            reason = getattr(e, "key", None) and f"{e.key} {getattr(e, 'values', '')}" or str(e)
            _move(path, failed, reason)
            out["failed"] += 1
            log.error("stock transfer file %s could not be imported: %s", path.name, reason)
            for role in ("warehouse", "admin"):
                notifications.notify(db, role=role, level="warning", title="A StoreHub stock-transfer export could not be imported",
                                     body=f"{path.name}: {reason}", link="/stock-transfers", dedupe_key=f"transfer-import-failed:{sha}:{role}")
            db.commit()
    return out


def robot_due(db: Session, every_minutes: int | None = None, retry_minutes: int = 30) -> bool:
    """True when the robot should run: the last import is old enough, and the robot has not already tried in the last half hour.
    Called every 10 minutes, so after a sleep, a shutdown or a restart the robot catches up within minutes instead of waiting for the next fixed hour."""
    every = timedelta(minutes=every_minutes or get_config().robot_every_minutes)
    now = now_utc()
    last = last_import_at(db)
    if last and now - last < every:
        return False
    row = db.get(Setting, "robot.last_attempt")
    if row and row.value:
        try:
            if now - datetime.fromisoformat(str(row.value)) < timedelta(minutes=retry_minutes):
                return False
        except ValueError:
            pass
    return True


def mark_robot_attempt(db: Session) -> None:
    row = db.get(Setting, "robot.last_attempt") or Setting(key="robot.last_attempt", value="")
    row.value = now_utc().isoformat()
    db.add(row)
    db.flush()


def run_robot(db: Session, export=None) -> dict:
    """Let the robot export the StoreHub stock transfers, then import the file straight away. A failure warns the warehouse team and admins (once a day)."""
    from .. import robot
    export = export or robot.export_stock_transfers
    if not robot.configured():
        return {"ran": False}
    try:
        path = export()
    except (robot.RobotLoginFailed, robot.RobotNotConfigured, robot.RobotError) as e:
        log.error("stock transfer robot failed: %s", e)
        for role in ("warehouse", "admin"):
            notifications.notify(db, role=role, level="warning", title="The StoreHub robot could not export stock transfers", body=str(e)[:300],
                                 link="/stock-transfers", dedupe_key=f"robot-fail:{now_utc().date()}:{role}")
        db.commit()
        return {"ran": True, "ok": False, "error": str(e)}
    out = process_inbox(db)
    row = db.get(Setting, "robot.last_ok") or Setting(key="robot.last_ok", value="")
    row.value = now_utc().isoformat()  # a run that finds nothing new is still a successful, up-to-date check
    db.add(row)
    db.commit()
    return {"ran": True, "ok": True, "file": path.name, **out}


def last_import_at(db: Session) -> datetime | None:
    """When the transfers were last known to be current: the newest import, or the last successful robot export (which may have had no changes)."""
    newest = db.scalar(select(func.max(StockTransferImport.imported_at)))
    row = db.get(Setting, "robot.last_ok")
    try:
        ok = datetime.fromisoformat(str(row.value)) if row and row.value else None
    except ValueError:
        ok = None
    if ok is not None and ok.tzinfo is not None and newest is not None and newest.tzinfo is None:
        ok = ok.replace(tzinfo=None)
    return max([d for d in (newest, ok) if d is not None], default=None)


def check_freshness(db: Session) -> bool:
    """Warn the warehouse team and admins once a day while no new export has been imported for too long. True when stale."""
    last = last_import_at(db)
    if last is None:
        return False  # nothing imported yet: nothing to be stale
    hours = float(cfg.get(db, "transfers.stale_hours"))
    if now_utc() - last <= timedelta(hours=hours):
        return False
    for role in ("warehouse", "admin"):
        notifications.notify(db, role=role, level="warning", title="Stock transfers have not been updated from StoreHub",
                             body=f"The last export was imported {int((now_utc() - last).total_seconds() // 3600)} hours ago.", link="/stock-transfers",
                             dedupe_key=f"transfers-stale:{now_utc().date()}:{role}")
    return True
