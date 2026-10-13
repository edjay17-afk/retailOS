"""Database engine and session handling (SQLite in WAL mode)."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_config


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal: sessionmaker | None = None


def init_engine(url: str | None = None):
    global _engine, _SessionLocal
    url = url or get_config().db_url
    _engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30}, future=True)

    @event.listens_for(_engine, "connect")
    def _pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_engine():
    if _engine is None:
        init_engine()
    return _engine


def SessionLocal() -> Session:
    if _SessionLocal is None:
        init_engine()
    return _SessionLocal()  # type: ignore[misc]


@contextmanager
def session_scope() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
