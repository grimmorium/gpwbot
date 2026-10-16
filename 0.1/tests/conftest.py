"""Globalne fixture (A4). S00: `fake_clock`, `app_ctx`; kolejne sesje dodają resztę."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from pathlib import Path

import pytest

from gpwbot.clock import FakeClock
from gpwbot.config import AppConfig, load_config
from gpwbot.context import AppContext, build_context

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
