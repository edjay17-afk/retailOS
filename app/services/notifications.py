"""In-app notifications. Dedupe keys make every notification idempotent."""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import Notification, User
from ..security import can_open
from ..timeutil import now_utc


def notify(db: Session, *, title: str, body: str | None = None, level: str = "info", link: str | None = None,
           user_id: int | None = None, role: str | None = None, location_id: int | None = None,
           dedupe_key: str | None = None) -> Notification | None:
    """Notify a user, or everyone with a role (optionally limited to those who cover a location)."""
    if dedupe_key and db.scalar(select(Notification).where(Notification.dedupe_key == dedupe_key)):
        return None
    n = Notification(user_id=user_id, role=role, location_id=location_id, level=level, title=title, body=body, link=link,
                     dedupe_key=dedupe_key)
    db.add(n)
    db.flush()
    return n


def visible_for(db: Session, user: User, limit: int = 30) -> list[Notification]:
    loc_ok = [None] + list(user.location_ids or [])
    q = select(Notification).where(
        or_(Notification.user_id == user.id,
            (Notification.user_id.is_(None)) & (Notification.role == user.role) &
            (Notification.location_id.is_(None) if not user.location_ids else Notification.location_id.in_(loc_ok) | Notification.location_id.is_(None)))
    ).order_by(Notification.created_at.desc()).limit(limit * 2)
    # a notification the user cannot open (its page is not for their role/scope) is noise: hide it
    return [n for n in db.scalars(q) if can_open(user, n.link)][:limit]


def unread_count(db: Session, user: User) -> int:
    return sum(1 for n in visible_for(db, user, 100) if n.read_at is None)


def mark_all_read(db: Session, user: User) -> None:
    now = now_utc()
    for n in visible_for(db, user, 200):
        if n.read_at is None and n.user_id == user.id:
            n.read_at = now
        elif n.read_at is None:
            n.read_at = now  # role notifications are shared; marking read clears them for the role
