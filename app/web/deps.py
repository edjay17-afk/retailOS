"""Request context: database session, authenticated user, CSRF check, permission guard, template rendering."""
from __future__ import annotations

import os
import secrets
from datetime import date, datetime
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import i18n
from .. import settings_service as cfg
from ..config import get_config
from ..db import SessionLocal
from ..models import Issue, Location, Recommendation, User, WebSession
from ..security import ROLE_LABELS, allowed_location_ids, has_perm, load_session
from ..services import freshness, issues, notifications
from ..timeutil import fmt_clock, fmt_local, now_utc, to_local, today_local

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
COOKIE = "rsid"


class NotAuthenticated(Exception):
    def __init__(self, next_url: str = "/"):
        self.next_url = next_url


class MustChangePassword(Exception):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class Ctx:
    """Everything a route needs about the current request."""

    def __init__(self, request: Request, db: Session, session: WebSession):
        self.request, self.db, self.session, self.user = request, db, session, session.user
        self.csrf = session.csrf

    def can(self, perm: str) -> bool:
        return has_perm(self.user, perm)

    @property
    def lang(self) -> str:
        return i18n.norm(self.request.cookies.get(i18n.COOKIE_NAME))

    def t(self, text: str, **values) -> str:
        return i18n.translate(self.lang, text, **values)

    @property
    def location_ids(self) -> list[int]:
        return allowed_location_ids(self.db, self.user)

    def locations(self) -> list[Location]:
        ids = self.location_ids
        return list(self.db.scalars(select(Location).where(Location.id.in_(ids or [0])).order_by(Location.type.desc(), Location.name)))

    def check_loc(self, location_id: int | None) -> None:
        if location_id is not None and location_id not in self.location_ids:
            raise HTTPException(403, "You do not have access to this location")

    def check_csrf(self, token: str) -> None:
        if not token or not secrets.compare_digest(token, self.csrf):
            raise HTTPException(403, "Security token missing or expired. Reload the page and try again.")


def ctx_dep(allow_must_change: bool = False):
    def dep(request: Request, db: Session = Depends(get_db)) -> Ctx:
        s = load_session(db, request.cookies.get(COOKIE))
        if s is None:
            raise NotAuthenticated(request.url.path)
        c = Ctx(request, db, s)
        if s.user.must_change_password and not allow_must_change:
            raise MustChangePassword()
        return c
    return dep


def need(perm: str) -> Callable:
    """Dependency factory: require a permission, enforced on the server."""
    base = ctx_dep()

    def dep(c: Ctx = Depends(base)) -> Ctx:
        if not c.can(perm):
            raise HTTPException(403, "Your role does not allow this")
        return c
    return dep


def redirect(url: str, ok: str | None = None, err: str | None = None) -> RedirectResponse:
    sep = "&" if "?" in url else "?"
    if ok:
        url += f"{sep}ok={quote(ok)}"
    elif err:
        url += f"{sep}err={quote(err)}"
    return RedirectResponse(url, status_code=303)


# ---------------------------------------------------------------------------------------- formatting
def money(v) -> str:
    return "" if v is None else f"{v:,.2f}"


def num(v, digits: int = 2) -> str:
    if v is None:
        return ""
    s = f"{v:,.{digits}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def pct(v) -> str:
    return "" if v is None else f"{v * 100:.0f}%"


def ago(dt: datetime | None) -> str:
    if not dt:
        return "never"
    s = (now_utc() - dt).total_seconds()
    if s < 90:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400:
        return f"{int(s // 3600)} h ago"
    return f"{int(s // 86400)} d ago"


templates.env.filters.update(money=money, num=num, pct=pct, local=fmt_local, clock=fmt_clock)


@pass_context
def localized_ago(context, dt):
    return i18n.translate(context.get("lang", "en"), ago(dt))


@pass_context
def localized_day(context, day):
    if not day:
        return ""
    if context.get("lang") == "zh":
        return f"{day.month}月{day.day}日 周{'一二三四五六日'[day.weekday()]}"
    return day.strftime("%a %d %b")


