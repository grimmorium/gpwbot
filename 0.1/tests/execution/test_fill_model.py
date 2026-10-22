"""Testy czystego modelu realizacji zleceń (S07): tabela przypadków `simulate_fill`.

Pokrycie: limit spełniony/niespełniony, równość open==limit, poślizg BUY/SELL,
zaokrąglenie do ticka (UP/DOWN/punkt stały), przycięcie do limitu, prowizja minimalna
i procentowa, limit wolumenu (granica, przekroczenie, volume=None).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from gpwbot.config import CostsCfg
from gpwbot.db.repo import PriceBar
from gpwbot.domain import OrderIntent, Side
from gpwbot.execution.fill_model import FillResult, rejection_reason, simulate_fill
from gpwbot.ticks import TickBand, TickTable

D = Decimal
DATE = dt.date(2026, 10, 15)
TICKS = TickTable({"default": [TickBand(None, D("0.10"))]})


def _bar(open_: Decimal, volume: int | None = None) -> PriceBar:
    return PriceBar(date=DATE, open=open_, high=open_, low=open_, close=open_, volume=volume)


# Wiersz: nazwa, side, open, limit, qty, volume, bps, fill=(cena, prowizja, poślizg)|None, powód
CASES = [
    # limit spełniony + prowizja minimalna (3.03 < 5) + tick UP po poślizgu BUY
    (
        "buy_limit_met_min_comm",
        "BUY",
        "10.00",
        "10.50",
        100,
        None,
        15,
        ("10.10", "5.00", "10.00"),
        None,
    ),
    # open == limit: poślizg+tick daje 10.10 > limit → przycięcie do limitu
    (
        "buy_open_eq_limit_clip",
        "BUY",
        "10.00",
        "10.00",
        100,
        None,
        15,
        ("10.00", "5.00", "0"),
        None,
    ),
    # otwarcie powyżej limitu → brak realizacji
    ("buy_limit_not_met", "BUY", "10.60", "10.50", 100, None, 15, None, "limit_not_met"),
    # SELL: poślizg w dół + tick DOWN; prowizja minimalna (2.97 < 5)
    (
        "sell_limit_met_min_comm",
        "SELL",
        "10.00",
        "9.50",
        100,
        None,
        15,
        ("9.90", "5.00", "10.00"),
        None,
    ),
    # open == limit: zaokrąglenie w dół daje 9.90 < limit → przycięcie do limitu
    (
        "sell_open_eq_limit_clip",
        "SELL",
        "10.00",
        "10.00",
        100,
        None,
        15,
        ("10.00", "5.00", "0"),
        None,
    ),
    # otwarcie poniżej limitu SELL → brak realizacji
    ("sell_limit_not_met", "SELL", "9.40", "9.50", 100, None, 15, None, "limit_not_met"),
    # bps=0: czyste zaokrąglenie do ticka — BUY w górę (10.02 → 10.10)
    ("buy_tick_round_up_bps0", "BUY", "10.02", None, 100, None, 0, ("10.10", "5.00", "8.00"), None),
    # bps=0: czyste zaokrąglenie do ticka — SELL w dół (10.08 → 10.00)
    (
        "sell_tick_round_down_bps0",
        "SELL",
        "10.08",
        None,
        100,
        None,
        0,
        ("10.00", "5.00", "8.00"),
        None,
    ),
    # poślizg+tick daje 10.10 > limit → przycięcie (cena otwarcia poniżej limitu)
    (
        "buy_clip_after_rounding",
        "BUY",
        "9.99",
        "10.00",
        100,
        None,
        25,
        ("10.00", "5.00", "1.00"),
        None,
    ),
    # zaokrąglenie w dół daje 9.90 < limit → przycięcie (cena otwarcia powyżej limitu)
    (
        "sell_clip_after_rounding",
        "SELL",
        "10.02",
        "10.00",
        100,
        None,
        25,
        ("10.00", "5.00", "2.00"),
        None,
    ),
    # dokładna wielokrotność ticka = punkt stały; prowizja procentowa (30 > min)
    (
        "buy_exact_tick_pct_commission",
        "BUY",
        "10.00",
        None,
        1000,
        None,
        0,
        ("10.00", "30.00", "0"),
        None,
    ),
    # granica limitu wolumenu dozwolona: qty == volume × 5% / 100 (20000 → 1000)
    ("volume_boundary_ok", "BUY", "10.00", None, 1000, 20_000, 0, ("10.00", "30.00", "0"), None),
    # przekroczenie limitu wolumenu → brak realizacji (bez częściowych)
    ("volume_exceeded", "BUY", "10.00", None, 1001, 20_000, 0, None, "volume_limit"),
    # volume=None: udziału nie da się policzyć → kontrola pominięta
    (
        "volume_none_no_check",
        "BUY",
        "10.00",
        None,
        2_000_000,
        None,
        0,
        ("10.00", "60000.00", "0"),
        None,
    ),
]


@pytest.mark.parametrize(
    ("name", "side_s", "open_s", "limit_s", "qty", "volume", "bps", "expected", "reason"),
    CASES,
    ids=[case[0] for case in CASES],
)
def test_simulate_fill_tabela_przypadków(
    name: str,
    side_s: str,
    open_s: str,
    limit_s: str | None,
    qty: int,
    volume: int | None,
    bps: int,
    expected: tuple[str, str, str] | None,
    reason: str | None,
) -> None:
    cfg = CostsCfg(slippage_bps=bps)  # prowizja i limit wolumenu domyślne (0.30/5 PLN; 5%)
    order = OrderIntent(
        ticker="AAA",
        side=Side(side_s),
        qty=qty,
        limit_price=None if limit_s is None else D(limit_s),
    )
    bar = _bar(D(open_s), volume)

    result = simulate_fill(order, bar, cfg, TICKS)

    if expected is None:
        assert result is None, f"{name}: oczekiwano braku filla"
    else:
        price, comm, slip = (D(part) for part in expected)
        assert result == FillResult(price=price, qty=qty, commission=comm, slippage_cost=slip), name


@pytest.mark.parametrize(
    ("name", "side_s", "open_s", "limit_s", "qty", "volume", "bps", "expected", "reason"),
    CASES,
    ids=[case[0] for case in CASES],
)
def test_rejection_reason_spójny_z_simulate_fill(
    name: str,
    side_s: str,
    open_s: str,
    limit_s: str | None,
    qty: int,
    volume: int | None,
    bps: int,
    expected: tuple[str, str, str] | None,
    reason: str | None,
) -> None:
    """`rejection_reason` zgadza się z wynikiem `simulate_fill` (wspólne predykaty)."""
    cfg = CostsCfg(slippage_bps=bps)
    order = OrderIntent(
        ticker="AAA",
        side=Side(side_s),
        qty=qty,
        limit_price=None if limit_s is None else D(limit_s),
    )
    bar = _bar(D(open_s), volume)

    assert rejection_reason(order, bar, cfg) == reason


@pytest.mark.parametrize("qty", [0, -5])
def test_simulate_fill_qty_nie_dodatnie_rzuca(qty: int) -> None:
    order = OrderIntent(ticker="AAA", side=Side.BUY, qty=qty, limit_price=D("10.00"))
    with pytest.raises(ValueError):
        simulate_fill(order, _bar(D("10.00")), CostsCfg(), TICKS)
