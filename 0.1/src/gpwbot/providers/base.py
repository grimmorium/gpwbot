"""Interfejsy dostawców danych rynkowych (S03).

`PriceBar` to bar po stronie źródła — pola wg specyfikacji S03 plus opcjonalny
`turnover_pln`, bo specyfikacja wymaga użycia obrotu podanego przez źródło, a w
przeciwnym razie policzenia `close × volume` (decyzja D-022). Zapis do bazy używa
rozszerzonego `gpwbot.db.repo.PriceBar`; konwersję robi ingest.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class PriceBar:
    """Jeden dzienny bar cenowy po stronie źródła (ceny `Decimal`, PLN)."""

    date: dt.date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adj_close: Decimal | None = None
    volume: int | None = None
    turnover_pln: Decimal | None = None


class PriceProvider(Protocol):
    """Dostawca cen dziennych; `symbol` to symbol źródła (nie ticker GPW)."""

    def get_daily(self, symbol: str, start: dt.date, end: dt.date) -> list[PriceBar]: ...
