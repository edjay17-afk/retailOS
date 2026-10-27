"""Command line: python -m app.cli <command>"""
from __future__ import annotations

import argparse
import secrets
import sys

from sqlalchemy import select

from . import audit
from .config import get_config
from .db import SessionLocal, init_engine
from .dbinit import init_db
from .models import User
from .security import ROLES, hash_password, password_problem


def _boot():
    init_engine()
    init_db()


def cmd_init(a):
    _boot()
    print("Database ready:", get_config().db_url)


def cmd_create_admin(a):
    _boot()
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.username == a.username.lower())):
            print(f"User '{a.username}' already exists. Use the Users page or --reset.")
            return 1
        pw = a.password or ("Tmp-" + secrets.token_urlsafe(9) + "7a")
        if a.password and password_problem(pw):
            print(password_problem(pw))
            return 1
        u = User(username=a.username.lower(), full_name=a.name or a.username, role=a.role, password_hash=hash_password(pw), must_change_password=True)
        db.add(u)
        db.flush()
        audit.log(db, actor_type="system", actor_id="cli", action="user.create", entity_type="user", entity_id=u.id, after={"username": u.username, "role": u.role})
        db.commit()
    print(f"Created {a.role} '{a.username}'. Temporary password (shown once, change it at first sign-in):\n  {pw}")


def cmd_reset_password(a):
    _boot()
    with SessionLocal() as db:
        u = db.scalar(select(User).where(User.username == a.username.lower()))
        if not u:
            print("No such user")
            return 1
        pw = "Tmp-" + secrets.token_urlsafe(9) + "7a"
        u.password_hash, u.must_change_password, u.failed_logins, u.locked_until = hash_password(pw), True, 0, None
        audit.log(db, actor_type="system", actor_id="cli", action="user.reset_password", entity_type="user", entity_id=u.id)
        db.commit()
    print(f"New temporary password for '{a.username}' (shown once):\n  {pw}")


def cmd_serve(a):
    import uvicorn
    cfg = get_config()
    uvicorn.run("app.web.main:app", host=a.host or cfg.host, port=a.port or cfg.port, log_level="info", proxy_headers=cfg.behind_proxy, forwarded_allow_ips="127.0.0.1")


def cmd_sync(a):
    _boot()
    from .storehub.sync import SyncService
    s = SyncService()
    for name, out in s.full_sync().items():
        print(f"{name:16} {out}")


def cmd_backup(a):
    _boot()
    from .services import backup
    print("Backup written:", backup.backup_now())


def cmd_lark_setup(a):
    """Create the mirror tables in the Lark Base named in LARK_BASE_TOKEN (the Lark app must be a collaborator with manage access)."""
    _boot()
    from . import lark
    cl = lark.LarkClient()
    if not cl.configured:
        print("Set LARK_APP_ID, LARK_APP_SECRET and LARK_BASE_TOKEN in .env first.")
        return 1
    ids = lark.setup(cl)
    print("Tables ready:", ids)


def cmd_lark_mirror(a):
    _boot()
    from . import lark
    print(lark.mirror())


def cmd_robot_check(a):
    """Try the robot's StoreHub sign-in (nothing is exported)."""
    from playwright.sync_api import sync_playwright
    from . import robot
    if not robot.configured():
        print("Set STOREHUB_BO_EMAIL and STOREHUB_BO_PASSWORD in .env first.")
        return 1
    with sync_playwright() as pw:
        browser, ctx = robot.open_browser(pw)
        page = ctx.new_page()
        try:
            robot.sign_in(page)
            print("Signed in to StoreHub OK:", page.url)
        except robot.RobotLoginFailed as e:
            print("Sign-in failed:", e)
            return 1
        finally:
            browser.close()


def cmd_robot_run(a):
    """Run the StoreHub robot now: export the stock transfers and import them."""
    _boot()
    from .services import transfer_inbox
    with SessionLocal() as db:
        print(transfer_inbox.run_robot(db))


def cmd_transfers_inbox(a):
    _boot()
    from .services import transfer_inbox
    with SessionLocal() as db:
        print(transfer_inbox.process_inbox(db))


def cmd_zreadings_run(a):
    """Read official Z-reading reports from StoreHub with GET requests only, then save them locally."""
    _boot()
    from .services import zreadings
    with SessionLocal() as db:
        print(zreadings.run(db))


def cmd_verify_audit(a):
    _boot()
    with SessionLocal() as db:
        ok, n, bad = audit.verify_chain(db)
    print("Audit chain OK" if ok else f"Audit chain BROKEN at event {bad}", f"({n} events checked)")
    return 0 if ok else 2


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="retailos")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=cmd_init)
    ca = sub.add_parser("create-admin")
    ca.add_argument("--username", default="admin")
    ca.add_argument("--name", default="")
    ca.add_argument("--role", default="admin", choices=ROLES)
    ca.add_argument("--password", default="")
    ca.set_defaults(fn=cmd_create_admin)
    rp = sub.add_parser("reset-password")
    rp.add_argument("username")
    rp.set_defaults(fn=cmd_reset_password)
    sv = sub.add_parser("serve")
    sv.add_argument("--host", default="")
    sv.add_argument("--port", type=int, default=0)
    sv.set_defaults(fn=cmd_serve)
    sub.add_parser("sync").set_defaults(fn=cmd_sync)
    sub.add_parser("backup").set_defaults(fn=cmd_backup)
    sub.add_parser("verify-audit").set_defaults(fn=cmd_verify_audit)
    sub.add_parser("lark-setup").set_defaults(fn=cmd_lark_setup)
    sub.add_parser("lark-mirror").set_defaults(fn=cmd_lark_mirror)
    sub.add_parser("robot-check").set_defaults(fn=cmd_robot_check)
    sub.add_parser("robot-run").set_defaults(fn=cmd_robot_run)
    sub.add_parser("zreadings-run").set_defaults(fn=cmd_zreadings_run)
    sub.add_parser("transfers-inbox").set_defaults(fn=cmd_transfers_inbox)
    a = p.parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
