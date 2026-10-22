"""Background jobs and their schedule. One scheduler inside the app process; each job is idempotent and logged."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from . import audit
from .config import get_config
from .db import SessionLocal
from .models import Recommendation
from .services import (backup, brief, counts, dq, expiry, inspections, issues, notifications, receiving, po_prices, replenishment, sales_profit, stock_events, transfer_inbox, transfer_sheets)
from .storehub.client import AuthError
from .storehub.sync import SyncService
from .timeutil import now_utc, today_local

log = logging.getLogger("retailos.jobs")
_sync = SyncService()
_scheduler: BackgroundScheduler | None = None


def _safe(name: str):
    """Decorator: a failing job must never stop the scheduler."""
    def deco(fn):
        def wrapper(*a, **kw):
            try:
                return fn(*a, **kw)
            except AuthError:
                log.error("job %s: StoreHub rejected our credentials", name)
            except Exception:  # noqa: BLE001
                log.exception("job %s failed", name)
        wrapper.__name__ = name
        return wrapper
    return deco


def _db_job(fn):
    with SessionLocal() as db:
        out = fn(db)
        db.commit()
        return out


@_safe("sync_sales")
def sync_sales(): _sync.sync_transactions()
@_safe("sync_inventory")
def sync_inventory(): _sync.sync_inventory(); _db_job(lambda db: (receiving.verify_with_storehub(db), stock_events.verify_with_sync(db), counts.reconcile_events(db)))
@_safe("sync_pos")
def sync_pos(): _sync.sync_purchase_orders(); _db_job(lambda db: replenishment.sync_batches_with_pos(db))
@_safe("sync_staff")
def sync_staff(): _sync.sync_employees()
@_safe("sync_people")
def sync_people(): _sync.sync_timesheets()
@_safe("sync_reference")
def sync_reference(): _sync.sync_stores(); _sync.sync_employees()
@_safe("sync_products")
def sync_products(): _sync.sync_products()
@_safe("reconcile")
def reconcile(): _sync.reconcile()
@_safe("snapshot")
def snapshot(): _sync.snapshot_inventory()
@_safe("dq")
def run_dq(): _db_job(lambda db: dq.run_all(db))
@_safe("escalate")
def escalate(): _db_job(lambda db: (issues.escalate_overdue(db), inspections.flag_missed(db)))
@_safe("inspection_tasks")
def inspection_tasks(): _db_job(lambda db: inspections.generate_tasks(db, today_local()))
@_safe("expiry")
def expiry_check(): _db_job(lambda db: expiry.check(db))
@_safe("replenish")
def replenish(): _db_job(lambda db: replenishment.ensure_current(db))
@_safe("outcomes")
def outcomes(): _db_job(lambda db: replenishment.track_outcomes(db))
@_safe("backup")
def do_backup(): backup.backup_now()
@_safe("transfer_inbox")
def import_transfer_inbox():
    out = _db_job(transfer_inbox.process_inbox)
    if out and out.get("files"):
        log.info("transfer inbox: %s", out)
    _db_job(lambda db: transfer_sheets.match_storehub(db))  # a warehouse sheet is linked to the StoreHub transfer that carries the same goods


@_safe("transfer_robot")
def run_transfer_robot():
    def go(db):
        if not transfer_inbox.robot_due(db):  # checked every minute; fetch only when the configured interval has elapsed
            return {"ran": False}
        transfer_inbox.mark_robot_attempt(db)
        db.commit()
        return transfer_inbox.run_robot(db)
    out = _db_job(go)
    if out and out.get("ran"):
        log.info("stock transfer robot: %s", out)


@_safe("sales_profit")
def run_sales_profit():
    """StoreHub's Gross Profit per day (read from its Sales over time report); checked every 15 minutes, it only runs every few hours."""
    def go(db):
        if not sales_profit.due(db):
            return {"ran": False}
        return sales_profit.run(db)
    out = _db_job(go)
    if out and out.get("ran"):
        log.info("gross profit report: %s", out)


@_safe("po_prices")
def run_po_prices():
    """The exact supplier prices of purchase orders (read from StoreHub's own PO pages); checked every 15 minutes, it only runs when a PO still lacks them."""
    def go(db):
        if not po_prices.due(db):
            return {"ran": False}
        return po_prices.run(db)
    out = _db_job(go)
    if out and out.get("ran"):
        log.info("purchase order prices: %s", out)


@_safe("transfer_fresh")
def check_transfer_freshness(): _db_job(transfer_inbox.check_freshness)


@_safe("lark")
def mirror_lark():
    from . import lark
    out = lark.mirror()
    if out.get("enabled") and (out.get("created") or out.get("updated") or out.get("errors")):
        log.info("lark mirror: %s", out)


@_safe("daily_brief")
def daily_brief_note():
    def f(db):
        from sqlalchemy import select
        from .models import Location
        ids = list(db.scalars(select(Location.id).where(Location.monitored.is_(True))))
        b = brief.build(db, ids)
        body = "\n".join(f"- {i['title']}" for i in b["items"][:8]) or "Nothing needs management action today."
        notifications.notify(db, role="management", title=f"Daily brief {b['date']}: {len(b['items'])} item(s)", body=body, link="/",
                             dedupe_key=f"brief:{b['date']}")
    _db_job(f)


@_safe("lark_alerts")
def lark_alerts():
    from . import lark
    out = lark.forward_alerts()
    if out.get("enabled") and (out.get("sent") or out.get("errors")):
        log.info("lark alerts: %s", out)


