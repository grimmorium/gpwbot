"""Testy repozytoriów (S01): instrumenty, ceny dzienne, unikalność, FK."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from gpwbot.db.models import PricesDaily
from gpwbot.db.repo import (
    InstrumentUpsert,
    PriceBar,
    get_instrument_by_ticker,
    get_prices_df,
    last_price_date,
    list_instruments,
    upsert_instruments,
    upsert_prices,
)
from gpwbot.db.session import session_scope

NOW = dt.datetime(2026, 10, 15, 8, 0, tzinfo=dt.UTC)


def _bar(date: dt.date, close: str = "10.0", **kwargs: Any) -> PriceBar:
    base: dict[str, Any] = {
        "open": Decimal("9.5"),
        "high": Decimal("10.5"),
        "low": Decimal("9.4"),
        "close": Decimal(close),
    }
    base.update(kwargs)
    return PriceBar(date=date, **base)


@pytest.fixture
def instrument_id(tmp_db) -> int:
    """Id istniejącego instrumentu PKN."""
    _, factory = tmp_db
    with session_scope(factory) as s:
        (inst,) = upsert_instruments(s, [InstrumentUpsert(ticker="PKN", name="Orlen")])
    return inst.id


class TestInstruments:
    def test_upsert_create(self, tmp_db, instrument_id):
        _, factory = tmp_db
        with session_scope(factory) as s:
            inst = get_instrument_by_ticker(s, "PKN")
        assert inst is not None and inst.id == instrument_id
        assert inst.name == "Orlen"
        assert inst.active is True  # domyślna wartość przy tworzeniu

    def test_upsert_update_nie_mienia_pól_none(self, tmp_db, instrument_id):
        _, factory = tmp_db
        with session_scope(factory) as s:
            upsert_instruments(s, [InstrumentUpsert(ticker="PKN", sector="energia")])
        with factory() as s2:
            inst = get_instrument_by_ticker(s2, "PKN")
        assert inst is not None and inst.sector == "energia"
        assert inst.name == "Orlen"  # niezmienione

    def test_get_by_ticker_brak(self, tmp_db):
        _, factory = tmp_db
        with session_scope(factory) as s:
            assert get_instrument_by_ticker(s, "BRAK") is None

    def test_list_active_only(self, tmp_db):
        _, factory = tmp_db
        with session_scope(factory) as s:
            upsert_instruments(
                s,
                [
                    InstrumentUpsert(ticker="PKN"),
                    InstrumentUpsert(ticker="ABC", active=False),
                ],
            )
        with factory() as s2:
            all_tickers = {i.ticker for i in list_instruments(s2)}
            active_tickers = {i.ticker for i in list_instruments(s2, active_only=True)}
        assert all_tickers == {"PKN", "ABC"}
        assert active_tickers == {"PKN"}


class TestUpsertPrices:
    def test_podwójny_upsert_daje_jeden_wiersz(self, tmp_db, instrument_id):
        _, factory = tmp_db
        bars = [_bar(dt.date(2026, 10, 14))]
        with session_scope(factory) as s:
            assert upsert_prices(s, instrument_id, bars, "stooq", now=NOW) == 1
        with session_scope(factory) as s:
            assert upsert_prices(s, instrument_id, bars, "stooq", now=NOW) == 1
        with factory() as s2:
            rows = list(s2.scalars(select(PricesDaily)).all())
        assert len(rows) == 1

    def test_zmiana_wartosci_aktualizuje_ingested_at(self, tmp_db, instrument_id):
        _, factory = tmp_db
        later = NOW + dt.timedelta(hours=1)
        with session_scope(factory) as s:
            upsert_prices(
                s, instrument_id, [_bar(dt.date(2026, 10, 14), close="10.0")], "stooq", now=NOW
            )
        with session_scope(factory) as s:
            upsert_prices(
                s, instrument_id, [_bar(dt.date(2026, 10, 14), close="11.5")], "stooq", now=later
            )
        with factory() as s2:
            row = s2.scalars(select(PricesDaily)).one()
        assert row.close == Decimal("11.5")
        assert row.ingested_at == later

    def test_duplikat_przez_insert_rzuca_integrity_error(self, tmp_db, instrument_id):
        """Zwykły insert duplikatu PK jest odrzucany (unikalność)."""
        _, factory = tmp_db
        with session_scope(factory) as s:
            upsert_prices(s, instrument_id, [_bar(dt.date(2026, 10, 14))], "stooq", now=NOW)
        with pytest.raises(IntegrityError), session_scope(factory) as s:
            s.execute(
                insert(PricesDaily).values(
                    {
                        "instrument_id": instrument_id,
                        "date": dt.date(2026, 10, 14),
                        "source": "stooq",
                        "open": Decimal("9.5"),
                        "high": Decimal("10.5"),
                        "low": Decimal("9.4"),
                        "close": Decimal("10.0"),
                        "ingested_at": NOW,
                    }
                )
            )

    def test_fk_cena_dla_nieistniejacego_instrumentu_odrzucana(self, tmp_db):
        _, factory = tmp_db
        with pytest.raises(IntegrityError), session_scope(factory) as s:
            upsert_prices(s, 9999, [_bar(dt.date(2026, 10, 14))], "stooq", now=NOW)

    def test_puste_bars_zwracaja_zero(self, tmp_db, instrument_id):
        _, factory = tmp_db
        with session_scope(factory) as s:
            assert upsert_prices(s, instrument_id, [], "stooq", now=NOW) == 0


class TestGetPricesDf:
    @pytest.fixture(autouse=True)
    def _seed(self, tmp_db, instrument_id):
        _, factory = tmp_db
        bars = [
            _bar(dt.date(2026, 10, 8), close="10.0", volume=100, turnover_pln=Decimal("1000")),
            _bar(dt.date(2026, 10, 9), close="10.5"),  # bez wolumenu/obrotu/korekty
            _bar(dt.date(2026, 10, 12), close="11.0", adj_close=Decimal("10.8")),
            _bar(dt.date(2026, 10, 13), close="11.5"),
            _bar(dt.date(2026, 10, 14), close="12.0", quality_flag="needs_review"),
        ]
        with session_scope(factory) as s:
            upsert_prices(s, instrument_id, bars, "stooq", now=NOW)

    def test_filtr_zakresu_dat(self, tmp_db, instrument_id):
        _, factory = tmp_db
        with session_scope(factory) as s:
            df = get_prices_df(
                s, instrument_id, start=dt.date(2026, 10, 9), end=dt.date(2026, 10, 13)
            )
        assert list(df.index.date) == [
            dt.date(2026, 10, 9),
            dt.date(2026, 10, 12),
            dt.date(2026, 10, 13),
        ]

    def test_sortowanie_rosnaco(self, tmp_db, instrument_id):
        _, factory = tmp_db
        with session_scope(factory) as s:
            df = get_prices_df(s, instrument_id)
        assert list(df.index.date) == sorted(list(df.index.date))
        assert len(df) == 5

    def test_typy_kolumn_i_indeks(self, tmp_db, instrument_id):
        _, factory = tmp_db
        with session_scope(factory) as s:
            df = get_prices_df(s, instrument_id)
        assert list(df.columns) == [
            "open",
            "high",
            "low",
            "close",
            "adj_close",
            "volume",
            "turnover",
            "quality_flag",
        ]
        for col in ("open", "high", "low", "close", "adj_close", "volume", "turnover"):
            assert df[col].dtype == "float64"
        assert isinstance(df.index, pd.DatetimeIndex) and df.index.name == "date"

    def test_wartosci_i_nan(self, tmp_db, instrument_id):
        _, factory = tmp_db
        with session_scope(factory) as s:
            df = get_prices_df(s, instrument_id)
        assert df.loc[df.index[0], "close"] == 10.0
        assert pd.isna(df.loc[df.index[1], "adj_close"])
        assert df.loc[df.index[2], "adj_close"] == 10.8
        assert df["quality_flag"].iloc[-1] == "needs_review"

    def test_pusty_wynik(self, tmp_db):
        _, factory = tmp_db
        with session_scope(factory) as s:
            df = get_prices_df(s, 424242)
        assert len(df) == 0
        assert list(df.columns) == [
            "open",
            "high",
            "low",
            "close",
            "adj_close",
            "volume",
            "turnover",
            "quality_flag",
        ]


class TestLastPriceDate:
    def test_zwraca_ostatnia_date(self, tmp_db, instrument_id):
        _, factory = tmp_db
        with session_scope(factory) as s:
            upsert_prices(
                s,
                instrument_id,
                [_bar(dt.date(2026, 10, 8)), _bar(dt.date(2026, 10, 14))],
                "stooq",
                now=NOW,
            )
        with factory() as s2:
            assert last_price_date(s2, instrument_id) == dt.date(2026, 10, 14)

    def test_brak_cen_zwraca_none(self, tmp_db):
        _, factory = tmp_db
        with session_scope(factory) as s:
            (inst,) = upsert_instruments(s, [InstrumentUpsert(ticker="NOWY")])
            assert last_price_date(s, inst.id) is None
