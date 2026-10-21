"""Testy S05: wskaźniki (`features`) — referencje, brzegi, brak look-ahead, własności.

Zakres z docs/sessions/S05.md: dla każdego wskaźnika referencyjna implementacja
(prosta pętla w czystym Pythonie) na serii losowej z ziarnem (tolerancja 1e-9);
małe przykłady ręczne dla SMA i ATR; przypadki brzegowe (RSI stała/wzrosty, krótsza
seria niż okno, wolumen stały → vol_z=None, NaN nie rozlewa się poza okno);
brak look-ahead `compute_features`; własności hypothesis (RSI ∈ [0,100], ATR ≥ 0,
SMA ∈ [min,max] okna); `compute_features` z krótką historią i flagą stale;
idempotencja `save_features`.
"""

from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.orm import Session

from gpwbot.calendar import GpwCalendar
from gpwbot.config import AppConfig, PathsCfg
from gpwbot.db.models import FeatureSnapshot, Instrument
from gpwbot.features import (
    FeatureSet,
    atr_wilder,
    avg_turnover,
    compute_features,
    dist_to_high,
    ema,
    momentum,
    momentum_12_1,
    realized_vol,
    rsi_wilder,
    save_features,
    sma,
    volume_zscore,
)

WARSAW = ZoneInfo("Europe/Warsaw")
TOL = 1e-9


# --------------------------------------------------------------------------- helpers


def make_df(
    closes: list[float],
    *,
    start: dt.date = dt.date(2025, 1, 6),
    dates: list[dt.date] | None = None,
    adj_close: list[float] | None = None,
    volumes: list[int | float | None] | None = None,
    turnovers: list[Decimal | float | None] | None = None,
) -> pd.DataFrame:
    """DataFrame w kształcie `repo.get_prices_df` (indeks date rosnąco, OHLC z close).

    `dates`: jawne daty indeksu (np. sesje GPW); domyślnie kolejne dni kalendarzowe od `start`.
    """
    n = len(closes)
    if dates is None:
        dates = [start + dt.timedelta(days=i) for i in range(n)]
    highs = [c * 1.02 for c in closes]
    lows = [c * 0.98 for c in closes]
    adj = list(closes) if adj_close is None else adj_close
    vols = volumes if volumes is not None else [10_000 + 7 * i for i in range(n)]
    turns = turnovers if turnovers is not None else [Decimal("500000") for _ in closes]
    df = pd.DataFrame(
        {
            "open": list(closes),
            "high": highs,
            "low": lows,
            "close": list(closes),
            "adj_close": adj,
            "volume": vols,
            "turnover": turns,
            "quality_flag": [None] * n,
        }
    )
    df.index = pd.DatetimeIndex(dates, name="date")
    for col in ("open", "high", "low", "close", "adj_close", "volume", "turnover"):
        df[col] = df[col].astype("float64")
    return df


