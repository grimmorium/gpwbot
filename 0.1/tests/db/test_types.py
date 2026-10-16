"""Testy typów kolumn `DecimalStr` i `UtcDateTime` (S01, czysta logika).

Procesory bind/result testowane bezpośrednio (bez bazy) oraz przez pełny
roundtrip w SQLite.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, create_engine

from gpwbot.db.types import DecimalStr, UtcDateTime

WARSAW = ZoneInfo("Europe/Warsaw")


def _bind(col_type: object, value: object) -> object:
    """Wywołuje procesor binda typu (czysta funkcja — baza niepotrzebna)."""
    return col_type.process_bind_param(value, dialect=None)  # type: ignore[union-attr]


def _result(col_type: object, value: object) -> object:
    """Wywołuje procesor odczytu typu."""
    return col_type.process_result_value(value, dialect=None)  # type: ignore[union-attr]


class TestDecimalStrBindResult:
    def test_bind_decimal(self):
        assert _bind(DecimalStr(), Decimal("0.1")) == "0.1"

    def test_bind_dokladnosc(self):
        assert _bind(DecimalStr(), Decimal("1234.5678")) == "1234.5678"

    def test_bind_int(self):
        # int jest dozwolony (przekształcany przez Decimal)
        assert _bind(DecimalStr(), 5) == "5"

    def test_bind_none(self):
        assert _bind(DecimalStr(), None) is None

    def test_float_odrzucany(self):
        with pytest.raises(ValueError, match="float"):
            _bind(DecimalStr(), 0.1)

    def test_result_decimal(self):
        assert _result(DecimalStr(), "1234.5678") == Decimal("1234.5678")

    def test_result_none(self):
        assert _result(DecimalStr(), None) is None


class TestUtcDateTimeBindResult:
    def test_bind_aware_konwertuje_do_utc_naive(self):
        ts = dt.datetime(2026, 10, 15, 8, 15, tzinfo=WARSAW)  # UTC+2 w październiku
        assert _bind(UtcDateTime(), ts) == dt.datetime(2026, 10, 15, 6, 15)

    def test_bind_aware_utc(self):
        ts = dt.datetime(2026, 10, 15, 6, 15, tzinfo=dt.UTC)
        assert _bind(UtcDateTime(), ts) == dt.datetime(2026, 10, 15, 6, 15)

    def test_bind_naive_rzuca_value_error(self):
        with pytest.raises(ValueError, match="stref"):
            _bind(UtcDateTime(), dt.datetime(2026, 10, 15, 8, 15))

    def test_bind_none(self):
        assert _bind(UtcDateTime(), None) is None

    def test_result_naive_dostaje_utc(self):
        value = _result(UtcDateTime(), dt.datetime(2026, 10, 15, 6, 15))
        assert value == dt.datetime(2026, 10, 15, 6, 15, tzinfo=dt.UTC)

    def test_result_none(self):
        assert _result(UtcDateTime(), None) is None


class TestRoundtripSqlite:
    @pytest.fixture
    def engine(self):
        return create_engine("sqlite://")

    def test_decimal_roundtrip_bez_utraty_precyzji(self, engine):
        """0.1 i 1234.5678 wracają identyczne (bez artefaktów float)."""
        metadata = MetaData()
        t = Table(
            "t",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("v", DecimalStr),
        )
        metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(
                t.insert().values(
                    [{"id": 1, "v": Decimal("0.1")}, {"id": 2, "v": Decimal("1234.5678")}]
                )
            )
            rows = conn.execute(t.select()).all()
        assert [row.v for row in rows] == [Decimal("0.1"), Decimal("1234.5678")]

    def test_decimal_none_roundtrip(self, engine):
        metadata = MetaData()
        t = Table(
            "t",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("v", DecimalStr),
        )
        metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(t.insert().values({"id": 1, "v": None}))
            row = conn.execute(t.select()).one()
        assert row.v is None

    def test_utc_datetime_inna_strefa_zapisana_jako_utc(self, engine):
        """Aware z Europe/Warsaw zapisany jako UTC i odczytane równe oryginałowi."""
        metadata = MetaData()
        t = Table(
            "t",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("ts", UtcDateTime),
        )
        metadata.create_all(engine)
        original = dt.datetime(2026, 10, 15, 8, 15, tzinfo=WARSAW)
        with engine.begin() as conn:
            conn.execute(t.insert().values({"id": 1, "ts": original}))
            row = conn.execute(t.select()).one()
        # fizycznie w bazie UTC (format SQLite bez przesunięcia strefy)
        with engine.connect() as conn:
            raw = conn.exec_driver_sql("SELECT ts FROM t").one()[0]
        assert str(raw).startswith("2026-10-15 06:15")
        assert row.ts.tzinfo is not None and row.ts.utcoffset() == dt.timedelta(0)
        assert row.ts == original

    def test_utc_datetime_naive_odrzucany_przez_binde(self, engine):
        """Na poziomie bazy `ValueError` jest owinięty w `StatementError`."""
        from sqlalchemy.exc import StatementError

        metadata = MetaData()
        t = Table(
            "t",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("ts", UtcDateTime),
        )
        metadata.create_all(engine)
        with pytest.raises(StatementError, match="stref"), engine.begin() as conn:
            conn.execute(t.insert().values({"id": 1, "ts": dt.datetime(2026, 10, 15)}))
