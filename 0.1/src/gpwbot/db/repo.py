"""Cienkie repozytoria (S01): instrumenty i ceny dzienne.

Repozytoria nie commitują — transakcja należy do wywołującego
(`session_scope`). `ingested_at` pochodzi z parametru `now` albo z
`SystemClock` (decyzja D-009).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from gpwbot.clock import SystemClock
from gpwbot.db.models import Instrument, PricesDaily


@dataclass(frozen=True)
class PriceBar:
    """Jeden dzienny bar cenowy (ceny jako `Decimal`, PLN)."""

    date: dt.date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adj_close: Decimal | None = None
    volume: int | None = None
    turnover_pln: Decimal | None = None
    quality_flag: str | None = None
    adjusted: bool = False


@dataclass(frozen=True)
class InstrumentUpsert:
    """Dane do upsertu instrumentu; `None` = pole nie jest zmieniane."""

    ticker: str
    isin: str | None = None
    name: str | None = None
    segment: str | None = None
    sector: str | None = None
    index_membership: list[str] | None = field(default=None)
    active: bool | None = None
    aliases_json: list[str] | None = field(default=None)


def upsert_instruments(
    session: Session, instruments: Sequence[InstrumentUpsert]
) -> list[Instrument]:
    """Tworzy lub aktualizuje instrumenty po tickerze.

    Zwraca obiekty ORM w kolejności wejścia (po `flush`, bez commitu).
    Tickery w jednej partii muszą być różne.
    """
    results: list[Instrument] = []
    for item in instruments:
        inst = session.scalar(select(Instrument).where(Instrument.ticker == item.ticker))
        if inst is None:
            inst = Instrument(ticker=item.ticker)
            session.add(inst)
        if item.isin is not None:
            inst.isin = item.isin
        if item.name is not None:
            inst.name = item.name
        if item.segment is not None:
            inst.segment = item.segment
        if item.sector is not None:
            inst.sector = item.sector
        if item.index_membership is not None:
            inst.index_membership = list(item.index_membership)
        if item.active is not None:
            inst.active = item.active
        if item.aliases_json is not None:
            inst.aliases_json = list(item.aliases_json)
        results.append(inst)
    session.flush()
    return results


def get_instrument_by_ticker(session: Session, ticker: str) -> Instrument | None:
    """Instrument po tickerze albo `None`."""
    return session.scalar(select(Instrument).where(Instrument.ticker == ticker))


def list_instruments(session: Session, active_only: bool = False) -> list[Instrument]:
    """Lista instrumentów (opcjonalnie tylko aktywnych), rosnąco po id."""
    stmt = select(Instrument).order_by(Instrument.id.asc())
    if active_only:
        stmt = stmt.where(Instrument.active.is_(True))
    return list(session.scalars(stmt).all())


_PRICE_UPDATE_COLS = (
    "open",
    "high",
    "low",
    "close",
    "adj_close",
    "volume",
    "turnover_pln",
    "quality_flag",
    "adjusted",
    "ingested_at",
)


def upsert_prices(
    session: Session,
    instrument_id: int,
    bars: Sequence[PriceBar],
    source: str,
    *,
    now: dt.datetime | None = None,
) -> int:
    """Upsert barów po kluczu (instrument_id, date, source); zwraca liczbę wierszy.

    Konflikt aktualizuje wartości i `ingested_at` (= `now`, domyślnie bieżący czas).
    Naruszenie FK (nieistniejący instrument) rzuca `IntegrityError`.
    """
    if not bars:
        return 0
    ts = now if now is not None else SystemClock().now()
    rows: list[dict[str, Any]] = [
        {
            "instrument_id": instrument_id,
            "date": bar.date,
            "source": source,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "adj_close": bar.adj_close,
            "volume": bar.volume,
            "turnover_pln": bar.turnover_pln,
            "quality_flag": bar.quality_flag,
            "adjusted": bar.adjusted,
            "ingested_at": ts,
        }
        for bar in bars
    ]
    stmt = sqlite_insert(PricesDaily).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["instrument_id", "date", "source"],
        set_={col: stmt.excluded[col] for col in _PRICE_UPDATE_COLS},
    )
    session.execute(stmt)
    return len(rows)


def get_prices_df(
    session: Session,
    instrument_id: int,
    start: dt.date | None = None,
    end: dt.date | None = None,
) -> pd.DataFrame:
    """Ceny dzienne jako DataFrame (indeks `date`, kolumny numeryczne float64)."""
    stmt = select(PricesDaily).where(PricesDaily.instrument_id == instrument_id)
    if start is not None:
        stmt = stmt.where(PricesDaily.date >= start)
    if end is not None:
        stmt = stmt.where(PricesDaily.date <= end)
    rows = list(session.scalars(stmt.order_by(PricesDaily.date.asc())).all())

    def _f(value: Decimal | int | None) -> float | None:
        return None if value is None else float(value)

    df = pd.DataFrame(
        [
            (
                float(r.open),
                float(r.high),
                float(r.low),
                float(r.close),
                _f(r.adj_close),
                _f(r.volume),
                _f(r.turnover_pln),
                r.quality_flag,
            )
            for r in rows
        ],
        columns=["open", "high", "low", "close", "adj_close", "volume", "turnover", "quality_flag"],
    )
    df.index = pd.DatetimeIndex([r.date for r in rows], name="date")
    for col in ("open", "high", "low", "close", "adj_close", "volume", "turnover"):
        df[col] = df[col].astype("float64")
    return df


def last_price_date(session: Session, instrument_id: int) -> dt.date | None:
    """Ostatnia data ceny dla instrumentu (dowolne źródło) albo `None`."""
    stmt = select(func.max(PricesDaily.date)).where(PricesDaily.instrument_id == instrument_id)
    return session.scalar(stmt)
