"""Ingest cen dziennych ze Stooq (S03).

Przepływ per ticker: pobranie → zapis surowej odpowiedzi do `raw_dir/stooq/<ticker>/
<data>.csv` → parsowanie → kontrola jakości → upsert. Błąd jednego tickera nie
przerywa pozostałych — trafia do raportu (Część I sekcja 7). Idempotencję daje
upsert po kluczu (instrument_id, date, source) z S01.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import structlog
from sqlalchemy.exc import SQLAlchemyError

from gpwbot.calendar import GpwCalendar
from gpwbot.config import AppConfig
from gpwbot.context import AppContext
from gpwbot.db.repo import (
    InstrumentUpsert,
    upsert_instruments,
    upsert_prices,
)
from gpwbot.db.repo import (
    PriceBar as RepoPriceBar,
)
from gpwbot.db.session import session_scope
from gpwbot.errors import GpwbotError
from gpwbot.providers.base import PriceBar
from gpwbot.providers.prices_stooq import StooqPriceProvider, parse_stooq_csv
from gpwbot.quality import QualityIssue, check_bars

WARSAW = ZoneInfo("Europe/Warsaw")
SOURCE_NAME = "stooq"


@dataclass(frozen=True)
class TickerResult:
    """Wynik ingestu dla jednego tickera."""

    ticker: str
    ok: bool
    bars_fetched: int = 0
    bars_upserted: int = 0
    error: str | None = None
    issues: tuple[QualityIssue, ...] = ()


@dataclass(frozen=True)
class IngestReport:
    """Raport ingestu z licznikami (S03)."""

    start: dt.date
    end: dt.date
    results: tuple[TickerResult, ...]

    @property
    def ok_tickers(self) -> list[str]:
        return [r.ticker for r in self.results if r.ok]

    @property
    def failed_tickers(self) -> dict[str, str]:
        """Ticker → komunikat błędu (tylko nieudane)."""
        return {r.ticker: (r.error or "") for r in self.results if not r.ok}

    @property
    def total_bars_upserted(self) -> int:
        return sum(r.bars_upserted for r in self.results)

    @property
    def total_issues(self) -> int:
        return sum(len(r.issues) for r in self.results)


def _bars_to_df(bars: Sequence[PriceBar]) -> pd.DataFrame:
    """Bary źródłowe → DataFrame w kształcie `repo.get_prices_df` (dla `check_bars`)."""
    df = pd.DataFrame(
        [(float(b.open), float(b.high), float(b.low), float(b.close)) for b in bars],
        columns=["open", "high", "low", "close"],
    )
    if any(b.adj_close is not None for b in bars):
        df["adj_close"] = [None if b.adj_close is None else float(b.adj_close) for b in bars]
    index = pd.DatetimeIndex([dt.datetime.combine(b.date, dt.time()) for b in bars])
    index.name = "date"
    df.index = index
    return df


def _to_repo_bar(bar: PriceBar, flag: str | None) -> RepoPriceBar:
    """Konwersja baru źródłowego na bar do bazy; obrót z `close × volume` (S03)."""
    turnover = bar.turnover_pln
    if turnover is None and bar.volume is not None and bar.volume > 0:
        turnover = (bar.close * Decimal(bar.volume)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    return RepoPriceBar(
        date=bar.date,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        adj_close=bar.adj_close,
        volume=bar.volume,
        turnover_pln=turnover,
        quality_flag=flag,
        adjusted=bar.adj_close is not None,
    )


def _save_raw(raw_dir: Path, ticker: str, now_utc: dt.datetime, raw_text: str) -> None:
    """Cache surowej odpowiedzi: `raw_dir/stooq/<ticker>/<data warszawska>.csv`."""
    day = now_utc.astimezone(WARSAW).date()
    path = Path(raw_dir) / SOURCE_NAME / ticker / f"{day.isoformat()}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw_text, encoding="utf-8")


def _ingest_one(
    ctx: AppContext,
    calendar: GpwCalendar,
    provider: StooqPriceProvider,
    ticker: str,
    start: dt.date,
    end: dt.date,
) -> TickerResult:
    log = structlog.get_logger().bind(ticker=ticker)
    try:
        symbol = provider.symbol_for(ticker)
        raw = provider.fetch_raw(symbol, start, end)
        _save_raw(ctx.config.paths.raw_dir, ticker, ctx.clock.now(), raw)

        bars = parse_stooq_csv(raw)
        issues = check_bars(_bars_to_df(bars), calendar) if bars else []
        flagged = {i.date: i.flag for i in issues if i.flag is not None}
        repo_bars = [
            _to_repo_bar(b, flagged.get(b.date)) for b in sorted(bars, key=lambda x: x.date)
        ]

        with session_scope(ctx.session_factory) as s:
            (inst,) = upsert_instruments(s, [InstrumentUpsert(ticker=ticker)])
            n = upsert_prices(s, inst.id, repo_bars, SOURCE_NAME, now=ctx.clock.now())
    except GpwbotError as exc:  # ProviderError/ConfigError — błąd tickera, nie runu (S03)
        log.warning("ingest_prices.blad", error=str(exc))
        return TickerResult(ticker=ticker, ok=False, error=str(exc))
    except SQLAlchemyError as exc:  # np. brak tabeli po pominięciu `db upgrade`
        log.warning("ingest_prices.blad_bazy", error=str(exc))
        return TickerResult(ticker=ticker, ok=False, error=f"błąd bazy danych: {exc}")

    if issues:
        log.info("ingest_prices.problemy_jakosci", count=len(issues))
    return TickerResult(
        ticker=ticker,
        ok=True,
        bars_fetched=len(bars),
        bars_upserted=n,
        issues=tuple(issues),
    )


def default_price_tickers(cfg: AppConfig) -> list[str]:
    """Tickery cen do pobrania przed S04 (brak modułu universe): indeksy + benchmarki.

    Po S04 lista będzie pochodzić z rozszerzenia universe; tu tylko konfiguracja
    (decyzja D-026).
    """
    return sorted(set(cfg.universe.indices) | set(cfg.portfolio.benchmarks))


def ingest_prices(
    ctx: AppContext, tickers: Sequence[str], start: dt.date, end: dt.date
) -> IngestReport:
    """Pobiera i zapisuje ceny dzienne `tickers` w zakresie `[start, end]`.

    Błąd jednego tickera nie przerywa pozostałych (S03); wyniki w raporcie.
    """
    if start > end:
        raise ValueError("wymagano start <= end")
    calendar = GpwCalendar(ctx.config)
    provider = StooqPriceProvider(ctx.config.providers)
    try:
        results = [_ingest_one(ctx, calendar, provider, ticker, start, end) for ticker in tickers]
    finally:
        provider.close()
    return IngestReport(start=start, end=end, results=tuple(results))


def backfill_prices(
    ctx: AppContext, years: int, tickers: Sequence[str] | None = None
) -> IngestReport:
    """Backfill historii `years` lat partiami rocznymi (S03).

    Zakres: od tej samej daty `years` lat wstecz do dnia bieżącego (zegar kontekstu,
    data warszawska). Domyślnie tickery z `default_price_tickers`; jawni nadpisują.
    """
    if years < 1:
        raise ValueError("years musi być >= 1")
    target = list(tickers) if tickers is not None else default_price_tickers(ctx.config)

    end = ctx.clock.now().astimezone(WARSAW).date()
    try:
        start = end.replace(year=end.year - years)
    except ValueError:  # 29 lutego w roku niestosownym
        start = dt.date(end.year - years, end.month, end.day - 1)

    reports: list[IngestReport] = []
    cursor = start
    while cursor <= end:
        batch_end = min(dt.date(cursor.year + 1, 1, 1) - dt.timedelta(days=1), end)
        reports.append(ingest_prices(ctx, target, cursor, batch_end))
        cursor = batch_end + dt.timedelta(days=1)

    all_results = tuple(r for rep in reports for r in rep.results)
    return IngestReport(start=start, end=end, results=all_results)
