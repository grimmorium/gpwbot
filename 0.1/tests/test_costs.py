"""Testy S02: koszty transakcji i dywidendy (`gpwbot.costs`).

Zakres z docs/sessions/S02.md: prowizja minimalna/procentowa, ROUND_HALF_UP, wartosc 0;
kierunek poslizgu (BUY w gore, SELL w dol); net_dividend(100, 19%) = 81.00.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from gpwbot.config import CostsCfg
from gpwbot.costs import Side, apply_slippage, commission, net_dividend

# --- prowizja ------------------------------------------------------------------


class TestCommission:
    def test_minimum_for_small_value(self) -> None:
        cfg = CostsCfg()  # 0.30%, min 5 PLN
        assert commission(Decimal("1000"), cfg) == Decimal("5.00")

    def test_percentage_for_large_value(self) -> None:
        cfg = CostsCfg()
        assert commission(Decimal("10000"), cfg) == Decimal("30.00")

    def test_boundary_raw_equals_minimum(self) -> None:
        # 1600 * 0.25% = 4.00 = minimum → wynik rowny minimum (granica max())
        cfg = CostsCfg(commission_pct=Decimal("0.25"), commission_min_pln=Decimal("4"))
        assert commission(Decimal("1600"), cfg) == Decimal("4.00")

    def test_round_half_up(self) -> None:
        # 402 * 0.25% = 1.005 → HALF_UP daje 1.01 (HALF_EVEN daloby 1.00)
        cfg = CostsCfg(commission_pct=Decimal("0.25"), commission_min_pln=Decimal("0"))
        assert commission(Decimal("402"), cfg) == Decimal("1.01")

    def test_zero_value_no_commission(self) -> None:
        # D-016: brak transakcji → brak prowizji (nie minimum!)
        assert commission(Decimal("0"), CostsCfg()) == Decimal("0.00")

    def test_negative_value_raises(self) -> None:
        with pytest.raises(ValueError):
            commission(Decimal("-1"), CostsCfg())


# --- poslizg --------------------------------------------------------------------


class TestApplySlippage:
    def test_buy_moves_up(self) -> None:
        # 100 * (1 + 15/10000) = 100.15 — dokladnie, bez artefaktow float
        assert apply_slippage(Decimal("100"), Side.BUY, 15) == Decimal("100.15")

    def test_sell_moves_down(self) -> None:
        # 100 * (1 - 15/10000) = 99.85
        assert apply_slippage(Decimal("100"), Side.SELL, 15) == Decimal("99.85")

    def test_zero_bps_no_change(self) -> None:
        for side in (Side.BUY, Side.SELL):
            assert apply_slippage(Decimal("42.37"), side, 0) == Decimal("42.37")

    def test_always_unfavorable_to_portfolio(self) -> None:
        price = Decimal("123.45")
        assert apply_slippage(price, Side.BUY, 15) > price
        assert apply_slippage(price, Side.SELL, 15) < price

    @pytest.mark.parametrize(
        ("price", "bps"),
        [("0", 15), ("-3", 15), ("100", -1)],
    )
    def test_invalid_inputs_raise(self, price: str, bps: int) -> None:
        with pytest.raises(ValueError):
            apply_slippage(Decimal(price), Side.BUY, bps)

    def test_sell_with_100_percent_bps_raises(self) -> None:
        # czynnik (1 - 1.0) = 0 → cena <= 0; strzegamy przed takim wejsciem
        with pytest.raises(ValueError):
            apply_slippage(Decimal("100"), Side.SELL, 10_000)


# --- dywidendy -------------------------------------------------------------------


class TestNetDividend:
    def test_spec_case(self) -> None:
        assert net_dividend(Decimal("100"), Decimal("19")) == Decimal("81.00")

    def test_round_half_up_on_result(self) -> None:
        # 1234.56 * (1 - 0.19) = 999.9936 → 999.99
        assert net_dividend(Decimal("1234.56"), Decimal("19")) == Decimal("999.99")

    def test_zero_gross(self) -> None:
        assert net_dividend(Decimal("0"), Decimal("19")) == Decimal("0.00")

    @pytest.mark.parametrize("rate", ["-1", "101"])
    def test_rate_out_of_range_raises(self, rate: str) -> None:
        with pytest.raises(ValueError):
            net_dividend(Decimal("100"), Decimal(rate))

    def test_negative_gross_raises(self) -> None:
        with pytest.raises(ValueError):
            net_dividend(Decimal("-10"), Decimal("19"))