templates.env.filters.update(ago=localized_ago, day=localized_day)
from . import charts  # noqa: E402

templates.env.filters["zip"] = lambda a, b: list(zip(a, b))


@pass_context
def localized_store_item(context, store):
    return (store["loc"].name.replace("The Daily Centro - ", ""), store["net"],
            i18n.translate(context.get("lang", "en"), "{count} units", count=f"{store['units']:,.0f}"))


@pass_context
def localized_top_item(context, row):
    return (row[0], float(row[2] or 0),
            i18n.translate(context.get("lang", "en"), "{count} units", count=f"{float(row[1] or 0):,.0f}"))


templates.env.filters.update(store_item=localized_store_item, top_item=localized_top_item)
templates.env.globals.update(unit_fmt=lambda v: f"{v:g} units", money_fmt=lambda v: f"₱{v:,.2f}")
templates.env.globals.update(ROLE_LABELS=ROLE_LABELS, hbars=charts.hbars)


def localized_chart(chart):
    @pass_context
    def draw(context, *args, **kwargs):
        return chart(*args, **kwargs, t=lambda text, **values: i18n.translate(context.get("lang", "en"), text, **values))
    return draw


templates.env.globals.update({name: localized_chart(getattr(charts, name))
                              for name in ("sparkline", "bars", "ring", "area", "donut", "hbars")})

# (group title, [(url, label, icon, permission, tag)])
NAV_GROUPS = [
    ("Overview", [("/", "Today", "home", "dashboard.view", "")]),
    ("Operations", [("/issues", "Issues", "alert", "issue.view", ""), ("/inspections", "Inspections", "clipboard", "inspection.do", ""),
                    ("/counts", "Cycle counts", "count", "count.enter", ""),
                    ("/stock-events", "Stock events", "camera", "count.enter", ""),
                    ("/wastage", "Wastage", "trash", "wastage.record", ""),
                    ("/expiry", "Expiry", "clock", "expiry.view", ""), ("/compliance", "Permits", "shield", "compliance.view", ""), ("/planogram", "Planogram", "grid", "planogram.view", "")]),
    ("Stock and ordering", [("/replenishment", "Replenishment", "trend", "repl.view", ""), ("/replenishment/repairs", "Repair queue", "wrench", "repl.view", ""), ("/inventory", "Inventory", "box", "inventory.view", ""),
                            ("/stock-transfers", "Stock transfers", "sync", "transfer.view", ""),
                            ("/purchase-orders", "Purchase orders", "file", "po.view", ""), ("/sales", "Sales", "chart", "sales.view", ""), ("/sales/products", "Sales by product", "tag", "sales.view", ""),
                            ("/sales/transactions", "Transactions", "list", "sales.detail", ""), ("/sales/employees", "Sales by employee", "user", "sales.detail", "")]),
    ("Intelligence", [("/assistant", "Assistant", "sparkles", "ai.use", "AI"), ("/performance", "Performance", "gauge", "performance.view", ""), ("/data-quality", "Data quality", "shield", "dq.view", ""),
                      ("/traceability", "Traceability", "link", "traceability.view", ""),
                      ("/sync", "Sync status", "pulse", "sync.view", "")]),
    ("Administration", [("/settings", "Settings", "settings", "settings.manage", ""), ("/audit", "Audit log", "history", "audit.view", "")]),
]


def render(c: Ctx, name: str, **kw):
    req = c.request
    lang = i18n.norm(req.cookies.get(i18n.COOKIE_NAME))
    fr = freshness.freshness(c.db)
    stale = [f for f in fr if f["status"] != "fresh" and f["critical"]]
    pills = _nav_pills(c)
    base = dict(request=req, user=c.user, csrf=c.csrf, can=c.can, company=cfg.get(c.db, "app.company"),
                nav_groups=[(t, [(u, l, i, str(pills[u]) if pills.get(u) else tag) for u, l, i, p, tag in links if c.can(p)]) for t, links in NAV_GROUPS if any(c.can(p) for _, _, _, p, _ in links)],
                path=req.url.path, stale=stale, lang=lang, t=c.t,
                ui_messages={key: c.t(key) for key in i18n.JS_MESSAGES},
                unit_fmt=lambda value: c.t("{count} units", count=f"{value:g}"),
                unread=notifications.unread_count(c.db, c.user), ok=req.query_params.get("ok"), err=req.query_params.get("err"))
    base.update(kw)
    return templates.TemplateResponse(req, name, base)


