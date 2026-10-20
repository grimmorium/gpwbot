"""Testy S03: zapasowy adapter yfinance (bez sieci; `yfinance` nie jest zainstalowany).

Zakres z docs/sessions/S03.md: opcjonalny adapter (.WA), włączany flagą w configu,
domyślnie wyłączony. Konwersja DataFrame'y testowana na syntetycznych danych.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pandas as pd
import pytest

from gpwbot.config import ProvidersCfg, YfinanceCfg
from gpwbot.errors import ConfigError, ProviderError
from gpwbot.providers.prices_yfinance import (
    YFinancePriceProvider,
    bars_from_yf_df,
    symbol_for,
)


def make_yf_df(dates: list[dt.date], with_adj: bool = True) -> pd.DataFrame:
    index = pd.DatetimeIndex([dt.datetime.combine(d, dt.time()) for d in dates])
    data: dict[str, object] = {
        "Open": [10.0 + i for i in range(len(dates))],
        "High": [10.5 + i for i in range(len(dates))],
        "Low": [9.8 + i for i in range(len(dates))],
        "Close": [10.2 + i for i in range(len(dates))],
        "Volume": [1000 * (i + 1) for i in range(len(dates))],
    }
    if with_adj:
        data["Adj Close"] = [9.5 + i for i in range(len(dates))]
    return pd.DataFrame(data, index=index)


class TestSymbolFor:
    def test_sufiks_wa(self) -> None:
        assert symbol_for("PKN") == "PKN.WA"

    def test_normalizacja_wielkości_liter(self) -> None:
        assert symbol_for("pkn ") == "PKN.WA"


class TestBarsFromYfDf:
    def test_poprawna_konwersja(self) -> None:
        df = make_yf_df([dt.date(2026, 10, 19), dt.date(2026, 10, 20)])
        bars = bars_from_yf_df(df)
        assert len(bars) == 2
        first = bars[0]
        assert first.date == dt.date(2026, 10, 19)
        assert first.open == Decimal("10.0") and first.close == Decimal("10.2")
        assert first.adj_close == Decimal("9.5")
        assert first.volume == 1000

    def test_brak_kolumny_adj_close(self) -> None:
        df = make_yf_df([dt.date(2026, 10, 19)], with_adj=False)
        bars = bars_from_yf_df(df)
        assert bars[0].adj_close is None

    def test_pusty_dataframe(self) -> None:
        assert bars_from_yf_df(make_yf_df([])) == []

    def test_brak_wymaganej_kolumny_rzuca_provider_error(self) -> None:
        df = make_yf_df([dt.date(2026, 10, 19)]).drop(columns=["Close"])
        with pytest.raises(ProviderError, match="Close"):
            bars_from_yf_df(df)


class TestProviderFlag:
    def test_domyślnie_wyłączony_rzuca_config_error(self) -> None:
        with pytest.raises(ConfigError, match="providers.yfinance.enabled"):
            YFinancePriceProvider(ProvidersCfg())

    def test_get_daily_yfinance_niezainstalowany(self) -> None:
        cfg = ProvidersCfg(yfinance=YfinanceCfg(enabled=True))
        provider = YFinancePriceProvider(cfg)
        with pytest.raises(ProviderError, match="nie jest zainstalowany"):
            provider.get_daily("PKN.WA", dt.date(2026, 10, 1), dt.date(2026, 10, 31))
