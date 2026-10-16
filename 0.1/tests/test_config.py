"""Testy konfiguracji (S00): ładowanie, walidacja, nadpisywania, hash."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from gpwbot.config import AppConfig, load_config
from gpwbot.errors import ConfigError


def _write(tmp_path: Path, text: str, name: str = "config.yaml") -> Path:
    config_file = tmp_path / name
    config_file.write_text(text, encoding="utf-8")
    return config_file


BASE_PATHS = """\
paths:
  db_path: data/gpwbot.db
  data_dir: data
  raw_dir: data/raw
  reports_dir: reports
"""


def test_valid_sample_config_loads(sample_config: AppConfig) -> None:
    assert sample_config.timezone == "Europe/Warsaw"
    assert sample_config.risk.max_position_pct == Decimal("10")
    assert [run.name for run in sample_config.runs] == ["morning", "evening"]


def test_defaults_applied(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, BASE_PATHS))
    assert cfg.timezone == "Europe/Warsaw"
    assert cfg.market.settlement_days == 2
    assert cfg.llm.samples_per_decision == 1
    assert cfg.costs.commission_pct == Decimal("0.30")
    assert [run.name for run in cfg.runs] == ["morning", "evening"]


def test_unknown_key_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="foo"):
        load_config(_write(tmp_path, BASE_PATHS + "\nfoo: bar\n"))


def test_out_of_range_percent_rejected(tmp_path: Path) -> None:
    text = BASE_PATHS + "risk:\n  max_position_pct: 150\n"
    with pytest.raises(ConfigError, match="max_position_pct"):
        load_config(_write(tmp_path, text))


def test_duplicate_run_name_rejected(tmp_path: Path) -> None:
    text = (
        BASE_PATHS
        + "runs:\n"
        + "  - {name: morning, hour: 8, minute: 15}\n"
        + "  - {name: morning, hour: 9, minute: 0}\n"
    )
    with pytest.raises(ConfigError, match="unikalne"):
        load_config(_write(tmp_path, text))


def test_duplicate_portfolio_id_rejected(tmp_path: Path) -> None:
    text = (
        BASE_PATHS
        + "portfolio:\n"
        + "  portfolios:\n"
        + "    - {id: a, strategy: llm}\n"
        + "    - {id: a, strategy: momentum}\n"
    )
    with pytest.raises(ConfigError, match="unikalne"):
        load_config(_write(tmp_path, text))


def test_samples_per_decision_out_of_range_rejected(tmp_path: Path) -> None:
    text = BASE_PATHS + "llm:\n  samples_per_decision: 6\n"
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, text))


def test_env_override_has_proper_type(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, BASE_PATHS), env={"GPWBOT__RISK__MAX_POSITION_PCT": "7.5"})
    assert cfg.risk.max_position_pct == Decimal("7.5")


def test_env_override_int_and_bool(tmp_path: Path) -> None:
    cfg = load_config(
        _write(tmp_path, BASE_PATHS),
        env={"GPWBOT__MARKET__SETTLEMENT_DAYS": "1", "GPWBOT__LLM__THINKING_DECISION": "false"},
    )
    assert cfg.market.settlement_days == 1
    assert cfg.llm.thinking_decision is False


def test_env_override_malformed_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="GPWBOT__"):
        load_config(_write(tmp_path, BASE_PATHS), env={"GPWBOT__ONLYSECTION": "x"})


def test_env_override_unknown_key_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, BASE_PATHS), env={"GPWBOT__RISK__NO_SUCH_KEY": "1"})


def test_relative_paths_resolved_against_file(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, BASE_PATHS))
    assert cfg.paths.db_path == tmp_path / "data" / "gpwbot.db"
    assert cfg.paths.reports_dir == tmp_path / "reports"


def test_absolute_paths_untouched(tmp_path: Path) -> None:
    text = (
        "paths:\n"
        f"  db_path: {tmp_path.as_posix()}/abs.db\n"
        "  data_dir: data\n"
        "  raw_dir: data/raw\n"
        "  reports_dir: reports\n"
    )
    cfg = load_config(_write(tmp_path, text))
    assert str(cfg.paths.db_path).endswith("abs.db")


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "brak.yaml")


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="YAML"):
        load_config(_write(tmp_path, "paths: [niezamkni\u0119te\n"))


def test_market_phase_order_validated(tmp_path: Path) -> None:
    text = BASE_PATHS + "market:\n" + '  preopen_start: "10:00"\n' + '  preopen_end: "09:00"\n'
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, text))


def test_time_fields_parsed_as_time(sample_config: AppConfig) -> None:
    assert sample_config.market.continuous_start == dt.time(9, 0)
    assert sample_config.market.close_auction_end == dt.time(17, 0)


def test_config_hash_independent_of_key_order(tmp_path: Path) -> None:
    a = _write(
        tmp_path,
        "timezone: Europe/Warsaw\n" + BASE_PATHS + "llm:\n  temperature: 0.2\n",
        name="a.yaml",
    )
    b = _write(
        tmp_path,
        "llm:\n  temperature: 0.2\n" + BASE_PATHS + "timezone: Europe/Warsaw\n",
        name="b.yaml",
    )
    assert load_config(a).config_hash() == load_config(b).config_hash()


def test_config_hash_changes_with_value(tmp_path: Path) -> None:
    base = _write(tmp_path, BASE_PATHS + "llm:\n  temperature: 0.2\n", name="base.yaml")
    changed = _write(tmp_path, BASE_PATHS + "llm:\n  temperature: 0.3\n", name="changed.yaml")
    assert load_config(base).config_hash() != load_config(changed).config_hash()


def test_config_is_frozen(sample_config: AppConfig) -> None:
    with pytest.raises(ValidationError, match="frozen"):
        sample_config.timezone = "UTC"  # type: ignore[misc]
