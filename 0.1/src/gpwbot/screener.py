"""Screener (S05): percentylowy scoring przekrojowy i deterministyczna shortlista.

Konwencje (decyzja D-032 w docs/decisions.md):
- `score_universe`: ranga każdej cechy = uśredniona pozycja (1-based) wśród ważnych
  wartości / liczba ważnych → przedział [1/k, 1]; brak wartości (`None`/NaN) = ranga 0.
  Remisy dostają tę samą uśrednioną rangę; wynik nie zależy od kolejności wejścia.
- `trend` jako dosłowne 1 lub 0 (nie percentyl); `low_vol` to inwersja rangi `atr_pct`
  (niższa zmienność = wyższa ranga, Część I sekcja 8: "zmienność (ATR14 / cena)").
- `build_shortlist`: kolejność grup POSITION → TEXT_EVENT → TOP_SCORE; wewnątrz grupy
  sortowanie stabilne (−score, ticker) dla pozycji i TOP_SCORE oraz (−materiality, ticker)
  dla TEXT_EVENT. Przy przekroczeniu `max_total` obcinanie: najpierw TOP_SCORE od
  najniższego wyniku, potem TEXT_EVENT od najniższej istotności; pozycje nigdy nie są
  odcinane (przy ich liczbie > max_total shortlista = same pozycje).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from gpwbot.config import ScreenerCfg
from gpwbot.features import FeatureSet


class Reason(StrEnum):
    """Powody obecności tickera na shortliście (wartości to małe litery — wzorzec D-031)."""

    TOP_SCORE = "top_score"
    POSITION = "position"
    TEXT_EVENT = "text_event"


@dataclass(frozen=True)
class ShortlistItem:
    """Jeden ticker shortlisty z powodami i wynikiem scoringu."""

    ticker: str
    reasons: tuple[Reason, ...]
    score: float


@dataclass(frozen=True)
class Shortlist:
    """Shortlista runu; kolejność wg `build_shortlist` (pozycje → teksty → top score)."""

    items: tuple[ShortlistItem, ...] = ()


def _percentile_ranks(
    values: Mapping[str, float | None], *, descending: bool = False
) -> dict[str, float]:
    """Percentylowa ranga (uśredniane remisy; brak wartości → 0.0).

    `descending=True` oznacza, że niższa wartość cechy dostaje wyższą rangę
    (cecha typu "low_vol").
    """
    valid = {t: v for t, v in values.items() if v is not None and math.isfinite(v)}
    out: dict[str, float] = {t: 0.0 for t in values}
    k = len(valid)
    if k == 0:
        return out

    ordered = sorted(valid, key=lambda t: -valid[t] if descending else valid[t])
    i = 0
    while i < k:
        j = i
        v = valid[ordered[i]]
        while j + 1 < k and valid[ordered[j + 1]] == v:
            j += 1
        avg_pos = (i + 1 + j + 1) / 2.0  # pozycje 1-based: i+1 .. j+1
        for t in ordered[i : j + 1]:
            out[t] = avg_pos / k
        i = j + 1
    return out


def score_universe(features: Sequence[FeatureSet], cfg: ScreenerCfg) -> dict[str, float]:
    """Wynik scoringu przekrojowego: suma wag × rang dla pięciu cech.

    Cechy: `mom_6m`, `mom_12_1` (wyższa = lepiej), `trend` (1/0), `low_vol` ← inwersja
    `atr_pct`, `vol_z` (wyższy = lepiej). Zduplikowany ticker rzuca `ValueError`.
    Pusta lista → pusty słownik.
    """
    by_ticker: dict[str, FeatureSet] = {}
    for fs in features:
        if fs.ticker in by_ticker:
            raise ValueError(f"zduplikowany ticker w features: {fs.ticker}")
        by_ticker[fs.ticker] = fs

    def values(getter: Callable[[FeatureSet], float | None]) -> dict[str, float | None]:
        return {t: getter(fs) for t, fs in by_ticker.items()}

    r_mom_6m = _percentile_ranks(values(lambda f: f.mom_6m))
    r_mom_12_1 = _percentile_ranks(values(lambda f: f.mom_12_1))
    r_low_vol = _percentile_ranks(values(lambda f: f.atr_pct), descending=True)
    r_vol_z = _percentile_ranks(values(lambda f: f.vol_z))

    scores: dict[str, float] = {}
    for t, fs in by_ticker.items():
        trend = 1.0 if fs.trend_ok is True else 0.0
        scores[t] = (
            cfg.weight_mom_6m * r_mom_6m[t]
            + cfg.weight_mom_12_1 * r_mom_12_1[t]
            + cfg.weight_trend * trend
            + cfg.weight_low_vol * r_low_vol[t]
            + cfg.weight_vol_z * r_vol_z[t]
        )
    return scores


def build_shortlist(
    scores: Mapping[str, float],
    positions: Sequence[str],
    event_tickers: Mapping[str, float],
    cfg: ScreenerCfg,
) -> Shortlist:
    """Buduje shortlistę: pozycje ∪ teksty (materiality ≥ próg) ∪ top N po score.

    `event_tickers`: mapa ticker → materiality (0..1); poniżej progu ignorowane.
    Ticker spełniający kilka warunków ma jeden wpis z sumą powodów (kolejność stała:
    POSITION, TEXT_EVENT, TOP_SCORE). Tickery normalizowane do uppercase; wynik
    deterministyczny i niezależny od kolejności wejścia.
    """
    pos = {t.upper() for t in positions}
    events = {t.upper(): float(m) for t, m in event_tickers.items()}
    threshold = float(cfg.materiality_threshold)
    events_ok = {t: m for t, m in events.items() if m >= threshold}

    top_n_set: set[str] = set()
    if scores:
        ordered_scores = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        top_n_set = {t for t, _ in ordered_scores[: cfg.top_n]}

    def score_of(t: str) -> float:
        return scores.get(t, 0.0)

    # Grupy są rozłączne wg pierwszeństwa POSITION > TEXT_EVENT > TOP_SCORE —
    # ticker spełniający kilka warunków ma jeden wpis z sumą powodów.
    pos_group = sorted(pos, key=lambda t: (-score_of(t), t))
    ev_group = sorted((t for t in events_ok if t not in pos), key=lambda t: (-events_ok[t], t))
    top_group = sorted(
        (t for t in top_n_set if t not in pos and t not in events_ok),
        key=lambda t: (-score_of(t), t),
    )

    # Obcinanie przy przekroczeniu max_total: najpierw TOP_SCORE od końca listy
    # (najniższy wynik), potem TEXT_EVENT od końca (najniższa istotność); pozycje nigdy.
    capacity = cfg.max_total - len(pos_group)
    if capacity <= 0:
        kept_ev: list[str] = []
        kept_top: list[str] = []
    elif len(ev_group) >= capacity:
        kept_ev, kept_top = ev_group[:capacity], []
    else:
        kept_ev = list(ev_group)
        kept_top = top_group[: capacity - len(ev_group)]

    items: list[ShortlistItem] = []
    for t in [*pos_group, *kept_ev, *kept_top]:
        reasons = tuple(
            r
            for r in (Reason.POSITION, Reason.TEXT_EVENT, Reason.TOP_SCORE)
            if (r is Reason.POSITION and t in pos)
            or (r is Reason.TEXT_EVENT and t in events_ok)
            or (r is Reason.TOP_SCORE and t in top_n_set)
        )
        items.append(ShortlistItem(ticker=t, reasons=reasons, score=score_of(t)))
    return Shortlist(items=tuple(items))
