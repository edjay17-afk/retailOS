"""Issue engine: creation with SLA, ownership, status workflow, verification before close, escalation."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from .. import settings_service as cfg
from ..models import Issue, IssueEvent, Location, User
from ..timeutil import now_utc
from . import notifications

OPEN_STATES = ("open", "acknowledged", "in_progress", "resolved")
SEVERITIES = ("P0", "P1", "P2", "P3")
SEV_LABEL = {"P0": "Critical", "P1": "High", "P2": "Medium", "P3": "Low"}
ISSUE_TYPES = ["inspection", "stock", "cash", "receiving", "expiry", "equipment", "permit", "data_quality", "system", "other"]
STATUS_LABEL = {"open": "Open", "acknowledged": "Acknowledged", "in_progress": "In progress", "resolved": "Resolved (awaiting check)", "closed": "Closed"}


def default_owner(db: Session, location_id: int | None) -> int | None:
    """Location manager, else a store manager covering the location, else management."""
    if location_id:
        loc = db.get(Location, location_id)
        if loc and loc.manager_user_id:
            return loc.manager_user_id
        for u in db.scalars(select(User).where(User.role == "store_manager", User.active.is_(True))):
            if not u.location_ids or location_id in u.location_ids:
                return u.id
    u = db.scalar(select(User).where(User.role.in_(["management", "admin"]), User.active.is_(True)).order_by(User.id))
    return u.id if u else None


def create_issue(db: Session, *, type: str, severity: str, title: str, description: str | None = None,
                 location_id: int | None = None, product_id: int | None = None, source: str = "manual",
                 source_ref: str | None = None, owner_id: int | None = None, created_by: int | None = None,
                 dedupe_key: str | None = None, due_hours: float | None = None) -> Issue:
    """Create an issue, or return the open one with the same dedupe key (so system checks never pile up duplicates)."""
    if severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {SEVERITIES}")
    if dedupe_key:
        existing = db.scalar(select(Issue).where(Issue.dedupe_key == dedupe_key, Issue.status.in_(OPEN_STATES)))
        if existing:
            return existing
    ack_min, due_h = cfg.get(db, "issue.sla")[severity]
    now = now_utc()
    owner = owner_id or default_owner(db, location_id)
    issue = Issue(type=type, severity=severity, title=title[:250], description=description, location_id=location_id,
                  product_id=product_id, source=source, source_ref=source_ref, owner_id=owner, created_by=created_by,
                  due_at=now + timedelta(hours=due_hours if due_hours is not None else due_h), dedupe_key=dedupe_key)
    db.add(issue)
    db.flush()
    db.add(IssueEvent(issue_id=issue.id, user_id=created_by, kind="created", text=description or title))
    audit.log(db, actor_type="human" if created_by else "system", actor_id=created_by or source, action="issue.create",
              entity_type="issue", entity_id=issue.id, after={"severity": severity, "type": type, "title": title, "owner": owner})
    link = f"/issues/{issue.id}"
    if owner:
        notifications.notify(db, user_id=owner, level="critical" if severity in ("P0", "P1") else "warning",
                             title=f"[{severity}] {title}", link=link, location_id=location_id, dedupe_key=f"issue:{issue.id}:owner")
    if severity in ("P0", "P1"):
        notifications.notify(db, role="management", level="critical", title=f"[{severity}] {title}", link=link,
                             dedupe_key=f"issue:{issue.id}:mgmt")
    return issue


def _event(db: Session, issue: Issue, user: User | None, kind: str, text: str | None = None, photo: str | None = None):
    db.add(IssueEvent(issue_id=issue.id, user_id=user.id if user else None, kind=kind, text=text, photo_path=photo))


class IssueError(Exception):
    pass


def act(db: Session, issue: Issue, user: User, action: str, note: str | None = None, photo: str | None = None,
        assignee_id: int | None = None) -> Issue:
    """Apply one workflow action. Closing needs a separate person from the one who resolved only when policy says so;
    here, closing requires the issue.manage permission, which staff do not hold, so work is always verified."""
    from ..security import has_perm
    before = {"status": issue.status, "owner_id": issue.owner_id}
    now = now_utc()
    if issue.status == "closed" and action != "reopen":
        raise IssueError("Issue is closed")
    if action == "comment":
        if not note and not photo:
            raise IssueError("Write a comment or attach evidence")
        _event(db, issue, user, "evidence" if photo else "comment", note, photo)
    elif action == "acknowledge":
        if issue.status == "open":
            issue.status, issue.acknowledged_at = "acknowledged", now
            _event(db, issue, user, "status", "Acknowledged")
    elif action == "start":
        if issue.status in ("open", "acknowledged"):
            issue.status = "in_progress"
            issue.acknowledged_at = issue.acknowledged_at or now
            _event(db, issue, user, "status", "Work started")
    elif action == "resolve":
        if not (note or "").strip():
            raise IssueError("Describe the fix before marking the issue resolved")
        issue.status, issue.resolved_at, issue.resolution_note = "resolved", now, note
        issue.acknowledged_at = issue.acknowledged_at or now
        _event(db, issue, user, "status", f"Resolved: {note}", photo)
        notifications.notify(db, role="store_manager", location_id=issue.location_id, level="info",
                             title=f"Issue #{issue.id} resolved, please verify", link=f"/issues/{issue.id}",
                             dedupe_key=f"issue:{issue.id}:verify:{now:%Y%m%d%H%M%S}")
    elif action == "close":
        if not has_perm(user, "issue.manage"):
            raise IssueError("Only a manager can verify and close an issue")
        if issue.status != "resolved":
            raise IssueError("An issue must be resolved (with a described fix) before it is verified and closed")
        issue.status, issue.closed_at, issue.verified_by = "closed", now, user.id
        _event(db, issue, user, "status", f"Verified and closed. {note or ''}".strip())
    elif action == "reopen":
        if not has_perm(user, "issue.manage"):
            raise IssueError("Only a manager can reopen an issue")
        issue.status, issue.closed_at, issue.resolved_at = "open", None, None
        _event(db, issue, user, "status", f"Reopened. {note or ''}".strip())
    elif action == "assign":
        if not has_perm(user, "issue.manage"):
            raise IssueError("Only a manager can assign an issue")
        if not assignee_id:
            raise IssueError("Choose an owner")
        issue.owner_id = assignee_id
        _event(db, issue, user, "assign", note)
        notifications.notify(db, user_id=assignee_id, level="warning", title=f"Issue #{issue.id} assigned to you: {issue.title}",
                             link=f"/issues/{issue.id}", dedupe_key=f"issue:{issue.id}:assign:{assignee_id}:{now:%Y%m%d%H%M%S}")
    elif action == "escalate_manual":
        issue.escalation_level += 1
        issue.last_escalated_at = now
        _event(db, issue, user, "escalate", note)
    else:
        raise IssueError(f"Unknown action {action}")
    audit.log(db, actor_type="human", actor_id=user.id, action=f"issue.{action}", entity_type="issue", entity_id=issue.id,
              before=before, after={"status": issue.status, "owner_id": issue.owner_id}, reason=note)
    return issue


def escalate_overdue(db: Session) -> dict:
    """Idempotent escalation: level 1 when not acknowledged in time, level 2 when past due and not resolved."""
    sla = cfg.get(db, "issue.sla")
    now = now_utc()
    l1 = l2 = 0
    for issue in db.scalars(select(Issue).where(Issue.status.in_(("open", "acknowledged", "in_progress")))):
        ack_min = sla[issue.severity][0]
        if issue.status == "open" and issue.escalation_level < 1 and now > issue.created_at + timedelta(minutes=ack_min):
            issue.escalation_level = 1
            issue.last_escalated_at = now
            _event(db, issue, None, "escalate", f"Not acknowledged within {ack_min} minutes: shift lead and store manager notified")
            notifications.notify(db, role="store_manager", location_id=issue.location_id, level="warning",
                                 title=f"Escalated: [{issue.severity}] {issue.title} not acknowledged", link=f"/issues/{issue.id}",
                                 dedupe_key=f"issue:{issue.id}:esc1")
            audit.log(db, actor_type="system", actor_id="escalation", action="issue.escalate", entity_type="issue",
                      entity_id=issue.id, after={"level": 1})
            l1 += 1
        if issue.escalation_level < 2 and now > issue.due_at:
            issue.escalation_level = 2
            issue.last_escalated_at = now
            _event(db, issue, None, "escalate", "Past its due time: management notified")
            notifications.notify(db, role="management", level="critical", title=f"Overdue: [{issue.severity}] {issue.title}",
                                 link=f"/issues/{issue.id}", dedupe_key=f"issue:{issue.id}:esc2")
            audit.log(db, actor_type="system", actor_id="escalation", action="issue.escalate", entity_type="issue",
                      entity_id=issue.id, after={"level": 2})
            l2 += 1
    return {"level1": l1, "level2": l2}
