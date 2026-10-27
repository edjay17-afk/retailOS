"""Passwords, sessions and the server-side permission matrix."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Location, User, WebSession
from .timeutil import now_utc

ROLES = ["staff", "store_manager", "warehouse", "management", "admin"]
ROLE_LABELS = {"staff": "Staff", "store_manager": "Store manager", "warehouse": "Warehouse",
               "management": "Management", "admin": "Admin"}

# permission -> roles that hold it. Enforced on the server for every request, never only in the UI.
# Warehouse has admin operational access; settings (including user management) and audit stay restricted.
PERMS: dict[str, set[str]] = {
    "dashboard.view": set(ROLES),
    "overview.ops": {"warehouse", "admin"},  # the operations charts on the overview
    "inventory.view": {"staff", "store_manager", "warehouse", "management", "admin"},
    "sales.view": {"store_manager", "warehouse", "management", "admin"},
    "repl.view": {"staff", "store_manager", "warehouse", "management", "admin"},
    "repl.decide": {"staff", "store_manager", "warehouse", "management", "admin"},
    "repl.run": {"staff", "warehouse", "management", "admin"},
    "po.view": {"warehouse", "admin"},
    "po.create": {"warehouse", "admin"},  # write a purchase order (supplier, deliver to, products, price) and export it for StoreHub
    "receiving.count": {"warehouse", "store_manager", "management", "admin"},
    "receiving.confirm": {"store_manager", "warehouse", "management", "admin"},
    "inspection.do": {"staff", "store_manager", "warehouse", "management", "admin"},
    "issue.view": {"store_manager", "warehouse", "management", "admin"},
    "issue.update": {"store_manager", "warehouse", "management", "admin"},
    "issue.manage": {"store_manager", "warehouse", "management", "admin"},  # create, assign, verify, close
    "count.enter": {"staff", "warehouse", "store_manager", "management", "admin"},
    "count.approve": {"store_manager", "warehouse", "management", "admin"},
    "expiry.view": {"store_manager", "warehouse", "management", "admin"},
    "traceability.view": {"store_manager", "warehouse", "management", "admin"},
    "dq.view": {"store_manager", "warehouse", "management", "admin"},
    "sync.view": {"store_manager", "warehouse", "management", "admin"},
    "sync.run": {"warehouse", "management", "admin"},
    "audit.view": {"management", "admin"},
    "transfer.dispatch": {"warehouse", "admin"},  # dispatch/import stay with the warehouse team and admins
    "transfer.receive": {"warehouse", "admin"},
    # stores see transfers from/to their own location (routes scope rows) so they can count what arrived
    "transfer.view": {"staff", "store_manager", "warehouse", "management", "admin"},
    "transfer.confirm": {"staff", "store_manager", "management", "admin"},  # V2.1: the store — never the warehouse — confirms receipt
    "transfer.import": {"warehouse", "admin"},
    "transfer.sheet": {"warehouse", "admin"},  # scan-and-quantity transfer sheets: the warehouse team (and admins) list what leaves for a store
    "product.add": {"staff", "store_manager", "warehouse", "management", "admin"},  # anyone on the floor can register a new product
    "product.push": {"warehouse", "management", "admin"},  # writing straight into StoreHub stays with HQ roles; staff lists wait for a push
    "wastage.record": {"staff", "store_manager", "warehouse", "management", "admin"},
    "planogram.view": {"warehouse", "admin"},
    "sales.detail": {"warehouse", "admin"},  # transactions and sales by employee
    "compliance.view": {"store_manager", "warehouse", "management", "admin"},
    "compliance.manage": {"store_manager", "warehouse", "management", "admin"},
    "performance.view": {"store_manager", "warehouse", "management", "admin"},
    "ai.use": {"staff", "store_manager", "warehouse", "management", "admin"},
    "ai.metrics": {"warehouse", "management", "admin"},
    "settings.manage": {"admin"},
    "users.manage": {"admin"},
    "loyverse.view": {"staff", "store_manager", "warehouse", "admin", "management"},
}

# Store-bound users (assigned locations + a store role) get only their own store's daily work:
# the location filter scopes their data, and these cross-store/system pages stay with HQ roles.
SCOPED_DENY = {"issue.view", "issue.update", "issue.manage", "performance.view", "dq.view", "sync.view", "sync.run"}


def has_perm(user: User, perm: str) -> bool:
    if user.location_ids and user.role in ("staff", "store_manager") and perm in SCOPED_DENY:
        return False
    return user.role in PERMS.get(perm, set())


# notification links -> the permission that page needs (first match wins; None = everyone)
LINK_PERMS = (
    ("/transfer-sheets", "transfer.sheet"),
    ("/stock-transfers", "transfer.view"),
    ("/purchase-orders/new", "po.create"),
    ("/purchase-orders", "po.view"),
    ("/replenishment/loyverse", "loyverse.view"),
    ("/replenishment", "repl.view"),
    ("/data-quality", "dq.view"),
    ("/performance", "performance.view"),
    ("/traceability", "traceability.view"),
    ("/inspections", "inspection.do"),
    ("/stock-events", "count.enter"),
    ("/inventory", "inventory.view"),
    ("/assistant", "ai.use"),
    ("/issues", "issue.view"),
    ("/counts", "count.enter"),
    ("/expiry", "expiry.view"),
    ("/sales", "sales.view"),
    ("/sync", "sync.view"),
    ("/audit", "audit.view"),
    ("/compliance", "compliance.view"),
    ("/receiving", "receiving.count"),
    ("/settings", "settings.manage"),
    ("/suppliers", "settings.manage"),
    ("/products", "settings.manage"),
    ("/users", "users.manage"),
    ("/", None),
)


def can_open(user: User, link: str | None) -> bool:
    """True when the user may open the page a link points to, with role and store scope applied."""
    if not link:
        return True
    path = link.split("?")[0]
    for prefix, perm in LINK_PERMS:
        if path == prefix or (prefix != "/" and path.startswith(prefix + "/")):
            return perm is None or has_perm(user, perm)
    return True


# ------------------------------------------------------------------ passwords (scrypt, standard library)
_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        calc = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(calc.hex(), dk)
    except Exception:
        return False


def password_problem(password: str) -> str | None:
    """Policy: at least 4 characters of any kind (letters, digits, special characters)."""
    if len(password) < 4:
        return "Password must be at least 4 characters."
    if password.strip() != password or not password.strip():
        return "Password cannot start or end with a space."
    return None


# ------------------------------------------------------------------ sessions
SESSION_HOURS = 12


def create_session(db: Session, user: User) -> WebSession:
    s = WebSession(id=secrets.token_urlsafe(32), user_id=user.id, csrf=secrets.token_urlsafe(24),
                   expires_at=now_utc() + timedelta(hours=SESSION_HOURS))
    db.add(s)
    db.flush()
    return s


def load_session(db: Session, sid: str | None) -> WebSession | None:
    if not sid:
        return None
    s = db.get(WebSession, sid)
    if s is None or s.expires_at < now_utc() or not s.user.active or s.user.deleted_at is not None:
        return None
    return s


def allowed_location_ids(db: Session, user: User) -> list[int]:
    """Locations the user may see: their scope, or all monitored locations."""
    ids = list(db.scalars(select(Location.id).where(Location.monitored.is_(True))))
    if user.location_ids:
        scope = set(user.location_ids)
        ids = [i for i in ids if i in scope]
    if not user.location_ids and user.role not in ('admin', 'management'):
        ids = list(db.scalars(select(Location.id).where(Location.id.in_(ids or [0]), Location.source == 'storehub')))
    if 'location_scope' in db.info:
        ids = [i for i in ids if i in db.info['location_scope']]
    return ids
