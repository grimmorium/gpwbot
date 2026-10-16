"""Globalne fixture (A4). S00: `fake_clock`, `app_ctx`; S01 dodaje `tmp_db`."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from gpwbot.clock import FakeClock
from gpwbot.config import AppConfig, PathsCfg, load_config
from gpwbot.context import AppContext, build_context
from gpwbot.db.models import Base
from gpwbot.db.session import create_engine_for, make_session_factory

MINIMAL_CONFIG = """\
paths:
  db_path: data/gpwbot.db
  data_dir: data
  raw_dir: data/raw
  reports_dir: reports
"""


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(dt.datetime(2026, 10, 15, 8, 15, tzinfo=dt.UTC))


@pytest.fixture
def app_ctx(tmp_path: Path, fake_clock: FakeClock) -> Iterator[AppContext]:
    """Kontekst z minimalną konfiguracją i deterministycznym zegarem."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(MINIMAL_CONFIG, encoding="utf-8")
    ctx = build_context(config_file, clock=fake_clock)
    yield ctx


@pytest.fixture
def sample_config() -> AppConfig:
    """Pełna przykładowa konfiguracja z repozytorium (config/config.yaml)."""
    config_path = Path(__file__).resolve().parent.parent / "config" / "config.yaml"
    return load_config(config_path)


@pytest.fixture
def tmp_db(tmp_path: Path) -> Iterator[tuple[Engine, sessionmaker[Session]]]:
    """Baza SQLite w pliku z nałożonym schematem (PRAGMA jak w produkcji).

    Schemat kładziemy przez `Base.metadata.create_all` — zgodność migracji
    z metadanymi jest sprawdzana osobno w `tests/db/test_migrations.py`.
    Zwraca parę `(engine, session_factory)`.
    """
    db_file = tmp_path / "test.db"
    cfg = AppConfig(
        paths=PathsCfg(
            db_path=db_file,
            data_dir=tmp_path / "data",
            raw_dir=tmp_path / "raw",
            reports_dir=tmp_path / "reports",
        )
    )
    engine = create_engine_for(cfg)
    Base.metadata.create_all(engine)
    yield engine, make_session_factory(engine)
    engine.dispose()
