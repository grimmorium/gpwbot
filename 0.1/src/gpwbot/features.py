"""Wskaźniki techniczne i snapshoty cech (S05): czyste funkcje na DataFrame, bez look-ahead.

Konwencje (decyzje D-032/D-033 w docs/decisions.md):
- `features.py` operuje na DataFrame w kształcie `repo.get_prices_df`: indeks `date`
  (`DatetimeIndex`, rosnąco) + kolumny OHLC (+ opcjonalnie `adj_close`, `volume`,
  `turnover`). Wiersze są deduplikowane i sortowane defensywnie.
- Do wskaźników cenowych (momentum, SMA/EMA, RSI, odległość od maksimum) używana jest
  kolumna `adj_close`; jeśli jej brak lub nie ma w niej żadnych wartości — `close`.
- ATR liczone na surowych OHLC: służy poziomom stop-loss (S08), więc musi być
  porównywalne z aktualną ceną transakcyjną.
- Wilder: pierwsza średnia = zwykła średnia z pierwszych `n` obserwacji, dalej
  `avg = (avg_prev × (n−1) + x) / n`. TR[0] = high − low (brak prev_close); pierwszy
  ATR na indeksie `n−1`, pierwszy RSI na indeksie `n` (deltę liczymy od drugiej ceny).
- RSI: średnia strata = 0 i zysk > 0 → 100; obie = 0 → 50.
- `compute_features(ticker, df, as_of, *, calendar)` tnie `df` do wierszy o dacie ≤
  `as_of` (bez look-ahead); `stale` wg `quality.is_fresh` wobec kalendarza GPW —
  dlatego wymagany jest parametr `calendar`.
"""

from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from gpwbot.calendar import GpwCalendar
from gpwbot.db import repo
from gpwbot.db.models import FeatureSnapshot
from gpwbot.quality import is_fresh

WARSAW = ZoneInfo("Europe/Warsaw")


# --------------------------------------------------------------------------- wskaźniki


def sma(s: pd.Series, n: int) -> pd.Series:
    """Średnia krocząca; `min_periods=n` → NaN przed pełnym oknem."""
    if n < 1:
        raise ValueError("n musi być dodatnie")
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    """EMA (`span=n`, `adjust=False`, `min_periods=n`)."""
    if n < 1:
        raise ValueError("n musi być dodatnie")
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def _rsi_from_averages(avg_gain: float, avg_loss: float) -> float:
    """RSI z uśrednionych zysków/strat (reguły brzegowe wg specyfikacji S05)."""
    if avg_gain == 0.0 and avg_loss == 0.0:
        return 50.0
    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def rsi_wilder(close: pd.Series, n: int = 14) -> pd.Series:
    """RSI wg Wildera; pierwszy ważny wynik na indeksie `n` (delt od drugiej ceny)."""
    if n < 1:
        raise ValueError("n musi być dodatnie")
    c = close.to_numpy(dtype="float64")
    m = len(c)
    out = np.full(m, np.nan)
    if m <= n:
        return pd.Series(out, index=close.index)

    delta = np.diff(c)
    gain = np.where(delta > 0.0, delta, 0.0)
    loss = np.where(delta < 0.0, -delta, 0.0)
    avg_gain = float(gain[:n].mean())
    avg_loss = float(loss[:n].mean())
    out[n] = _rsi_from_averages(avg_gain, avg_loss)
    for i in range(n + 1, m):
        avg_gain = (avg_gain * (n - 1) + gain[i - 1]) / n
        avg_loss = (avg_loss * (n - 1) + loss[i - 1]) / n
        out[i] = _rsi_from_averages(avg_gain, avg_loss)
    return pd.Series(out, index=close.index)


