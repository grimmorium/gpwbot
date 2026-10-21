"""Testy CLI (S00): --help, version, doctor, stuby; S01: db upgrade; S03: ingest;
S04: universe; S05: screen."""

from __future__ import annotations

import datetime as dt
import sqlite3
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import create_engine
from typer.testing import CliRunner

from gpwbot.cli import app
from gpwbot.db.migrate import upgrade_to_head
from gpwbot.db.repo import InstrumentUpsert, PriceBar, upsert_instruments, upsert_prices
from gpwbot.db.session import make_session_factory, session_scope, sqlite_url

runner = CliRunner()


def test_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in (
        "version",
        "doctor",
        "ingest",
        "backfill-prices",
        "universe",
        "screen",
        "run",
        "replay",
        "report",
    ):
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


# --- S04: universe ------------------------------------------------------------


def _write_universe_env(tmp_path: Path) -> tuple[Path, Path]:
    """Konfiguracja + seed pod tmp CWD; baza migracyjnie gotowa."""
    db_file = tmp_path / "data" / "gpwbot.db"
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "universe_seed.yaml").write_text(
        "- ticker: AAA\n  indices: [wig20]\n- ticker: BBB\n  indices: [wig20]\n",
        encoding="utf-8",
    )
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "paths:\n"
        f"  db_path: {db_file.as_posix()}\n"
        f"  data_dir: {(tmp_path / 'data').as_posix()}\n"
        f"  raw_dir: {(tmp_path / 'raw').as_posix()}\n"
        f"  reports_dir: {(tmp_path / 'reports').as_posix()}\n"
        "universe:\n"
        "  indices: [wig20]\n"
        "  min_avg_turnover_pln_20d: 100\n"
        "  min_history_sessions: 3\n",
        encoding="utf-8",
    )
    upgrade_to_head(db_file)
    return config_file, db_file


def _seed_prices_for_cli(db_file: Path) -> None:
    """Dobre ceny AAA (3 sesje do 2026-10-15); BBB bez danych."""
    engine = create_engine(sqlite_url(db_file))
    factory = make_session_factory(engine)
    try:
        with session_scope(factory) as s:
            (inst,) = upsert_instruments(
                s, [InstrumentUpsert(ticker="AAA", index_membership=["wig20"])]
            )
            bars = [
                PriceBar(
                    date=d,
                    open=Decimal("10"),
                    high=Decimal("10"),
                    low=Decimal("10"),
                    close=Decimal("10"),
                    volume=1000,
                    turnover_pln=Decimal("1000"),
                )
                for d in (dt.date(2026, 10, 13), dt.date(2026, 10, 14), dt.date(2026, 10, 15))
            ]
            ts_now = dt.datetime(2026, 10, 15, 21, tzinfo=dt.UTC)
            upsert_prices(s, inst.id, bars, "test", now=ts_now)
    finally:
        engine.dispose()


