"""Planogram: the 3D store walk-through (built separately, copied in by scripts/sync_planogram.py) shown inside Retail OS."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..config import BASE_DIR
from .deps import Ctx, need, render

router = APIRouter()
ROOT = (BASE_DIR / "planogram").resolve()
# The 3D viewer is its own page: it needs inline scripts, blob workers and the Inter font, and it may be shown in a frame by Retail OS itself (never by other sites)
VIEWER_HEADERS = {
    "X-Frame-Options": "SAMEORIGIN",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                               "font-src 'self' https://fonts.gstatic.com data:; script-src 'self' 'unsafe-inline' blob:; worker-src 'self' blob:; connect-src 'self' blob: data:; "
                               "frame-ancestors 'self'; form-action 'self'",
}


@router.get("/planogram")
def planogram_page(c: Ctx = Depends(need("planogram.view"))):
    return render(c, "planogram.html", available=(ROOT / "index.html").exists())


@router.get("/planogram-app/{path:path}")
def planogram_file(path: str, c: Ctx = Depends(need("planogram.view"))):
    target = (ROOT / (path or "index.html")).resolve()
    if target.is_dir():
        target = target / "index.html"
    if ROOT not in target.parents or not target.is_file():  # nothing outside the planogram folder is ever served
        raise HTTPException(404)
    headers = dict(VIEWER_HEADERS)
    headers["Cache-Control"] = "no-store" if target.suffix == ".html" else "private, max-age=86400"
    return FileResponse(target, headers=headers)
