"""Consistent online backups of the SQLite database, verified after writing, with retention."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from ..config import get_config
from ..timeutil import now_utc, to_local


def backup_now(keep: int = 40) -> Path:
    cfg = get_config()
    src = cfg.data_dir / "retailos.db"
    stamp = to_local(now_utc()).strftime("%Y%m%d-%H%M")
    dest = cfg.backup_dir / f"retailos-{stamp}.db"
    with sqlite3.connect(src) as s, sqlite3.connect(dest) as d:
        s.backup(d)  # SQLite online backup API: safe while the app is running
    with sqlite3.connect(dest) as chk:
        ok = chk.execute("PRAGMA integrity_check").fetchone()[0]
    if ok != "ok":
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"backup failed integrity check: {ok}")
    files = sorted(cfg.backup_dir.glob("retailos-*.db"))
    for old in files[:-keep]:
        old.unlink(missing_ok=True)
    return dest


def list_backups() -> list[tuple[str, int, datetime]]:
    cfg = get_config()
    return [(p.name, p.stat().st_size, datetime.fromtimestamp(p.stat().st_mtime)) for p in sorted(cfg.backup_dir.glob("retailos-*.db"), reverse=True)]
