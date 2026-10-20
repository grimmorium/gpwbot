"""Testy CLI (S00): --help, version, doctor, stuby; S01: db upgrade; S03: ingest."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from gpwbot.cli import app
from gpwbot.db.migrate import upgrade_to_head

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
        ["run", "morning"],
        ["replay", "--run-id", "3"],
        ["report", "--date", "2026-10-19"],
        ["eval", "--from", "2026-11-01"],
    ],
)
def test_stubs_return_2(args: list[str]) -> None:
    result = runner.invoke(app, args)
    assert result.exit_code == 2


# --- S03: ingest cen -----------------------------------------------------------

CSV_PKN = (Path(__file__).resolve().parent / "fixtures" / "stooq_pkn.csv").read_text(
    encoding="utf-8"
)


def _write_ingest_config(tmp_path: Path) -> tuple[Path, Path]:
    """Konfiguracja z bazą w tmp i jednym tickerem (PKN); baza migracyjnie gotowa."""
    db_file = tmp_path / "data" / "gpwbot.db"
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "paths:\n"
        f"  db_path: {db_file.as_posix()}\n"
        f"  data_dir: {(tmp_path / 'data').as_posix()}\n"
        f"  raw_dir: {(tmp_path / 'raw').as_posix()}\n"
        f"  reports_dir: {(tmp_path / 'reports').as_posix()}\n"
        "universe:\n"
        "  indices: [PKN]\n"
        "providers:\n"
        "  stooq:\n"
        "    requests_per_second: 10000\n"
        "    timeout_seconds: 5.0\n"
        "    max_retries: 3\n"
        "    retry_backoff_base_s: 0.0\n",
        encoding="utf-8",
    )
    upgrade_to_head(db_file)
    return config_file, db_file


def test_ingest_prices_end_to_end(tmp_path: Path) -> None:
    """`gpwbot ingest --prices --since 5d`: pobranie (mock), zapis do bazy i raport."""
    config_file, _ = _write_ingest_config(tmp_path)
    with respx.mock:
        respx.route(url__startswith="https://stooq.pl/q/d/l/").mock(
            return_value=httpx.Response(200, text=CSV_PKN)
        )
        result = runner.invoke(app, ["--config", str(config_file), "ingest", "--prices"])

    assert result.exit_code == 0, result.output
    assert "OK: PKN" in result.output
    assert "razem:" in result.output


def test_ingest_bad_since_returns_1(tmp_path: Path) -> None:
    config_file, _ = _write_ingest_config(tmp_path)
    result = runner.invoke(
        app, ["--config", str(config_file), "ingest", "--prices", "--since", "abc"]
    )
    assert result.exit_code == 1


def test_backfill_prices_end_to_end(tmp_path: Path) -> None:
    config_file, _ = _write_ingest_config(tmp_path)
    with respx.mock:
        respx.route(url__startswith="https://stooq.pl/q/d/l/").mock(
            return_value=httpx.Response(200, text=CSV_PKN)
        )
        result = runner.invoke(
            app, ["--config", str(config_file), "backfill-prices", "--years", "1"]
        )

    assert result.exit_code == 0, result.output
    assert "OK: PKN" in result.output


def test_backfill_prices_bad_config_returns_1(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("paths:\n  db_path: x\n", encoding="utf-8")
    result = runner.invoke(app, ["--config", str(bad), "backfill-prices"])
    assert result.exit_code == 1
