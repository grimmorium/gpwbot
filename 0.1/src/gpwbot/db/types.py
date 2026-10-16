"""Typy kolumn bazodanowych (S01).

`DecimalStr`: pieniądze/ceny jako tekst `str(Decimal)`; odrzuca `float` (A3).
`UtcDateTime`: datetime świadome strefy; zapis w UTC, odczyt aware UTC.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, String
from sqlalchemy.types import TypeDecorator


class DecimalStr(TypeDecorator[Decimal]):
    """Kwoty i ceny: po stronie Pythona `Decimal`, w bazie tekst."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, float):
            raise ValueError("DecimalStr nie przyjmuje float (A3); użyj Decimal")
        return str(Decimal(value))

    def process_result_value(self, value: Any, dialect: object) -> Decimal | None:
        if value is None:
            return None
        return Decimal(str(value))


def _is_naive(value: dt.datetime) -> bool:
    return value.tzinfo is None or value.utcoffset() is None


class UtcDateTime(TypeDecorator[dt.datetime]):
    """Aware datetime; w bazie UTC, odczyt zawsze ze strefą `dt.UTC`."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: object) -> dt.datetime | None:
        if value is None:
            return None
        if not isinstance(value, dt.datetime):
            raise TypeError(f"UtcDateTime wymaga datetime, otrzymano {type(value).__name__}")
        if _is_naive(value):
            raise ValueError("UtcDateTime wymaga datetime ze strefą czasową")
        return value.astimezone(dt.UTC).replace(tzinfo=None)

    def process_result_value(self, value: Any, dialect: object) -> dt.datetime | None:
        if value is None:
            return None
        if not isinstance(value, dt.datetime):
            raise TypeError(f"UtcDateTime zwraca datetime, otrzymano {type(value).__name__}")
        if _is_naive(value):
            return value.replace(tzinfo=dt.UTC)
        return value.astimezone(dt.UTC)
