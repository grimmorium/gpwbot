"""Typy wspólne portfela i wykonania (S06): strona zlecenia, intencja, fill.

Czyste typy bez zależności od ORM — używane przez `portfolio/` (S06), fill model
(S07) i Risk Manager (S08). Pieniądze jako `Decimal` (A3).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class Side(Enum):
    """Strona zlecenia. Przeniesiona z `gpwbot.costs` w S06 (D-034); tam reeksportowana."""

    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class OrderIntent:
    """Propozycja zlecenia przed zapisem do bazy (S07/S08 decydują, tu tylko kształt)."""

    ticker: str
    side: Side
    qty: int
    limit_price: Decimal | None = None
    reason: str = ""


@dataclass(frozen=True)
class FillRecord:
    """Dane realizacji zlecenia — wejście do zapisu w `fills` i `PortfolioService.apply_fill`.

    `commission` to prowizja już policzona (S07, `costs.commission`); serwis księguje ją
    bez ponownego liczenia.
    """

    order_id: int
    session_date: dt.date
    price: Decimal
    qty: int
    commission: Decimal = Decimal("0")
