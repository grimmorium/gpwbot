"""Testy integracyjne S03: `ingest_prices` / `backfill_prices` (respx + tmp_db).

Zakres z docs/sessions/S03.md: idempotentny (dwa uruchomienia = te same wiersze),
surowy plik zapisany, awaria jednego tickera nie blokuje reszty, raport zawiera
liczniki; backfill partiami rocznymi.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from gpwbot.clock import FakeClock
from gpwbot.config import AppConfig, load_config
from gpwbot.context import AppContext
from gpwbot.db.models import Base, PricesDaily
from gpwbot.db.session import create_engine_for, make_session_factory
from gpwbot.ingest.prices_job import backfill_prices, default_price_tickers, ingest_prices

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CSV_PKN = (FIXTURES / "stooq_pkn.csv").read_text(encoding="utf-8")
CSV_JUMP = (FIXTURES / "stooq_jump.csv").read_text(encoding="utf-8")

START, END = dt.date(2026, 10, 19), dt.date(2026, 10, 21)


def write_config(tmp_path: Path, db_file: Path) -> AppConfig:
    text = (
        "paths:\n"
        f"  db_path: {db_file.as_posix()}\n"
        f"  data_dir: {tmp_path.as_posix()}\n"
        f"  raw_dir: {(tmp_path / 'raw').as_posix()}\n"
        f"  reports_dir: {(tmp_path / 'reports').as_posix()}\n"
        "universe:\n"
        "  indices: [PKN]\n"
        "portfolio:\n"
        "  benchmarks: [WIG20]\n"
        "providers:\n"
        "  stooq:\n"
        "    requests_per_second: 10000\n"
        "    timeout_seconds: 5.0\n"
        "    max_retries: 3\n"
        "    retry_backoff_base_s: 0.0\n"
    )
    config_file = tmp_path / "config.yaml"
    config_file.write_text(text, encoding="utf-8")
    return load_config(config_file)


@pytest.fixture
def ctx(tmp_path: Path, fake_clock: FakeClock) -> AppContext:
    """Kontekst z bazą (schemat nałożony), deterministycznym zegarem i configiem testowym."""
    cfg = write_config(tmp_path, tmp_path / "test.db")
    engine = create_engine_for(cfg)
    Base.metadata.create_all(engine)
    factory: sessionmaker[Session] = make_session_factory(engine)
    yield AppContext(config=cfg, clock=fake_clock, session_factory=factory)
    engine.dispose()


def stooq_handler(
    bodies: dict[str, str], calls: list[str]
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        symbol = request.url.params["s"]
        calls.append(symbol)
        body = bodies.get(symbol)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, text=body)

    return handler


def price_rows(session_factory: sessionmaker[Session]) -> list[PricesDaily]:
    with session_factory() as s:
        return list(s.scalars(select(PricesDaily).order_by(PricesDaily.date)).all())


class TestIngestPrices:
    def test_zapisuje_bary_i_cache_surowej_odpowiedzi(self, ctx: AppContext) -> None:
        calls: list[str] = []
        with respx.mock:
            respx.route(url__startswith="https://stooq.pl/q/d/l/").mock(
                side_effect=stooq_handler({"pkn": CSV_PKN}, calls)
            )
            report = ingest_prices(ctx, ["PKN"], START, END)

        assert report.ok_tickers == ["PKN"]
        assert calls == ["pkn"]  # symbol z mapowania (małe litery)
        rows = price_rows(ctx.session_factory)
        assert len(rows) == 3
        assert all(r.source == "stooq" for r in rows)

        raw_file = ctx.config.paths.raw_dir / "stooq" / "PKN" / "2026-10-15.csv"
        assert raw_file.is_file()
        assert raw_file.read_text(encoding="utf-8") == CSV_PKN

    def test_idempotentny_dwa_uruchomienia_te_same_wiersze(self, ctx: AppContext) -> None:
        calls: list[str] = []
        with respx.mock:
            respx.route(url__startswith="https://stooq.pl/q/d/l/").mock(
                side_effect=stooq_handler({"pkn": CSV_PKN}, calls)
            )
            ingest_prices(ctx, ["PKN"], START, END)
        ctx.clock.advance(dt.timedelta(hours=2))
        with respx.mock:
            respx.route(url__startswith="https://stooq.pl/q/d/l/").mock(
                side_effect=stooq_handler({"pkn": CSV_PKN}, calls)
            )
            report = ingest_prices(ctx, ["PKN"], START, END)

        assert len(price_rows(ctx.session_factory)) == 3
        assert report.total_bars_upserted == 3
        rows = price_rows(ctx.session_factory)
        assert all(r.ingested_at >= ctx.clock.now() - dt.timedelta(minutes=1) for r in rows)

    def test_awaria_jednego_tickera_nie_blokuje_reszty(self, ctx: AppContext) -> None:
        calls: list[str] = []
        with respx.mock:
            respx.route(url__startswith="https://stooq.pl/q/d/l/").mock(
                side_effect=stooq_handler({"pkn": CSV_PKN}, calls)  # xyz → 404
            )
            report = ingest_prices(ctx, ["PKN", "XYZ"], START, END)

        assert report.ok_tickers == ["PKN"]
        assert list(report.failed_tickers) == ["XYZ"]
        assert "HTTP 404" in report.failed_tickers["XYZ"]
        tickers_in_db = {r.instrument_id for r in price_rows(ctx.session_factory)}
        assert len(tickers_in_db) == 1  # tylko PKN w bazie

    def test_raport_zawiera_liczniki(self, ctx: AppContext) -> None:
        calls: list[str] = []
        with respx.mock:
            respx.route(url__startswith="https://stooq.pl/q/d/l/").mock(
                side_effect=stooq_handler({"pkn": CSV_PKN}, calls)
            )
            report = ingest_prices(ctx, ["PKN"], START, END)

        assert (report.start, report.end) == (START, END)
        assert len(report.results) == 1
        result = report.results[0]
        assert result.bars_fetched == 3 and result.bars_upserted == 3
        assert result.error is None and result.issues == ()

    def test_flag_needs_review_trafia_do_bazy(self, ctx: AppContext) -> None:
        calls: list[str] = []
        with respx.mock:
            respx.route(url__startswith="https://stooq.pl/q/d/l/").mock(
                side_effect=stooq_handler({"pkn": CSV_JUMP}, calls)
            )
            report = ingest_prices(ctx, ["PKN"], START, END)

        assert report.total_issues == 1
        (issue,) = report.results[0].issues
        assert issue.rule == "large_change" and issue.flag == "needs_review"
        rows = price_rows(ctx.session_factory)
        flags = {r.date: r.quality_flag for r in rows}
        assert flags[dt.date(2026, 10, 20)] == "needs_review"
        assert flags[dt.date(2026, 10, 19)] is None

    def test_obrót_liczony_jako_close_x_volume(self, ctx: AppContext) -> None:
        calls: list[str] = []
        with respx.mock:
            respx.route(url__startswith="https://stooq.pl/q/d/l/").mock(
                side_effect=stooq_handler({"pkn": CSV_PKN}, calls)
            )
            ingest_prices(ctx, ["PKN"], START, END)

        rows = price_rows(ctx.session_factory)
        first = rows[0]
        expected = (Decimal("98.90") * Decimal(1234567)).quantize(Decimal("0.01"))
        assert first.turnover_pln == expected
        assert first.adjusted is False  # CSV bez cen skorygowanych

    def test_zły_zakres_dat_rzuca_value_error(self, ctx: AppContext) -> None:
        with pytest.raises(ValueError):
            ingest_prices(ctx, ["PKN"], END, START)


class TestBackfillPrices:
    def test_partie_roczne_i_zakres(self, ctx: AppContext) -> None:
        # Zegar: 2026-10-15 (Warszawa); years=2 → start 2024-10-15.
        calls: list[str] = []
        d1s: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            symbol = request.url.params["s"]
            calls.append(symbol)
            d1s.append(request.url.params["d1"])
            return httpx.Response(200, text=CSV_PKN) if symbol == "pkn" else httpx.Response(404)

        with respx.mock:
            respx.route(url__startswith="https://stooq.pl/q/d/l/").mock(side_effect=handler)
            report = backfill_prices(ctx, 2, tickers=["PKN"])

        assert (report.start, report.end) == (dt.date(2024, 10, 15), dt.date(2026, 10, 15))
        # Trzy partie: reszta 2024, całe 2025, początek 2026.
        assert d1s == ["20241015", "20250101", "20260101"]

    def test_domyślne_tickery_z_konfiguracji(self, ctx: AppContext) -> None:
        calls: list[str] = []
        with respx.mock:
            respx.route(url__startswith="https://stooq.pl/q/d/l/").mock(
                side_effect=stooq_handler({"pkn": CSV_PKN, "wig20": CSV_PKN}, calls)
            )
            report = backfill_prices(ctx, 1)

        # Zakres 2025-10-15..2026-10-15 to dwie partie roczne × dwa tickery.
        assert sorted(calls) == ["pkn", "pkn", "wig20", "wig20"]
        assert set(report.ok_tickers) == {"PKN", "WIG20"}


class TestDefaultTickers:
    def test_indeksy_plus_benchmarki(self, ctx: AppContext) -> None:
        assert default_price_tickers(ctx.config) == ["PKN", "WIG20"]
