"""Programowe sterowanie migracjami Alembic (S01).

`script_location` i `sqlalchemy.url` są nadpisywane jawnie, więc komendy
działają niezależnie od bieżącego katalogu.
"""

from __future__ import annotations

from pathlib import Path

import alembic.command as command
from alembic.config import Config as AlembicConfig

_DB_DIR = Path(__file__).resolve().parent
_MIGRATIONS_DIR = _DB_DIR / "migrations"
_PROJECT_ROOT = _DB_DIR.parents[2]
_ALEMBIC_INI = _PROJECT_ROOT / "alembic.ini"


def _config_for_url(url: str) -> AlembicConfig:
    cfg = AlembicConfig(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _ensure_parent_dir(db_path: str | Path) -> None:
    path = Path(str(db_path))
    if str(path) != ":memory:" and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)


def upgrade_to_head(db_path: str | Path) -> None:
    """`alembic upgrade head` dla pliku bazy (tworzy bazę od zera)."""
    from gpwbot.db.session import sqlite_url

    _ensure_parent_dir(db_path)
    command.upgrade(_config_for_url(sqlite_url(db_path)), "head")


def downgrade_to_base(db_path: str | Path) -> None:
    """`alembic downgrade base` dla pliku bazy (usuwa wszystkie tabele)."""
    from gpwbot.db.session import sqlite_url

    _ensure_parent_dir(db_path)
    command.downgrade(_config_for_url(sqlite_url(db_path)), "base")
