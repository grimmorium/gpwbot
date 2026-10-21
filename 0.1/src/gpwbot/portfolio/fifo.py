"""Partie FIFO (S06) — czysta logika bez ORM i I/O (A3/A4).

Kolejność konsumpcji: `buy_date`, potem `id` partii. `unit_cost` partii to dokładna
wartość `cena + prowizja_zakupu / qty` (D-035) — bez kwantyzacji, by tożsamość
księgowa P/L zachodziła z dokładnością do szumu arytmetyki Decimal.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from gpwbot.errors import LedgerError


@dataclass(frozen=True)
class Lot:
    """Widok partii FIFO (czysty; `unit_cost` = cena + prowizja/qty, D-035)."""

    id: int
    buy_date: dt.date
    qty_remaining: int
    unit_cost: Decimal


@dataclass(frozen=True)
class LotConsumption:
    """Ile akcji pobrano z jednej partii przy sprzedaży."""

    lot_id: int
    qty: int
    unit_cost: Decimal


def consume_lots(lots: Sequence[Lot], qty: int) -> list[LotConsumption]:
    """Konsumuje `qty` akcji w kolejności FIFO (`buy_date`, `id`).

    Rzuca `ValueError` dla `qty <= 0` i `LedgerError`, gdy `qty` przekracza sumę
    dostępnych partii. Partie z `qty_remaining == 0` są pomijane.
    """
    if qty <= 0:
        raise ValueError(f"qty musi być dodatnie (otrzymano {qty})")
    ordered = sorted(lots, key=lambda lot: (lot.buy_date, lot.id))
    available = sum(lot.qty_remaining for lot in ordered)
    if qty > available:
        raise LedgerError(
            f"sprzedaż {qty} przekracza dostępne partie ({available}); FIFO nie może "
            "sprzedać więcej niż stan"
        )
    out: list[LotConsumption] = []
    remaining = qty
    for lot in ordered:
        if remaining == 0 or lot.qty_remaining <= 0:
            continue
        take = min(remaining, lot.qty_remaining)
        out.append(LotConsumption(lot_id=lot.id, qty=take, unit_cost=lot.unit_cost))
        remaining -= take
    return out