def test_universe_wypisuje_członków_i_powody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Akceptacja S04: `gpwbot universe --as-of DATA` wypisuje członków i powody."""
    config_file, db_file = _write_universe_env(tmp_path)
    _seed_prices_for_cli(db_file)

    monkeypatch.chdir(tmp_path)  # odwracane automatycznie po teście (chdir nie zwraca CM)
    result = runner.invoke(app, ["--config", str(config_file), "universe", "--as-of", "2026-10-15"])

    assert result.exit_code == 0, result.output
    out = result.output
    assert "członkowie (1):" in out
    assert "AAA" in out
    assert "wykluczeni (1):" in out
    assert "BBB: short_history" in out


def test_universe_bad_as_of_returns_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_file, _ = _write_universe_env(tmp_path)

    monkeypatch.chdir(tmp_path)  # odwracane automatycznie po teście (chdir nie zwraca CM)
    result = runner.invoke(app, ["--config", str(config_file), "universe", "--as-of", "abc"])

    assert result.exit_code == 1


# --- S05: screen --------------------------------------------------------------


def _write_screen_env(tmp_path: Path) -> tuple[Path, Path]:
    """Konfiguracja + seed pod tmp CWD; baza migracyjnie gotowa (wzorzec S04)."""
    db_file = tmp_path / "data" / "gpwbot.db"
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "universe_seed.yaml").write_text(
        "- ticker: AAA\n  indices: [wig20]\n- ticker: BBB\n  indices: [wig20]\n",
        encoding="utf-8",
    )
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "paths:\n"
        f"  db_path: {db_file.as_posix()}\n"
        f"  data_dir: {(tmp_path / 'data').as_posix()}\n"
        f"  raw_dir: {(tmp_path / 'raw').as_posix()}\n"
        f"  reports_dir: {(tmp_path / 'reports').as_posix()}\n"
        "universe:\n"
        "  indices: [wig20]\n"
        "  min_avg_turnover_pln_20d: 100\n"
        "  min_history_sessions: 3\n",
        encoding="utf-8",
    )
    upgrade_to_head(db_file)
    return config_file, db_file


def _seed_screen_prices(db_file: Path, n_bars: int = 40) -> None:
    """Rosnące ceny AAA i malejące BBB (różne zmienności/momentum) do czw. 2026-10-15."""
    dates: list[dt.date] = []
    d = dt.date(2026, 10, 15)
    while len(dates) < n_bars:
        if d.weekday() < 5:
            dates.append(d)
        d -= dt.timedelta(days=1)
    dates.reverse()

    def bars_for(start: Decimal, step: Decimal) -> list[PriceBar]:
        out = []
        for i, date in enumerate(dates):
            close = start + step * i
            volume = 1_000 + i
            out.append(
                PriceBar(
                    date=date,
                    open=close - Decimal("0.1"),
                    high=close + Decimal("0.2"),
                    low=close - Decimal("0.3"),
                    close=close,
                    volume=volume,
                    turnover_pln=Decimal(str(close * volume)),
                )
            )
        return out

    engine = create_engine(sqlite_url(db_file))
    factory = make_session_factory(engine)
    try:
        with session_scope(factory) as s:
            (aaa, bbb) = upsert_instruments(
                s,
                [
                    InstrumentUpsert(ticker="AAA", index_membership=["wig20"]),
                    InstrumentUpsert(ticker="BBB", index_membership=["wig20"]),
                ],
            )
            ts_now = dt.datetime(2026, 10, 15, 21, tzinfo=dt.UTC)
            upsert_prices(s, aaa.id, bars_for(Decimal("10"), Decimal("0.1")), "test", now=ts_now)
            upsert_prices(s, bbb.id, bars_for(Decimal("20"), Decimal("-0.05")), "test", now=ts_now)
    finally:
        engine.dispose()


def test_screen_wypisuje_shortliste_z_powodami(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Akceptacja S05: `gpwbot screen --as-of DATA` wypisuje shortlistę z powodami."""
    config_file, db_file = _write_screen_env(tmp_path)
    _seed_screen_prices(db_file)

    monkeypatch.chdir(tmp_path)  # odwracane automatycznie po teście (chdir nie zwraca CM)
    result = runner.invoke(app, ["--config", str(config_file), "screen", "--as-of", "2026-10-15"])

    assert result.exit_code == 0, result.output
    out = result.output
    assert "universe: członkowie=2" in out
    assert "shortlista (2):" in out
    for ticker in ("AAA", "BBB"):
        line = next(line for line in out.splitlines() if line.strip().startswith(ticker))
        assert f"{ticker}: score=" in line
        assert "powody=top_score" in line


def test_screen_bad_as_of_returns_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_file, _ = _write_screen_env(tmp_path)

    monkeypatch.chdir(tmp_path)  # odwracane automatycznie po teście (chdir nie zwraca CM)
    result = runner.invoke(app, ["--config", str(config_file), "screen", "--as-of", "abc"])

    assert result.exit_code == 1
