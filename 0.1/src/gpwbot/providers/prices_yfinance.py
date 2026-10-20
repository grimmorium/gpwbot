"""Zapasowy adapter cen yfinance (S03) — nieoficjalny, domyślnie wyłączony.

`yfinance` jest zależnością opcjonalną (import leniwy); warunki użycia i dostępność
do weryfikacji (V-03). Włączany flagą `providers.yfinance.enabled`.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

import pandas as pd

from gpwbot.config import ProvidersCfg
from gpwbot.errors import ConfigError, ProviderError
from gpwbot.providers.base import PriceBar


def symbol_for(ticker: str) -> str:
    """Symbol yfinance dla tickera GPW (sufiks `.WA`)."""
    return f"{ticker.strip().upper()}.WA"


def bars_from_yf_df(df: pd.DataFrame) -> list[PriceBar]:
    """Konwersja DataFrame'y yfinance (`Open/High/Low/Close[/Adj Close]/Volume`) na bary.

    Czyste funkcje — bez dostępu do sieci (S03). Brak kolumny `Adj Close` oznacza
    brak cen skorygowanych: `adj_close=None`.
    """
    if df.empty:
        return []
    missing = [col for col in ("Open", "High", "Low", "Close") if col not in df.columns]
    if missing:
        raise ProviderError(f"yfinance: brak kolumn w odpowiedzi: {', '.join(missing)}")

    bars: list[PriceBar] = []
    for ts, row in df.iterrows():
        d = ts.date() if hasattr(ts, "date") else dt.date.fromisoformat(str(ts))
        try:
            bar = PriceBar(
                date=d,
                open=Decimal(str(row["Open"])),
                high=Decimal(str(row["High"])),
                low=Decimal(str(row["Low"])),
                close=Decimal(str(row["Close"])),
                adj_close=_adj(row),
                volume=None if pd.isna(row.get("Volume")) else int(row["Volume"]),
            )
        except (InvalidOperation, ValueError) as exc:
            msg = f"yfinance: błędne wartości w wierszu {d.isoformat()}: {exc}"
            raise ProviderError(msg) from exc
        bars.append(bar)
    return bars


def _adj(row: pd.Series) -> Decimal | None:
    if "Adj Close" not in row.index or pd.isna(row["Adj Close"]):
        return None
    return Decimal(str(row["Adj Close"]))


class YFinancePriceProvider:
    """Dostawca cen dziennych przez `yfinance` (protokół `PriceProvider`)."""

    def __init__(self, cfg: ProvidersCfg) -> None:
        if not cfg.yfinance.enabled:
            raise ConfigError(
                "adapter yfinance jest wyłączony w konfiguracji (providers.yfinance.enabled)"
            )

    def get_daily(self, symbol: str, start: dt.date, end: dt.date) -> list[PriceBar]:
        """Pobiera ceny dzienne dla symbolu `.WA`; `yfinance` importowane leniwie."""
        try:
            import yfinance  # opcjonalna zależność — nie w [project.dependencies]
        except ImportError as exc:
            raise ProviderError(
                "yfinance nie jest zainstalowany (opcjonalna zależność adaptera zapasowego)"
            ) from exc

        data = yfinance.download(
            symbol, start=start.isoformat(), end=(end + dt.timedelta(days=1)).isoformat()
        )
        if data is None or len(data) == 0:
            return []
        # yfinance zwraca MultiIndex kolumn przy wielu symbolach — spłaszczamy.
        if isinstance(data.columns, pd.MultiIndex):
            data = data.droplevel(-1, axis=1) if data.columns.nlevels > 1 else data
        return bars_from_yf_df(data)