@_safe("compliance")
def compliance_check():
    from .services import compliance
    _db_job(lambda db: compliance.check(db))


@_safe("ai_brief")
def ai_brief():
    """Generate the AI management brief and tell management. Skipped quietly when AI is off or over its daily limit."""
    from .ai import service as ai
    with SessionLocal() as db:
        try:
            row = ai.generate_brief(db, None)
            db.commit()
        except ai.AiUnavailable as e:
            log.info("ai brief skipped: %s", e)
            return
        if row.status == "ok":
            notifications.notify(db, role="management", title="AI brief: " + str((row.output_json or {}).get("headline", ""))[:200], link="/assistant",
                                 dedupe_key=f"aibrief:{row.id}")
            db.commit()


def recover_interrupted() -> dict:
    """Runs once at start-up. Only one copy of the system runs, so any job lock or 'running' record left over from a
    previous process (restart, crash, power cut) is dead: release it so the syncs resume at once instead of waiting
    for the lock to expire, and mark the cut-off runs as failed so the history is honest."""
    from sqlalchemy import delete, update
    from .models import JobLock, SyncRun
    with SessionLocal() as db:
        locks = db.execute(delete(JobLock)).rowcount or 0
        runs = db.execute(update(SyncRun).where(SyncRun.status == "running").values(status="failed", finished_at=now_utc(), message="interrupted by a restart")).rowcount or 0
        db.commit()
    if locks or runs:
        log.info("start-up recovery: released %d job lock(s), closed %d interrupted run(s)", locks, runs)
    return {"locks": locks, "runs": runs}


def start() -> BackgroundScheduler | None:
    global _scheduler
    cfg = get_config()
    if not cfg.scheduler_enabled or _scheduler is not None:
        return _scheduler
    recover_interrupted()
    tzname = ZoneInfo("Asia/Manila")
    s = BackgroundScheduler(timezone=tzname, job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300})
    soon = lambda sec: datetime.now() + timedelta(seconds=sec)  # noqa: E731
    # data syncs (spec 6.1 defaults); first run shortly after start-up so a restart catches up at once
    s.add_job(sync_reference, "interval", hours=6, next_run_time=soon(2), id="reference")
    s.add_job(sync_products, "interval", minutes=1, next_run_time=soon(8), id="products")  # one job also handles the initial catch-up
    s.add_job(sync_inventory, "interval", minutes=1, next_run_time=soon(20), id="inventory")
    s.add_job(sync_pos, "interval", minutes=1, next_run_time=soon(40), id="pos")
    s.add_job(sync_sales, "interval", minutes=1, next_run_time=soon(60), id="sales")
    s.add_job(sync_staff, "interval", minutes=1, next_run_time=soon(30), id="staff")
    s.add_job(sync_people, "interval", minutes=1, next_run_time=soon(50), id="people")
    s.add_job(run_dq, "interval", minutes=10, next_run_time=soon(150), id="dq")
    s.add_job(escalate, "interval", minutes=5, next_run_time=soon(30), id="escalate")
    s.add_job(inspection_tasks, "cron", minute=5, id="inspection_tasks")
    s.add_job(inspection_tasks, "date", run_date=soon(10), id="inspection_tasks_boot")
    s.add_job(reconcile, "cron", hour=2, minute=30, id="reconcile")
    s.add_job(snapshot, "cron", hour=23, minute=55, id="snapshot")
    s.add_job(snapshot, "date", run_date=soon(200), id="snapshot_boot")
    s.add_job(expiry_check, "cron", hour=6, minute=30, id="expiry")
    s.add_job(replenish, "interval", minutes=5, next_run_time=soon(90), id="replenish")
    s.add_job(outcomes, "cron", hour=4, id="outcomes")
    s.add_job(daily_brief_note, "cron", hour=6, minute=45, id="brief")
    s.add_job(ai_brief, "cron", hour="6,16", minute=15, id="ai_brief")
    s.add_job(do_backup, "cron", hour="1,13", minute=45, id="backup")
    s.add_job(mirror_lark, "interval", minutes=5, next_run_time=soon(240), id="lark_mirror")
    s.add_job(lark_alerts, "interval", minutes=2, next_run_time=soon(260), id="lark_alerts")
    s.add_job(import_transfer_inbox, "interval", minutes=5, next_run_time=soon(280), id="transfer_inbox")
    s.add_job(check_transfer_freshness, "cron", minute=10, id="transfer_fresh")
    s.add_job(run_transfer_robot, "interval", minutes=1, next_run_time=soon(240), id="transfer_robot")  # due-check only; actual fetching defaults to every 20 minutes
    s.add_job(run_sales_profit, "interval", minutes=15, next_run_time=soon(330), id="sales_profit")
    s.add_job(run_po_prices, "interval", minutes=15, next_run_time=soon(420), id="po_prices")
    s.add_job(compliance_check, "cron", hour=6, minute=20, id="compliance")
    s.add_job(compliance_check, "date", run_date=soon(120), id="compliance_boot")
    s.start()
    _scheduler = s
    log.info("scheduler started with %d jobs", len(s.get_jobs()))
    return s


def stop() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def status() -> list[dict]:
    if not _scheduler:
        return []
    return [{"id": j.id, "next_run": j.next_run_time} for j in sorted(_scheduler.get_jobs(), key=lambda j: j.id)]
