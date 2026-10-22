"""Typy zleceń symulatora wykonania (S07): typ, status i kody powodów.

Wartości `StrEnum` to małe litery — spójnie z konwencją słupków tekstowych bazy
(`status="pending"`, D-031). Kody powodów trafiają do `orders.reason`.
"""

from __future__ import annotations

from enum import StrEnum


class OrderType(StrEnum):
    """Typ zlecenia; v1 realizuje wyłącznie limit na otwarciu sesji."""

    LIMIT_AT_OPEN = "limit_at_open"


class OrderStatus(StrEnum):
    """Status cyklu życia zlecenia w symulatorze."""

    PENDING = "pending"
    FILLED = "filled"
    EXPIRED = "expired"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class OrderReason(StrEnum):
    """Kody powodów odrzucenia (submit) i wygaśnięcia (process_session)."""

    NO_CASH = "no_cash"
    NO_POSITION = "no_position"
    LIMIT_NOT_MET = "limit_not_met"
    VOLUME_LIMIT = "volume_limit"
    NO_BAR = "no_bar"
