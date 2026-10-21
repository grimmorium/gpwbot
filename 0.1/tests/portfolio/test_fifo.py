"""Testy czystej logiki FIFO (S06): kolejność, zużycie, błędy."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from gpwbot.errors import LedgerError
from gpwbot.portfolio.fifo import Lot, consume_lots


def _lot(lot_id: int, buy_date: str, qty: int, unit_cost: str) -> Lot:
    return Lot(
        id=lot_id,
        buy_date=dt.date.fromisoformat(buy_date),
        qty_remaining=qty,
        unit_cost=Decimal(unit_cost),
    )


def test_fifo_kolejnosc_buy_date_i_id():
    """Konsumpcja w kolejności (buy_date, id) — nie kolejności listy."""
    lots = [
        _lot(3, "2026-10-14", 50, "12.00"),
        _lot(1, "2026-10-12", 100, "10.00"),
        _lot(2, "2026-10-12", 30, "11.00"),
    ]
    out = consume_lots(lots, 80)
    assert [(c.lot_id, c.qty, str(c.unit_cost)) for c in out] == [(1, 80, "10.00")]


def test_fifo_kupno_100_i_50_sprzedaz_120():
    """Kupno 100 po 10 i 50 po 12, sprzedaż 120 → zużycie 100 + 20."""
    lots = [
        _lot(1, "2026-10-12", 100, "10.00"),
        _lot(2, "2026-10-14", 50, "12.00"),
    ]
    out = consume_lots(lots, 120)
    assert [(c.lot_id, c.qty) for c in out] == [(1, 100), (2, 20)]
    cost_basis = sum(c.qty * c.unit_cost for c in out)
    assert cost_basis == Decimal("1240.00")


def test_fifo_sprzedaz_czesciowa():
    """Sprzedaż mniejsza niż pierwsza partia nie dotyka kolejnych."""
    lots = [
        _lot(1, "2026-10-12", 100, "10.05"),
        _lot(2, "2026-10-14", 50, "13.00"),
    ]
    out = consume_lots(lots, 30)
    assert [(c.lot_id, c.qty, str(c.unit_cost)) for c in out] == [(1, 30, "10.05")]


def test_fifo_sprzedaz_wieksza_niz_stan_rzuca_ledger_error():
    lots = [_lot(1, "2026-10-12", 100, "10.00"), _lot(2, "2026-10-14", 50, "12.00")]
    with pytest.raises(LedgerError):
        consume_lots(lots, 151)


def test_fifo_qty_nierozpoznane():
    lots = [_lot(1, "2026-10-12", 100, "10.00")]
    with pytest.raises(ValueError):
        consume_lots(lots, 0)
    with pytest.raises(ValueError):
        consume_lots([], -5)


def test_fifo_partie_zerowe_pomijane():
    """Partia wyczerpana (qty=0) nie bierze udziału w konsumpcji."""
    lots = [
        _lot(1, "2026-10-12", 0, "10.00"),
        _lot(2, "2026-10-14", 50, "12.00"),
    ]
    out = consume_lots(lots, 50)
    assert [(c.lot_id, c.qty) for c in out] == [(2, 50)]


def test_fifo_kolejnosc_przy_remisie_dat():
    """Ten sam buy_date → kolejność po id partii."""
    lots = [
        _lot(7, "2026-10-12", 40, "10.00"),
        _lot(5, "2026-10-12", 40, "11.00"),
    ]
    out = consume_lots(lots, 60)
    assert [(c.lot_id, c.qty) for c in out] == [(5, 40), (7, 20)]