def atr_wilder(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """ATR wg Wildera; TR[0] = high − low (brak prev_close), pierwszy wynik na `n−1`."""
    if n < 1:
        raise ValueError("n musi być dodatnie")
    h = high.to_numpy(dtype="float64")
    lo = low.to_numpy(dtype="float64")
    c = close.to_numpy(dtype="float64")
    m = len(c)
    out = np.full(m, np.nan)
    if m < n:
        return pd.Series(out, index=close.index)

    tr = np.empty(m, dtype="float64")
    tr[0] = h[0] - lo[0]
    for i in range(1, m):
        pc = c[i - 1]
        tr[i] = max(h[i] - lo[i], abs(h[i] - pc), abs(lo[i] - pc))

    avg = float(tr[:n].mean())
    out[n - 1] = avg
    for i in range(n, m):
        avg = (avg * (n - 1) + tr[i]) / n
        out[i] = avg
    return pd.Series(out, index=close.index)


def momentum(adj: pd.Series, n: int) -> pd.Series:
    """Stopa zwrotu z `n` sesji: `adj / adj.shift(n) − 1`."""
    if n < 1:
        raise ValueError("n musi być dodatnie")
    return adj / adj.shift(n) - 1.0


def momentum_12_1(adj: pd.Series) -> pd.Series:
    """Momentum 12-1 (z pominięciem ostatniego miesiąca): `shift(21)/shift(252) − 1`."""
    return adj.shift(21) / adj.shift(252) - 1.0


def volume_zscore(v: pd.Series, n: int = 60) -> pd.Series:
    """Z-score wolumenu względem okna `n`; std == 0 (wolumen stały) → NaN."""
    if n < 2:
        raise ValueError("wymagane n >= 2")
    mean = v.rolling(n).mean()
    std = v.rolling(n).std(ddof=1)
    z = (v - mean) / std
    return z.where(std > 0)


def dist_to_high(close: pd.Series, n: int = 252) -> pd.Series:
    """Odległość od `n`-sesyjnego maksimum: `close / rolling_max − 1` (≤ 0)."""
    if n < 1:
        raise ValueError("n musi być dodatnie")
    return close / close.rolling(n).max() - 1.0


def avg_turnover(t: pd.Series, n: int = 20) -> pd.Series:
    """Średni obrót z okna `n` sesji; NaN w oknie → NaN (brak wypełniania zerami)."""
    if n < 1:
        raise ValueError("n musi być dodatnie")
    return t.rolling(n).mean()


def realized_vol(adj: pd.Series, n: int = 20) -> pd.Series:
    """Zmienność zrealizowana: odchylenie std logarytmicznych stóp zwrotu (okno `n`) × √252."""
    if n < 2:
        raise ValueError("wymagane n >= 2")
    ratio = adj / adj.shift(1)
    rets = pd.Series(np.log(ratio.to_numpy(dtype="float64")), index=adj.index)
    return rets.rolling(n).std(ddof=1) * math.sqrt(252.0)


# --------------------------------------------------------------------------- FeatureSet


class FeatureSet(BaseModel):
    """Snapshot cech jednego instrumentu na chwilę `as_of` (frozen, pydantic).

    Pola z niewystarczającą historią mają wartość `None`. Ceny/kwoty jako `Decimal`,
    bezwymiarowe wskaźniki jako `float` (warstwa cen i wskaźników — A3).
    """

    model_config = ConfigDict(frozen=True)

    ticker: str
    as_of: dt.datetime
    close: Decimal | None = None  # surowa cena zamknięcia ostatniej świecy
    ret_1d: float | None = None
    mom_3m: float | None = None  # 63 sesje
    mom_6m: float | None = None  # 126 sesji
    mom_12_1: float | None = None
    sma50: Decimal | None = None
    sma200: Decimal | None = None
    trend_ok: bool | None = None  # close > sma200 i sma50 > sma200; None gdy brak SMA200
    rsi14: float | None = None
    atr14: Decimal | None = None  # na surowych OHLC (D-032)
    atr_pct: float | None = None  # atr14 / close
    vol_z: float | None = None
    dist_52w_high: float | None = None
    avg_turnover_20d: Decimal | None = None
    vol_ann: float | None = None
    n_obs: int = 0
    stale: bool = False


def _last_value(series: pd.Series) -> float | None:
    """Ostatnia wartość serii albo `None` (seria pusta / NaN)."""
    if series.empty:
        return None
    v = series.iloc[-1]
    return None if pd.isna(v) else float(v)


def _to_decimal(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def compute_features(
    ticker: str, df: pd.DataFrame, as_of: dt.datetime, *, calendar: GpwCalendar
) -> FeatureSet:
    """Liczy `FeatureSet` wyłącznie na wierszach o dacie ≤ `as_of` (brak look-ahead).

    Wymagany `calendar` (GpwCalendar z konfiguracji): flaga `stale` jest definiowana
    wobec ostatniej zakończonej sesji GPW (`quality.is_fresh`). Naive `as_of` rzuca
    `ValueError`. Pusty `df` → wszystkie pola `None`, `n_obs=0`, `stale=True`.
    """
    if as_of.tzinfo is None or as_of.tzinfo.utcoffset(as_of) is None:
        raise ValueError("as_of musi być datą świadomą strefy czasowej")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("df musi mieć indeks DatetimeIndex o nazwie 'date'")

    idx = df.index
    cmp_idx = idx.tz_localize(WARSAW) if idx.tz is None else idx.tz_convert(WARSAW)
    # Porównanie Timestamp vs aware datetime na poziomie Pythona (pandas radzi sobie ze strefami).
    mask = np.array([ts <= as_of for ts in cmp_idx], dtype="bool")
    cut = df[mask]
    cut = cut[~cut.index.duplicated(keep="first")].sort_index()

    if cut.empty:
        return FeatureSet(ticker=ticker, as_of=as_of, n_obs=0, stale=True)

    has_adj = "adj_close" in cut.columns and bool(cut["adj_close"].notna().any())
    adj = cut["adj_close"] if has_adj else cut["close"]

    last_row = cut.iloc[-1]
    close_last = _last_value(pd.Series([last_row["close"]]))
    d_last = cut.index[-1].date()

    ret_1d = _last_value(momentum(adj, 1))
    mom_3m = _last_value(momentum(adj, 63))
    mom_6m = _last_value(momentum(adj, 126))
    mom_12_1 = _last_value(momentum_12_1(adj))
    sma50_f = _last_value(sma(adj, 50))
    sma200_f = _last_value(sma(adj, 200))
    rsi14 = _last_value(rsi_wilder(adj, 14))
    atr14_f = _last_value(atr_wilder(cut["high"], cut["low"], cut["close"], 14))
    vol_z = _last_value(volume_zscore(cut["volume"], 60))
    dist_52w_high = _last_value(dist_to_high(adj, 252))
    turnover_series = cut["turnover"] if "turnover" in cut.columns else None
    avg_turnover_f = (
        _last_value(avg_turnover(turnover_series, 20)) if turnover_series is not None else None
    )
    vol_ann = _last_value(realized_vol(adj, 20))

    trend_ok: bool | None = None
    if close_last is not None and sma50_f is not None and sma200_f is not None:
        trend_ok = close_last > sma200_f and sma50_f > sma200_f

    atr_pct: float | None = None
    if atr14_f is not None and close_last is not None and close_last > 0:
        atr_pct = atr14_f / close_last

    return FeatureSet(
        ticker=ticker,
        as_of=as_of,
        close=_to_decimal(close_last),
        ret_1d=ret_1d,
        mom_3m=mom_3m,
        mom_6m=mom_6m,
        mom_12_1=mom_12_1,
        sma50=_to_decimal(sma50_f),
        sma200=_to_decimal(sma200_f),
        trend_ok=trend_ok,
        rsi14=rsi14,
        atr14=_to_decimal(atr14_f),
        atr_pct=atr_pct,
        vol_z=vol_z,
        dist_52w_high=dist_52w_high,
        avg_turnover_20d=_to_decimal(avg_turnover_f),
        vol_ann=vol_ann,
        n_obs=len(cut),
        stale=not is_fresh(d_last, as_of, calendar),
    )


def save_features(session: Session, fs: FeatureSet) -> None:
    """Zapisuje snapshot do `feature_snapshots` (upsert po `instrument_id, as_of`).

    Instrument jest wyszukiwany po tickerze z `FeatureSet`; nieznany ticker rzuca
    `ValueError`. Payload JSON przez `model_dump(mode="json")` (Decimal → float).
    Flush bez commitu — transakcja należy do wywołującego.
    """
    inst = repo.get_instrument_by_ticker(session, fs.ticker)
    if inst is None:
        raise ValueError(f"nieznany ticker w feature_snapshots: {fs.ticker}")

    payload: dict[str, object] = fs.model_dump(mode="json")
    stmt = sqlite_insert(FeatureSnapshot).values(
        instrument_id=inst.id, as_of=fs.as_of, features_json=payload
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["instrument_id", "as_of"],
        set_={"features_json": stmt.excluded["features_json"]},
    )
    session.execute(stmt)
