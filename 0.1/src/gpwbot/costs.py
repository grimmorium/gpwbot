"""Koszty transakcji i dywidendy: prowizja, poślizg wykonania, podatek od dywidendy.

Czyste funkcje (A4); kwoty jako Decimal, zaokrąglanie ROUND_HALF_UP do 0,01 (A3).
Parametry rynkowe pochodzą z konfiguracji (`CostsCfg`), nie z kodu.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from gpwbot.config import CostsCfg
from gpwbot.domain import Side  # noqa: F401 — reeksport (D-034); źródło prawdy w `domain`

TWO_PLACES = Decimal("0.01")
_BPS_SCALE = Decimal(10_000)


def commission(value: Decimal, cfg: CostsCfg) -> Decimal:
    """Prowizja maklerska: max(wartość × pct/100, min), zaokrąglona do 0,01 (HALF_UP).

    Wartość 0 → prowizja 0 (brak transakcji; D-016); ujemna wartość rzuca ValueError.
    """
    if value < 0:
        raise ValueError(f"wartość nie może być ujemna (otrzymano {value})")
    if value == 0:
        return Decimal("0.00")
    raw = value * cfg.commission_pct / Decimal(100)
    result = max(raw, cfg.commission_min_pln)
    return result.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def apply_slippage(price: Decimal, side: Side, bps: int) -> Decimal:
    """Poślizg wykonania: BUY w górę, SELL w dół (zawsze niekorzystnie dla portfela).

    `bps` — punkty bazowe (1 bp = 0,01%). Wynik jest dokładnym iloczynem; kwantyzacja
    do ticka należy do fill model (S07).
    """
    if price <= 0:
        raise ValueError(f"cena musi być dodatnia (otrzymano {price})")
    bps_decimal = Decimal(bps)
    if bps_decimal < 0:
        raise ValueError(f"bps nie może być ujemne (otrzymano {bps})")
    factor = bps_decimal / _BPS_SCALE
    if side is Side.BUY:
        return price * (Decimal(1) + factor)
    if factor >= Decimal(1):
        raise ValueError("SELL z bps >= 10000 dałby cenę nie większą od zera")
    return price * (Decimal(1) - factor)


def net_dividend(gross: Decimal, tax_rate: Decimal) -> Decimal:
    """Dywidenda netto po podatku procentowym (`tax_rate` w %, np. 19 → 81,00 z 100)."""
    if gross < 0:
        raise ValueError(f"wartość brutto nie może być ujemna (otrzymano {gross})")
    if not Decimal(0) <= tax_rate <= Decimal(100):
        raise ValueError(f"tax_rate musi być w zakresie 0–100% (otrzymano {tax_rate})")
    net = gross * (Decimal(100) - tax_rate) / Decimal(100)
    return net.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
