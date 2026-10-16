"""Silnik i sesje SQLAlchemy (S01).

PRAGMA na każdym połączeniu: `journal_mode=WAL`, `foreign_keys=ON`,
`busy_timeout=5000`. Repozytoria nie commitują — transakcja należy do
wywołującego (`session_scope`).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from gpwbot.config import AppConfig


def sqlite_url(db_path: str | Path) -> str:
    """URL SQLite dla ścieżki pliku (forward slashes — bezpieczne na Windows)."""
    path = Path(str(db_path))
    if str(path) == ":memory:":
        return "sqlite://"
    return "sqlite:///" + path.as_posix()


def create_engine_for(cfg: AppConfig) -> Engine:
    """Buduje silnik dla `cfg.paths.db_path`; katalog bazy tworzony automatycznie."""
    db_path = Path(str(cfg.paths.db_path))
    if str(db_path) != ":memory:" and not db_path.parent.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        sqlite_url(db_path),
        connect_args={"check_same_thread": False},
    )
    _attach_pragmas(engine)
    return engine


def _attach_pragmas(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Fabryka sesji ORM; `expire_on_commit=False` (obiekty żyją po commicie)."""
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Sesja w transakcji: commit na końcu, rollback przy wyjątku."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
