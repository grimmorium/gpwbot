"""Testy CLI (S00): --help, version, doctor, stuby; S01: db upgrade."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gpwbot.cli import app

runner = CliRunner()


def test_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("version", "doctor", "ingest", "backfill-prices", "run", "replay", "report"):
        assert command in result.output


def test_db_upgrade_tworzy_baze(tmp_path: Path) -> None:
    """`gpwbot db upgrade` tworzy bazę od zera (akceptacja S01)."""
    config_file = tmp_path / "config.yaml"
    db_file = tmp_path / "data" / "gpwbot.db"
    config_file.write_text(
        "paths:\n"
        f"  db_path: {db_file.as_posix()}\n"
        "  data_dir: data\n"
        "  raw_dir: data/raw\n"
        "  reports_dir: reports\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["--config", str(config_file), "db", "upgrade"])
    assert result.exit_code == 0, result.output
    assert db_file.is_file()
    con = sqlite3.connect(db_file)
    try:
        query = "SELECT name FROM sqlite_master WHERE type='table'"
        tables = {row[0] for row in con.execute(query)}
    finally:
        con.close()
    assert {"instruments", "prices_daily", "runs"}.issubset(tables)


def test_db_upgrade_bad_config_returns_1(tmp_path: Path) -> None:
    config_file = tmp_path / "bad.yaml"
    config_file.write_text("paths:\n  db_path: x\n", encoding="utf-8")
    result = runner.invoke(app, ["--config", str(config_file), "db", "upgrade"])
    assert result.exit_code == 1


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "gpwbot" in result.output


def test_doctor_ok(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "paths:\n"
        f"  db_path: {tmp_path.as_posix()}/data/gpwbot.db\n"
        "  data_dir: data\n"
        "  raw_dir: data/raw\n"
        "  reports_dir: reports\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["--config", str(config_file), "doctor"])
    assert result.exit_code == 0
    assert "hash:" in result.output
    assert "db_path:" in result.output


def test_doctor_bad_config_returns_1(tmp_path: Path) -> None:
    config_file = tmp_path / "bad.yaml"
    config_file.write_text("paths:\n  db_path: x\n", encoding="utf-8")
    result = runner.invoke(app, ["--config", str(config_file), "doctor"])
    assert result.exit_code == 1


def test_doctor_missing_config_returns_1(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--config", str(tmp_path / "brak.yaml"), "doctor"])
    assert result.exit_code == 1


@pytest.mark.parametrize(
    "args",
    [
        ["ingest"],
        ["backfill-prices"],
        ["run", "morning"],
        ["replay", "--run-id", "3"],
        ["report", "--date", "2026-10-19"],
        ["eval", "--from", "2026-11-01"],
    ],
)
def test_stubs_return_2(args: list[str]) -> None:
    result = runner.invoke(app, args)
    assert result.exit_code == 2
