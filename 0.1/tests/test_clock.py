"""Testy zegarów i kontekstu aplikacji (S00)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from gpwbot.clock import FakeClock, SystemClock
from gpwbot.config import AppConfig, load_config
from gpwbot.context import AppContext, build_context

MINIMAL_CONFIG = """\
paths:
  db_path: data/gpwbot.db
  data_dir: data
  raw_dir: data/raw
  reports_dir: reports
"""


def _config_file(tmp_path: Path, name: str = "config.yaml") -> Path:
    path = tmp_path / name
    path.write_text(MINIMAL_CONFIG, encoding="utf-8")
    return path


def test_fake_clock_advance() -> None:
    clock = FakeClock(dt.datetime(2026, 10, 15, 8, 15, tzinfo=dt.UTC))
    assert clock.now() == dt.datetime(2026, 10, 15, 8, 15, tzinfo=dt.UTC)
    clock.advance(dt.timedelta(hours=1, minutes=30))
    assert clock.now() == dt.datetime(2026, 10, 15, 9, 45, tzinfo=dt.UTC)


def test_fake_clock_advance_negative() -> None:
    clock = FakeClock(dt.datetime(2026, 10, 15, 8, 15, tzinfo=dt.UTC))
    clock.advance(dt.timedelta(seconds=-30))
    assert clock.now() == dt.datetime(2026, 10, 15, 8, 14, 30, tzinfo=dt.UTC)


def test_fake_clock_set() -> None:
    clock = FakeClock(dt.datetime(2026, 10, 15, 8, 15, tzinfo=dt.UTC))
    new_ts = dt.datetime(2027, 1, 1, 0, 0, tzinfo=dt.timezone(dt.timedelta(hours=1)))
    clock.set(new_ts)
    assert clock.now() == new_ts


def test_fake_clock_naive_start_rejected() -> None:
    with pytest.raises(ValueError):
        FakeClock(dt.datetime(2026, 10, 15, 8, 15))


def test_fake_clock_naive_set_rejected() -> None:
    clock = FakeClock(dt.datetime(2026, 10, 15, tzinfo=dt.UTC))
    with pytest.raises(ValueError):
        clock.set(dt.datetime(2026, 10, 16))


def test_system_clock_now_aware_utc() -> None:
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() == dt.timedelta(0)


def test_app_ctx_fixture(app_ctx: AppContext) -> None:
    assert app_ctx.config.timezone == "Europe/Warsaw"
    now = app_ctx.clock.now()
    assert isinstance(now, dt.datetime) and now.tzinfo is not None


def test_build_context_defaults_to_system_clock(tmp_path: Path) -> None:
    ctx = build_context(_config_file(tmp_path))
    assert isinstance(ctx.config, AppConfig)
    assert isinstance(ctx.clock, SystemClock)


def test_build_context_overrides_config_and_clock(tmp_path: Path) -> None:
    other_cfg = load_config(_config_file(tmp_path, name="other.yaml"))
    clock = FakeClock(dt.datetime(2026, 1, 2, tzinfo=dt.UTC))
    ctx = build_context(_config_file(tmp_path), config=other_cfg, **{"clock": clock})
    assert ctx.config is other_cfg
    assert isinstance(ctx.clock, FakeClock)
    assert ctx.clock.now() == dt.datetime(2026, 1, 2, tzinfo=dt.UTC)


def test_build_context_rejects_unknown_field(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="nieznane pola"):
        build_context(_config_file(tmp_path), bogus=1)
