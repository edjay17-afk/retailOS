"""FastAPI application factory."""
from __future__ import annotations

import logging
import logging.handlers
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .. import jobs
from ..config import get_config
from ..db import init_engine
from ..dbinit import init_db
from .deps import MustChangePassword, NotAuthenticated, render_public
from . import routes_admin, routes_ai, routes_core, routes_ops, routes_po_drafts, routes_products, routes_stock, routes_transfers, routes_documents, routes_planogram, routes_wastage, routes_v21, routes_transfer_sheets


def setup_logging() -> None:
    cfg = get_config()
    root = logging.getLogger()
    if any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(cfg.log_dir / "retailos.log", maxBytes=5_000_000, backupCount=8, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.setLevel(logging.INFO)
    root.addHandler(fh)
    root.addHandler(sh)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    init_engine()
    init_db()
    jobs.start()
    yield
    jobs.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="Retail OS", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if get_config().require_https and request.url.scheme != "https" and request.url.path != "/healthz":
            # Do not process credentials or other writes over plaintext transport.
            if request.method not in ("GET", "HEAD"):
                return JSONResponse({"detail": "HTTPS is required. Reopen the site using its trusted HTTPS address."}, status_code=400)
            from urllib.parse import urlsplit
            base = urlsplit(get_config().base_url)
            if base.scheme != "https" or not base.netloc or base.username or base.password:
                return JSONResponse({"detail": "The secure site address has not been configured."}, status_code=503)
            return RedirectResponse(get_config().base_url.rstrip("/") + request.url.path + ("?" + request.url.query if request.url.query else ""), status_code=307)
        resp = await call_next(request)
        if get_config().require_https and request.url.scheme == "https":
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; frame-ancestors 'none'; form-action 'self'")
        if not request.url.path.startswith("/static/"):
            resp.headers.setdefault("Cache-Control", "no-store")  # pages hold business data: never cache them
        return resp

    @app.exception_handler(NotAuthenticated)
    async def _na(request: Request, exc: NotAuthenticated):
        nxt = exc.next_url if exc.next_url.startswith("/") and not exc.next_url.startswith("//") else "/"
        return RedirectResponse(f"/login?next={nxt}", status_code=303)

    @app.exception_handler(MustChangePassword)
    async def _mcp(request: Request, exc: MustChangePassword):
        return RedirectResponse("/password", status_code=303)

    @app.exception_handler(HTTPException)
    async def _http(request: Request, exc: HTTPException):
        if request.url.path.startswith("/api/") or "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return render_public(request, "error.html", status_code=exc.status_code, title=str(exc.status_code),
                             detail=str(exc.detail))

    @app.exception_handler(Exception)
    async def _any(request: Request, exc: Exception):
        logging.getLogger("retailos.web").exception("unhandled error on %s", request.url.path)
        return render_public(request, "error.html", status_code=500, title="Something went wrong",
                             detail="The error was logged. Please try again, or tell the administrator.")

    for r in (routes_core, routes_po_drafts, routes_stock, routes_ops, routes_ai, routes_admin, routes_transfers, routes_products, routes_documents, routes_planogram, routes_wastage, routes_v21, routes_transfer_sheets):
        app.include_router(r.router)
    return app


app = create_app()