def _nav_pills(c: Ctx) -> dict:
    """Live work-waiting counts shown as small pills next to nav entries."""
    pills = {}
    ids = c.location_ids
    if c.can("repl.view"):
        pills["/replenishment"] = c.db.scalar(select(func.count()).select_from(Recommendation).join(Location, Location.id == Recommendation.location_id).where(
            Recommendation.business_date == today_local(), Recommendation.location_id.in_(ids or [0]), Recommendation.status == "pending", Location.type == "store")) or 0
    if c.can("issue.view"):
        pills["/issues"] = c.db.scalar(select(func.count()).select_from(Issue).where(
            (Issue.location_id.in_(ids or [0])) | Issue.location_id.is_(None), Issue.status.in_(issues.OPEN_STATES))) or 0
    return pills


def render_public(request: Request, name: str, status_code: int = 200, **kw):
    kw.setdefault("ok", request.query_params.get("ok"))
    kw.setdefault("err", request.query_params.get("err"))
    lang = i18n.norm(request.cookies.get(i18n.COOKIE_NAME))
    return templates.TemplateResponse(request, name, dict(request=request, company="The Daily Centro", lang=lang,
                                                          ui_messages={key: i18n.translate(lang, key) for key in i18n.JS_MESSAGES},
                                                          t=lambda s, **values: i18n.translate(lang, s, **values), **kw), status_code=status_code)


# ---------------------------------------------------------------------------------------- uploads
ALLOWED_MAGIC = {b"\xff\xd8\xff": ".jpg", b"\x89PNG": ".png", b"RIFF": ".webp"}
MAX_UPLOAD = 8 * 1024 * 1024


async def save_upload(file: UploadFile | None, allow_video: bool = False) -> str | None:
    """Store an evidence photo (or, for the walkthrough, a video) after checking size and real file type. Returns the stored relative path."""
    if file is None or not file.filename:
        return None
    head = await file.read(16)
    await file.seek(0)
    if allow_video and _video_ext(head):
        return await _save_video(file, _video_ext(head))
    data = await file.read(MAX_UPLOAD + 1)
    if not data:
        return None
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Photo is larger than 8 MB")
    ext = next((e for m, e in ALLOWED_MAGIC.items() if data.startswith(m)), None)
    if ext is None:
        raise HTTPException(400, "Only JPEG, PNG or WebP photos are accepted")
    now = now_utc()
    rel = f"{now:%Y/%m}/{secrets.token_hex(12)}{ext}"
    dest = get_config().upload_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return rel


MAX_VIDEO = 300 * 1024 * 1024


def _video_ext(head: bytes) -> str | None:
    """Real file type from the first bytes: MP4 or MOV ('ftyp' box) or WebM/MKV (EBML header)."""
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return ".mov" if head[8:12] == b"qt  " else ".mp4"
    if head[:4] == bytes.fromhex("1a45dfa3"):
        return ".webm"
    return None


async def _save_video(file: UploadFile, ext: str) -> str:
    rel = f"{now_utc():%Y/%m}/{secrets.token_hex(12)}{ext}"
    dest = get_config().upload_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        with open(dest, "wb") as out:  # streamed to disk in pieces, never held in memory
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_VIDEO:
                    raise HTTPException(413, "Video is larger than 300 MB. Record a shorter walkthrough.")
                out.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    return rel


def parse_float(v: str | None) -> float | None:
    v = (v or "").strip().replace(",", "")
    if v == "":
        return None
    try:
        return float(v)
    except ValueError:
        raise HTTPException(400, f"'{v}' is not a number")


def parse_date(v: str | None) -> date | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise HTTPException(400, f"'{v}' is not a valid date")