def random_closes(n: int, seed: int) -> list[float]:
    """Losowa seria cen (random walk), zawsze dodatnia."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0, 0.01, size=n)
    prices = 50.0 * np.exp(np.cumsum(steps))
    return [float(p) for p in prices]


def ref_sma(values: list[float], n: int) -> list[float]:
    out: list[float] = []
    for i in range(len(values)):
        if i + 1 < n or any(math.isnan(v) for v in values[i - n + 1 : i + 1]):
            out.append(float("nan"))
        else:
            window = values[i - n + 1 : i + 1]
            s = 0.0
            for v in window:
                s += v
            out.append(s / n)
    return out


def ref_ema(values: list[float], n: int) -> list[float]:
    alpha = 2.0 / (n + 1.0)
    out: list[float] = []
    prev: float | None = None
    count = 0
    for x in values:
        if math.isnan(x):
            out.append(float("nan"))
            continue
        prev = x if prev is None else alpha * x + (1.0 - alpha) * prev
        count += 1
        out.append(prev if count >= n else float("nan"))
    return out


def ref_rsi_wilder(closes: list[float], n: int) -> list[float]:
    m = len(closes)
    out = [float("nan")] * m
    if m <= n:
        return out
    gains, losses = [], []
    for i in range(1, m):
        d = closes[i] - closes[i - 1]
        gains.append(d if d > 0 else 0.0)
        losses.append(-d if d < 0 else 0.0)

    def rsi(gain_avg: float, loss_avg: float) -> float:
        if gain_avg == 0.0 and loss_avg == 0.0:
            return 50.0
        if loss_avg == 0.0:
            return 100.0
        rs = gain_avg / loss_avg
        return 100.0 - 100.0 / (1.0 + rs)

    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    out[n] = rsi(ag, al)
    for i in range(n + 1, m):
        ag = (ag * (n - 1) + gains[i - 1]) / n
        al = (al * (n - 1) + losses[i - 1]) / n
        out[i] = rsi(ag, al)
    return out


def ref_atr_wilder(
    highs: list[float], lows: list[float], closes: list[float], n: int
) -> list[float]:
    m = len(closes)
    tr: list[float] = []
    for i in range(m):
        if i == 0:
            tr.append(highs[0] - lows[0])  # brak prev_close → zakres świecy (D-032)
        else:
            pc = closes[i - 1]
            tr.append(max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc)))
    out = [float("nan")] * m
    if m < n:
        return out
    a = sum(tr[:n]) / n
    out[n - 1] = a
    for i in range(n, m):
        a = (a * (n - 1) + tr[i]) / n
        out[i] = a
    return out


def ref_momentum(values: list[float], n: int) -> list[float]:
    m = len(values)
    out = [float("nan")] * m
    for i in range(n, m):
        if not math.isnan(values[i]) and not math.isnan(values[i - n]):
            out[i] = values[i] / values[i - n] - 1.0
    return out


def ref_momentum_12_1(values: list[float]) -> list[float]:
    m = len(values)
    out = [float("nan")] * m
    for i in range(252, m):
        if not math.isnan(values[i - 21]) and not math.isnan(values[i - 252]):
            out[i] = values[i - 21] / values[i - 252] - 1.0
    return out


def ref_volume_zscore(vols: list[float], n: int) -> list[float]:
    m = len(vols)
    out = [float("nan")] * m
    for i in range(n - 1, m):
        window = vols[i - n + 1 : i + 1]
        if any(math.isnan(v) for v in window):
            continue
        mean = sum(window) / n
        var = sum((v - mean) ** 2 for v in window) / (n - 1)
        std = math.sqrt(var)
        out[i] = (window[-1] - mean) / std if std > 0 else float("nan")
    return out


def ref_dist_to_high(values: list[float], n: int) -> list[float]:
    m = len(values)
    out = [float("nan")] * m
    for i in range(n - 1, m):
        window = values[i - n + 1 : i + 1]
        if any(math.isnan(v) for v in window):
            continue
        mx = max(window)
        out[i] = (values[i] / mx - 1.0) if not math.isnan(values[i]) else float("nan")
    return out


def ref_avg_turnover(turns: list[float], n: int) -> list[float]:
    m = len(turns)
    out = [float("nan")] * m
    for i in range(n - 1, m):
        window = turns[i - n + 1 : i + 1]
        if any(math.isnan(v) for v in window):
            continue
        out[i] = sum(window) / n
    return out


def ref_realized_vol(
    values: list[float], n: int, annualize: float = math.sqrt(252.0)
) -> list[float]:
    m = len(values)
    rets: list[float | None] = [None]
    for i in range(1, m):
        a, b = values[i - 1], values[i]
        if math.isnan(a) or math.isnan(b) or a <= 0:
            rets.append(None)
        else:
            rets.append(math.log(b / a))
    out = [float("nan")] * m
    for i in range(m):
        window = [r for r in rets[i - n + 1 : i + 1] if r is not None]
        if len(window) < n:
            continue
        mean = sum(window) / n
        var = sum((r - mean) ** 2 for r in window) / (n - 1)
        out[i] = math.sqrt(var) * annualize
    return out


def compare_series(actual: pd.Series, expected: list[float], name: str) -> None:
    assert len(actual) == len(expected), f"{name}: długość {len(actual)} != {len(expected)}"
    for i, (a, e) in enumerate(zip(actual.tolist(), expected, strict=True)):
        if math.isnan(e):
            assert pd.isna(a), f"{name}[{i}]: oczekiwano NaN, otrzymano {a}"
        else:
            assert not pd.isna(a), f"{name}[{i}]: oczekiwano {e}, otrzymano NaN"
            assert abs(a - e) <= TOL * max(1.0, abs(e)), (
                f"{name}[{i}]: |{a} - {e}| > tolerancja 1e-9"
            )


# Konfiguracja tylko po `cfg.market` (godziny faz) — kalendarz nie dotyka bazy/ścieżek.
_CFG = AppConfig(
    paths=PathsCfg(
        db_path=Path("unused.db"), data_dir=Path("."), raw_dir=Path("."), reports_dir=Path(".")
    )
)


@pytest.fixture
def calendar() -> GpwCalendar:
    return GpwCalendar(_CFG, override_path=None)


# ------------------------------------------------------------------- referencje losowe


@pytest.mark.parametrize("n", [30, 250])
def test_sma_matches_reference(n: int) -> None:
    values = random_closes(n, seed=7)
    s = pd.Series(values)
    compare_series(sma(s, 14), ref_sma(values, 14), "sma")


@pytest.mark.parametrize("n", [30, 250])
def test_ema_matches_reference(n: int) -> None:
    values = random_closes(n, seed=8)
    s = pd.Series(values)
    compare_series(ema(s, 14), ref_ema(values, 14), "ema")


@pytest.mark.parametrize("n", [30, 250])
def test_rsi_wilder_matches_reference(n: int) -> None:
    values = random_closes(n, seed=9)
    compare_series(rsi_wilder(pd.Series(values), 14), ref_rsi_wilder(values, 14), "rsi")


@pytest.mark.parametrize("n", [30, 250])
def test_atr_wilder_matches_reference(n: int) -> None:
    values = random_closes(n, seed=10)
    highs = [v * 1.03 for v in values]
    lows = [v * 0.97 for v in values]
    compare_series(
        atr_wilder(pd.Series(highs), pd.Series(lows), pd.Series(values), 14),
        ref_atr_wilder(highs, lows, values, 14),
        "atr",
    )


@pytest.mark.parametrize("n", [30, 250])
def test_momentum_matches_reference(n: int) -> None:
    values = random_closes(n, seed=11)
    compare_series(momentum(pd.Series(values), 63), ref_momentum(values, 63), "momentum")


@pytest.mark.parametrize("n", [30, 400])
def test_momentum_12_1_matches_reference(n: int) -> None:
    values = random_closes(n, seed=12)
    compare_series(momentum_12_1(pd.Series(values)), ref_momentum_12_1(values), "momentum_12_1")


@pytest.mark.parametrize("n", [30, 250])
def test_volume_zscore_matches_reference(n: int) -> None:
    rng = np.random.default_rng(13)
    vols = [float(v) for v in rng.integers(1_000, 100_000, size=n)]
    compare_series(volume_zscore(pd.Series(vols), 60), ref_volume_zscore(vols, 60), "vol_z")


@pytest.mark.parametrize("n", [30, 250])
def test_dist_to_high_matches_reference(n: int) -> None:
    values = random_closes(n, seed=14)
    compare_series(dist_to_high(pd.Series(values), 252), ref_dist_to_high(values, 252), "dist")


@pytest.mark.parametrize("n", [30, 250])
def test_avg_turnover_matches_reference(n: int) -> None:
    rng = np.random.default_rng(15)
    turns = [float(v) for v in rng.integers(100_000, 9_000_000, size=n)]
    compare_series(avg_turnover(pd.Series(turns), 20), ref_avg_turnover(turns, 20), "avg_turnover")


@pytest.mark.parametrize("n", [30, 250])
def test_realized_vol_matches_reference(n: int) -> None:
    values = random_closes(n, seed=16)
    compare_series(
        realized_vol(pd.Series(values), 20), ref_realized_vol(values, 20), "realized_vol"
    )


# ------------------------------------------------------------------- przykłady ręczne


def test_sma_hand_computed() -> None:
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = sma(s, 3).tolist()
    assert math.isnan(out[0]) and math.isnan(out[1])
    assert out[2] == pytest.approx(2.0)
    assert out[3] == pytest.approx(3.0)
    assert out[4] == pytest.approx(4.0)


def test_atr_hand_computed() -> None:
    # TR[0] = 10−9 = 1 (brak prev_close);
    # TR[1] = max(12−10, |12−9.5|, |10−9.5|) = 2.5;
    # TR[2] = max(13−12, |13−11|, |12−11|) = 2;
    # TR[3] = max(14−12, |14−12.5|, |12−12.5|) = 2.
    # ATR(2): NaN, (1+2.5)/2=1.75, (1.75·1+2)/2=1.875, (1.875·1+2)/2=1.9375
    high = pd.Series([10.0, 12.0, 13.0, 14.0])
    low = pd.Series([9.0, 10.0, 12.0, 12.0])
    close = pd.Series([9.5, 11.0, 12.5, 13.0])
    out = atr_wilder(high, low, close, n=2).tolist()
    assert math.isnan(out[0])
    assert out[1] == pytest.approx(1.75)
    assert out[2] == pytest.approx(1.875)
    assert out[3] == pytest.approx(1.9375)


def test_rsi_hand_computed() -> None:
    # n=2: close [10, 11, 10] → d = [+1, −1]; avg_gain = avg_loss = 0.5 → RSI = 50
    out = rsi_wilder(pd.Series([10.0, 11.0, 10.0]), n=2).tolist()
    assert math.isnan(out[0]) and math.isnan(out[1])
    assert out[2] == pytest.approx(50.0)

    # same wzrosty: avg_loss = 0, avg_gain > 0 → RSI = 100
    out_up = rsi_wilder(pd.Series([10.0, 11.0, 12.0]), n=2).tolist()
    assert out_up[2] == pytest.approx(100.0)

    # ręczny Wilder: close [10, 11, 9], n=2 → d=[+1, −2]; pierwsze okno: ag=(1+0)/2=0.5,
    # al=(0+2)/2=1.0; rs=0.5 → RSI = 100 − 100/1.5 ≈ 33.333
    out_w = rsi_wilder(pd.Series([10.0, 11.0, 9.0]), n=2).tolist()
    assert out_w[2] == pytest.approx(100.0 - 100.0 / (1.0 + 0.5))

    # rekurencja Wilder: close [10, 11, 9, 8], n=2 → d=[+1, −2, −1];
    # i=3: ag=(0.5·1+0)/2=0.25, al=(1.0·1+1)/2=1.0; rs=0.25 → RSI = 100 − 100/1.25 = 20
    out_r = rsi_wilder(pd.Series([10.0, 11.0, 9.0, 8.0]), n=2).tolist()
    assert out_r[3] == pytest.approx(20.0)


# ------------------------------------------------------------------- przypadki brzegowe


def test_rsi_constant_series_is_50() -> None:
    s = pd.Series([10.0] * 40)
    out = rsi_wilder(s, 14).tolist()
    assert all(math.isnan(v) for v in out[:14])
    assert all(v == pytest.approx(50.0) for v in out[14:])


def test_rsi_all_gains_is_100() -> None:
    s = pd.Series([float(i + 1) for i in range(40)])
    out = rsi_wilder(s, 14).tolist()
    assert all(v == pytest.approx(100.0) for v in out[14:])


def test_indicators_shorter_than_window_are_nan() -> None:
    s = pd.Series([float(i + 1) for i in range(5)])
    assert sma(s, 10).isna().all()
    assert ema(s, 10).isna().all()
    assert rsi_wilder(s, 14).isna().all()
    high = s * 1.01
    low = s * 0.99
    assert atr_wilder(high, low, s, 14).isna().all()
    assert momentum(s, 63).isna().all()
    assert volume_zscore(s, 60).isna().all()
    assert dist_to_high(s, 252).isna().all()
    assert avg_turnover(s, 20).isna().all()
    assert realized_vol(s, 20).isna().all()


def test_volume_zscore_constant_is_nan() -> None:
    s = pd.Series([100.0] * 80)
    out = volume_zscore(s, 60).tolist()
    assert all(math.isnan(v) for v in out[:59])
    assert all(math.isnan(v) for v in out[59:])


def test_nan_does_not_spread_beyond_window() -> None:
    values = random_closes(30, seed=21)
    values[10] = float("nan")  # luka w środku serii

    s = pd.Series(values)
    sma_out = sma(s, 5).tolist()
    # okno 5 kończące się na i zawiera indeks 10 tylko dla i ∈ {10..14}
    assert all(math.isnan(v) for i, v in enumerate(sma_out) if 10 <= i <= 14)
    for i in (9, 15, 29):
        assert not math.isnan(sma_out[i]), f"sma[{i}] poza oknem NaN"

    mom_out = momentum(s, 3).tolist()
    # wiersz i używa values[i] i values[i−3]: NaN wpływa tylko na i ∈ {10, 13}
    assert all(math.isnan(v) for i, v in enumerate(mom_out) if i in (10, 13))
    assert not math.isnan(mom_out[9]) and not math.isnan(mom_out[11])
    assert not math.isnan(mom_out[12]) and not math.isnan(mom_out[14])

    z_out = volume_zscore(s, 5).tolist()
    # okno 5: NaN wpływa na i ∈ {10..14}
    assert all(math.isnan(v) for i, v in enumerate(z_out) if 10 <= i <= 14)
    assert not math.isnan(z_out[9]) and not math.isnan(z_out[15]) and not math.isnan(z_out[29])


# ------------------------------------------------------------------- własności (hypothesis)


@settings(max_examples=60, deadline=None)
@given(st.lists(st.floats(min_value=1.0, max_value=1_000.0), min_size=30, max_size=80).map(list))
def test_rsi_bounded_between_0_and_100(values: list[float]) -> None:
    out = rsi_wilder(pd.Series(values), 14).dropna().tolist()
    assert all(0.0 <= v <= 100.0 for v in out)


@settings(max_examples=60, deadline=None)
@given(st.lists(st.floats(min_value=1.0, max_value=1_000.0), min_size=30, max_size=80))
def test_atr_non_negative(values: list[float]) -> None:
    high = pd.Series([v * 1.05 for v in values])
    low = pd.Series([v * 0.95 for v in values])
    out = atr_wilder(high, low, pd.Series(values), 14).dropna().tolist()
    assert all(v >= 0.0 for v in out)


@settings(max_examples=60, deadline=None)
@given(st.lists(st.floats(min_value=1.0, max_value=1_000.0), min_size=30, max_size=80))
def test_sma_within_window_range(values: list[float]) -> None:
    out = sma(pd.Series(values), 14).dropna().tolist()
    assert all(min(values) - TOL <= v <= max(values) + TOL for v in out)


# ------------------------------------------------------------------- compute_features


AS_OF_EOD = dt.datetime(2026, 3, 13, 23, 59, 59, tzinfo=WARSAW)  # piątek, koniec sesji
AS_OF_MORNING = dt.datetime(2026, 3, 13, 8, 15, tzinfo=WARSAW)  # przed otwarciem


def _features_df(n: int, seed: int = 31) -> pd.DataFrame:
    """n sesji GPW kończących się w piątek 2026-03-13 (bez luk)."""
    cal = GpwCalendar(_CFG, override_path=None)
    dates: list[dt.date] = []
    d = dt.date(2026, 3, 13)
    while len(dates) < n:
        if cal.is_session(d):
            dates.append(d)
        d -= dt.timedelta(days=1)
    dates.reverse()
    closes = random_closes(n, seed=seed)
    return make_df(closes, dates=list(dates))


def test_compute_features_short_history_mostly_none(calendar: GpwCalendar) -> None:
    df = _features_df(59)  # < 60 sesji
    fs = compute_features("AAA", df, AS_OF_EOD, calendar=calendar)
    assert isinstance(fs, FeatureSet)
    assert fs.ticker == "AAA"
    assert fs.as_of == AS_OF_EOD
    assert fs.n_obs == 59
    # pola wymagające ≥60 obserwacji:
    assert fs.mom_3m is None and fs.mom_6m is None and fs.mom_12_1 is None
    assert fs.sma200 is None and fs.trend_ok is None
    assert fs.dist_52w_high is None
    # pola dostępne przy 59 obserwacjach:
    assert fs.close is not None and isinstance(fs.close, Decimal)
    assert fs.ret_1d is not None
    assert fs.sma50 is not None and isinstance(fs.sma50, Decimal)
    assert fs.rsi14 is not None and 0.0 <= fs.rsi14 <= 100.0
    assert fs.atr14 is not None and fs.atr_pct is not None
    # vol_z wymaga pełnego okna 60 → przy 59 obserwacjach brak wartości
    assert fs.vol_z is None
    assert fs.avg_turnover_20d is not None and isinstance(fs.avg_turnover_20d, Decimal)
    assert fs.vol_ann is not None
    # ostatnia świeca = 2026-03-13 (sesja zakończona przed as_of) → nie jest stale
    assert fs.stale is False


def test_compute_features_empty_df(calendar: GpwCalendar) -> None:
    df = make_df([])
    fs = compute_features("AAA", df, AS_OF_EOD, calendar=calendar)
    assert fs.n_obs == 0
    assert fs.close is None and fs.stale is True


def test_compute_features_stale_flag(calendar: GpwCalendar) -> None:
    # dane do środy 2026-03-11; as_of w piątek po zamknięciu → czwartek bez świecy = stale
    df = _features_df(59).iloc[:-2]
    assert df.index[-1].date() == dt.date(2026, 3, 11)
    fs_eod = compute_features("AAA", df, AS_OF_EOD, calendar=calendar)
    assert fs_eod.stale is True

    # as_of w piątek rano (przed otwarciem): ostatnia zakończona sesja = czwartek → stale
    fs_morning = compute_features("AAA", df, AS_OF_MORNING, calendar=calendar)
    assert fs_morning.stale is True

    # dane do czwartku 2026-03-12; as_of w piątek rano → świeżo (czwartek zakończony)
    df_fresh = _features_df(59).iloc[:-1]
    assert df_fresh.index[-1].date() == dt.date(2026, 3, 12)
    fs_ok = compute_features("AAA", df_fresh, AS_OF_MORNING, calendar=calendar)
    assert fs_ok.stale is False


def test_compute_features_no_lookahead(calendar: GpwCalendar) -> None:
    """compute_features(df, d) == compute_features(df + przyszłość, d) dla wielu dat."""
    df = _features_df(300, seed=41)
    last_date = df.index[-1].date()
    # przedłużamy serię o 20 sesji "w przyszłości" (po ostatniej dacie wejścia)
    future_dates: list[dt.date] = []
    cal = GpwCalendar(_CFG, override_path=None)
    d = last_date + dt.timedelta(days=1)
    while len(future_dates) < 20:
        if cal.is_session(d):
            future_dates.append(d)
        d += dt.timedelta(days=1)
    extra_closes = random_closes(20, seed=42)
    df_future = pd.concat([df, make_df(extra_closes, dates=list(future_dates))])

    check_dates: list[dt.datetime] = []
    for i in (50, 100, 150, 200, 299):
        day = df.index[i].date()
        check_dates.append(dt.datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=WARSAW))

    for ts in check_dates:
        a = compute_features("AAA", df, ts, calendar=calendar)
        b = compute_features("AAA", df_future, ts, calendar=calendar)
        assert a == b, f"look-ahead wykryty dla as_of={ts.isoformat()}"


def test_compute_features_naive_as_of_raises(calendar: GpwCalendar) -> None:
    df = _features_df(10)
    with pytest.raises(ValueError):
        compute_features("AAA", df, dt.datetime(2026, 3, 13), calendar=calendar)


def test_compute_features_uses_adj_close_when_present(calendar: GpwCalendar) -> None:
    """Wskaźniki cenowe na adj_close; `close` w FeatureSet to surowa cena zamknięcia."""
    df = _features_df(260, seed=51)
    closes = df["close"].tolist()
    # dywidenda: ostatnia świeca ma close > adj_close (historia skorygowana w dół)
    adj = list(closes)
    adj[-1] = closes[-1] * 0.98
    df["adj_close"] = [float(v) for v in adj]

    fs = compute_features("AAA", df, AS_OF_EOD, calendar=calendar)
    # ret_1d liczone na adj: (adj[-1]/adj[-2] - 1) < (close[-1]/close[-2] - 1)
    raw_ret = closes[-1] / closes[-2] - 1.0
    assert fs.ret_1d is not None and fs.ret_1d < raw_ret
    # close w FeatureSet = surowa cena ostatniej świecy
    assert fs.close == Decimal(str(closes[-1]))


def test_compute_features_falls_back_to_close_without_adj(calendar: GpwCalendar) -> None:
    """Brak wartości adj_close → wskaźniki na `close` (np. dane Stooq bez korekt)."""
    df = _features_df(260, seed=51)
    df["adj_close"] = [float("nan")] * len(df)

    fs = compute_features("AAA", df, AS_OF_EOD, calendar=calendar)
    closes = df["close"].tolist()
    assert fs.ret_1d is not None
    assert abs(fs.ret_1d - (closes[-1] / closes[-2] - 1.0)) < TOL


# ------------------------------------------------------------------- save_features


@pytest.fixture
def session(tmp_db) -> Session:
    _, factory = tmp_db
    with factory() as s:
        yield s


def test_save_features_upserts_and_is_idempotent(session: Session, calendar: GpwCalendar) -> None:
    inst = Instrument(ticker="AAA")
    session.add(inst)
    session.flush()

    df = _features_df(300, seed=61)
    fs = compute_features("AAA", df, AS_OF_EOD, calendar=calendar)
    save_features(session, fs)
    session.commit()

    rows = list(session.scalars(select(FeatureSnapshot)).all())
    assert len(rows) == 1
    row = rows[0]
    assert row.instrument_id == inst.id
    assert row.as_of == AS_OF_EOD
    payload = dict(row.features_json)
    assert payload["ticker"] == "AAA"
    assert payload["n_obs"] == 300
    # kwoty w JSON jako tekst (wzorzec DecimalStr — A3; D-032)
    assert isinstance(payload["close"], str)
    assert Decimal(payload["close"]) == fs.close

    # idempotencja: ten sam (instrument_id, as_of) → update, nie duplikat
    fs2 = compute_features("AAA", df, AS_OF_EOD, calendar=calendar)
    save_features(session, fs2)
    session.commit()
    rows = list(session.scalars(select(FeatureSnapshot)).all())
    assert len(rows) == 1

    # inna chwila as_of → osobny wiersz
    ts2 = AS_OF_EOD - dt.timedelta(days=7)
    fs3 = compute_features("AAA", df, ts2, calendar=calendar)
    save_features(session, fs3)
    session.commit()
    assert len(list(session.scalars(select(FeatureSnapshot)).all())) == 2


def test_save_features_unknown_ticker_raises(session: Session, calendar: GpwCalendar) -> None:
    df = _features_df(10)
    fs = compute_features("BRAK", df, AS_OF_EOD, calendar=calendar)
    with pytest.raises(ValueError):
        save_features(session, fs)
