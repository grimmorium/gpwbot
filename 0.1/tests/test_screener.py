"""Testy S05: screener — scoring przekrojowy i shortlista (determinizm, limity).

Zakres z docs/sessions/S05.md: wynik niezależny od kolejności wejścia; rangi z remisami;
waga zero wyłącza cechę; pozycje zawsze w shortlist; limity i kolejność obcinania;
ticker spełniający kilka warunków ma sumę `reasons`; pusta lista wejściowa.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from itertools import permutations
from zoneinfo import ZoneInfo

import pytest

from gpwbot.config import ScreenerCfg
from gpwbot.features import FeatureSet
from gpwbot.screener import Reason, Shortlist, build_shortlist, score_universe

WARSAW = ZoneInfo("Europe/Warsaw")
AS_OF = dt.datetime(2026, 3, 13, 23, 59, 59, tzinfo=WARSAW)


def make_fs(
    ticker: str,
    *,
    mom_6m: float | None = None,
    mom_12_1: float | None = None,
    trend_ok: bool | None = None,
    atr_pct: float | None = None,
    vol_z: float | None = None,
) -> FeatureSet:
    """Minimalny `FeatureSet` z wybranymi cechami scoringowymi."""
    return FeatureSet(
        ticker=ticker,
        as_of=AS_OF,
        close=Decimal("10"),
        ret_1d=0.0,
        mom_3m=0.0,
        mom_6m=mom_6m,
        mom_12_1=mom_12_1,
        sma50=None,
        sma200=None,
        trend_ok=trend_ok,
        rsi14=None,
        atr14=None,
        atr_pct=atr_pct,
        vol_z=vol_z,
        dist_52w_high=None,
        avg_turnover_20d=None,
        vol_ann=None,
        n_obs=300,
        stale=False,
    )


def cfg(
    *,
    top_n: int = 25,
    max_total: int = 40,
    materiality_threshold: Decimal | str = "0.6",
    w_mom_6m: float = 1.0,
    w_mom_12_1: float = 1.0,
    w_trend: float = 1.0,
    w_low_vol: float = 1.0,
    w_vol_z: float = 1.0,
) -> ScreenerCfg:
    return ScreenerCfg(
        top_n=top_n,
        max_total=max_total,
        materiality_threshold=Decimal(str(materiality_threshold)),
        weight_mom_6m=w_mom_6m,
        weight_mom_12_1=w_mom_12_1,
        weight_trend=w_trend,
        weight_low_vol=w_low_vol,
        weight_vol_z=w_vol_z,
    )


# ------------------------------------------------------------------- score_universe


def test_score_independent_of_input_order() -> None:
    features = [
        make_fs("AAA", mom_6m=0.10, mom_12_1=-0.05, trend_ok=True, atr_pct=0.03, vol_z=1.2),
        make_fs("BBB", mom_6m=0.20, mom_12_1=0.15, trend_ok=False, atr_pct=0.08, vol_z=-0.4),
        make_fs("CCC", mom_6m=None, mom_12_1=0.02, trend_ok=None, atr_pct=0.03, vol_z=0.7),
        make_fs("DDD", mom_6m=0.05, mom_12_1=None, trend_ok=True, atr_pct=None, vol_z=None),
    ]
    base = score_universe(features, cfg())
    for perm in permutations(range(len(features))):
        shuffled = [features[i] for i in perm]
        assert score_universe(shuffled, cfg()) == base


def test_score_ranks_with_ties() -> None:
    """Remisy dostają uśrednioną rangę; brak wartości = ranga 0 (waga tylko mom_6m)."""
    c = cfg(w_mom_12_1=0.0, w_trend=0.0, w_low_vol=0.0, w_vol_z=0.0)
    features = [
        make_fs("AAA", mom_6m=0.10),
        make_fs("BBB", mom_6m=0.10),  # remis z AAA (najniższe wartości)
        make_fs("CCC", mom_6m=0.20),  # najwyższa → ranga 1.0
        make_fs("DDD"),  # brak wartości → ranga 0
    ]
    scores = score_universe(features, c)
    assert scores["CCC"] == pytest.approx(1.0)
    assert scores["AAA"] == pytest.approx((1 + 2) / 2 / 3)  # uśredniona pozycja 1–2 z k=3
    assert scores["BBB"] == pytest.approx(scores["AAA"])
    assert scores["DDD"] == pytest.approx(0.0)


def test_zero_weight_disables_feature() -> None:
    features = [make_fs("AAA", mom_6m=0.10), make_fs("BBB", mom_6m=0.30)]

    c_w = cfg(w_mom_12_1=0.0, w_trend=0.0, w_low_vol=0.0, w_vol_z=0.0)
    with_weight = score_universe(features, c_w)
    assert with_weight["AAA"] != with_weight["BBB"]

    zero = score_universe(
        features, cfg(w_mom_6m=0.0, w_mom_12_1=0.0, w_trend=0.0, w_low_vol=0.0, w_vol_z=0.0)
    )
    assert zero["AAA"] == 0.0 and zero["BBB"] == 0.0


def test_trend_is_literal_one_or_zero() -> None:
    c = cfg(w_mom_6m=0.0, w_mom_12_1=0.0, w_low_vol=0.0, w_vol_z=0.0)
    features = [
        make_fs("AAA", trend_ok=True),
        make_fs("BBB", trend_ok=False),
        make_fs("CCC"),  # None (brak SMA200) → 0
    ]
    scores = score_universe(features, c)
    assert scores["AAA"] == pytest.approx(1.0)
    assert scores["BBB"] == pytest.approx(0.0)
    assert scores["CCC"] == pytest.approx(0.0)


def test_low_vol_rank_inverted() -> None:
    """Niższa zmienność (atr_pct) = wyższa ranga; brak wartości = 0."""
    c = cfg(w_mom_6m=0.0, w_mom_12_1=0.0, w_trend=0.0, w_vol_z=0.0)
    features = [make_fs("AAA", atr_pct=0.04), make_fs("BBB", atr_pct=0.02), make_fs("CCC")]
    scores = score_universe(features, c)
    assert scores["BBB"] == pytest.approx(1.0)  # najniższa zmienność
    assert scores["AAA"] == pytest.approx(0.5)
    assert scores["CCC"] == pytest.approx(0.0)


def test_vol_z_rank_higher_is_better() -> None:
    c = cfg(w_mom_6m=0.0, w_mom_12_1=0.0, w_trend=0.0, w_low_vol=0.0)
    features = [make_fs("AAA", vol_z=-1.0), make_fs("BBB", vol_z=2.0)]
    scores = score_universe(features, c)
    assert scores["BBB"] == pytest.approx(1.0)
    assert scores["AAA"] == pytest.approx(0.5)


def test_score_empty_input() -> None:
    assert score_universe([], cfg()) == {}


def test_score_duplicate_ticker_raises() -> None:
    features = [make_fs("AAA", mom_6m=0.1), make_fs("AAA", mom_6m=0.2)]
    with pytest.raises(ValueError):
        score_universe(features, cfg())


# ------------------------------------------------------------------- build_shortlist


def test_positions_always_in_shortlist() -> None:
    """Pozycja spoza top N (najniższy wynik) i tak trafia na shortlistę."""
    c = cfg(top_n=2, max_total=40)
    scores = {"AAA": 3.0, "BBB": 2.0, "CCC": 1.0}
    sl = build_shortlist(scores, positions=["CCC"], event_tickers={}, cfg=c)

    tickers = [i.ticker for i in sl.items]
    assert set(tickers) == {"AAA", "BBB", "CCC"}
    ccc = next(i for i in sl.items if i.ticker == "CCC")
    assert ccc.reasons == (Reason.POSITION,)
    assert ccc.score == pytest.approx(1.0)


def test_position_outside_scores_gets_zero_score() -> None:
    """Pozycja bez wpisu w `scores` ma wynik 0, ale jest na shortliście."""
    sl = build_shortlist({"AAA": 3.0}, positions=["ZZZ"], event_tickers={}, cfg=cfg(top_n=1))
    zzz = next(i for i in sl.items if i.ticker == "ZZZ")
    assert zzz.reasons == (Reason.POSITION,)
    assert zzz.score == pytest.approx(0.0)


def test_event_below_threshold_ignored() -> None:
    c = cfg(top_n=1, max_total=40)
    sl = build_shortlist({"AAA": 3.0}, positions=[], event_tickers={"BBB": 0.59}, cfg=c)
    assert [i.ticker for i in sl.items] == ["AAA"]


def test_event_at_threshold_included() -> None:
    c = cfg(top_n=1, max_total=40)
    sl = build_shortlist({"AAA": 3.0}, positions=[], event_tickers={"BBB": 0.6}, cfg=c)
    tickers = [i.ticker for i in sl.items]
    assert "BBB" in tickers
    bbb = next(i for i in sl.items if i.ticker == "BBB")
    assert Reason.TEXT_EVENT in bbb.reasons


def test_multiple_reasons_summed_in_one_item() -> None:
    """Ticker będący pozycją, tekstem i top score ma jeden wpis z sumą powodów."""
    c = cfg(top_n=2, max_total=40)
    scores = {"AAA": 3.0, "BBB": 2.0}
    sl = build_shortlist(scores, positions=["AAA"], event_tickers={"AAA": 0.9}, cfg=c)

    aaa_items = [i for i in sl.items if i.ticker == "AAA"]
    assert len(aaa_items) == 1
    item = aaa_items[0]
    assert set(item.reasons) == {Reason.POSITION, Reason.TEXT_EVENT, Reason.TOP_SCORE}
    # pozycja ma pierwszeństwo w kolejności shortlisty
    assert sl.items[0].ticker == "AAA"


def test_trimming_order_top_score_first_then_text_event() -> None:
    """Obcinanie: najpierw TOP_SCORE od najniższego wyniku, potem teksty (od niskiej istotności)."""
    scores = {f"T{i}": float(6 - i) for i in range(1, 6)}  # T1 > T2 > ... > T5

    # max_total=3: capacity = 3 − 1 (pozycja) = 2 → dwa teksty, zero TOP_SCORE
    c3 = cfg(top_n=5, max_total=3)
    sl3 = build_shortlist(scores, positions=["P1"], event_tickers={"E1": 0.9, "E2": 0.7}, cfg=c3)
    assert [i.ticker for i in sl3.items] == ["P1", "E1", "E2"]

    # max_total=4: pozycja + oba teksty + tylko najwyższy wynik T1 (T2–T5 obcięte)
    c4 = cfg(top_n=5, max_total=4)
    sl4 = build_shortlist(scores, positions=["P1"], event_tickers={"E1": 0.9, "E2": 0.7}, cfg=c4)
    assert [i.ticker for i in sl4.items] == ["P1", "E1", "E2", "T1"]

    # max_total=5: pozycja + oba teksty + T1, T2 (mieści się dokładnie limit)
    c5 = cfg(top_n=5, max_total=5)
    sl5 = build_shortlist(scores, positions=["P1"], event_tickers={"E1": 0.9, "E2": 0.7}, cfg=c5)
    assert [i.ticker for i in sl5.items] == ["P1", "E1", "E2", "T1", "T2"]

    # teksty przekraczają capacity → obcinane od najniższej istotności (E3);
    # TOP_SCORE w ogóle nie wchodzi
    c6 = cfg(top_n=5, max_total=2)
    sl6 = build_shortlist(
        scores, positions=[], event_tickers={"E1": 0.9, "E2": 0.7, "E3": 0.65}, cfg=c6
    )
    assert [i.ticker for i in sl6.items] == ["E1", "E2"]


def test_positions_never_trimmed_even_above_max_total() -> None:
    """Liczba pozycji > max_total → shortlista = same pozycje (limit nie dotyczy ich)."""
    c = cfg(top_n=5, max_total=2)
    scores = {"AAA": 3.0}
    sl = build_shortlist(scores, positions=["P1", "P2"], event_tickers={"E1": 0.9}, cfg=c)
    assert [i.ticker for i in sl.items] == ["P1", "P2"]


def test_top_n_limit() -> None:
    """TOP_SCORE nie przekracza top_n, nawet gdy miejsc w shortliście jest więcej."""
    c = cfg(top_n=3, max_total=40)
    scores = {f"T{i}": float(10 - i) for i in range(1, 8)}
    sl = build_shortlist(scores, positions=[], event_tickers={}, cfg=c)
    assert [i.ticker for i in sl.items] == ["T1", "T2", "T3"]


def test_order_within_groups() -> None:
    """Pozycje (−score, ticker); teksty malejąco po istotności; TOP_SCORE (−score, ticker)."""
    c = cfg(top_n=40, max_total=40)
    scores = {"AAA": 1.0, "BBB": 2.0, "CCC": 3.0}
    sl = build_shortlist(
        scores, positions=["CCC", "AAA"], event_tickers={"DDD": 0.7, "EEE": 0.9}, cfg=c
    )
    assert [i.ticker for i in sl.items] == ["CCC", "AAA", "EEE", "DDD", "BBB"]


def test_shortlist_independent_of_input_order() -> None:
    """Permutacje kolejności scores/pozycji/eventów nie zmieniają shortlisty."""
    c = cfg(top_n=3, max_total=10)
    scores = {"AAA": 1.5, "BBB": 2.5, "CCC": 0.5}
    base = build_shortlist(scores, positions=["bbb"], event_tickers={"DDD": 0.8}, cfg=c)

    keys = list(scores)
    for perm in permutations(range(3)):
        shuffled_scores = {keys[i]: scores[keys[i]] for i in perm}
        result = build_shortlist(
            shuffled_scores, positions=["bbb"], event_tickers={"DDD": 0.8}, cfg=c
        )
        assert result == base

    # odwrócona kolejność pozycji i eventów → ten sam wynik
    assert (
        build_shortlist(
            dict(reversed(list(scores.items()))),
            positions=list(reversed(["bbb"])),
            event_tickers=dict(reversed([("DDD", 0.8)])),
            cfg=c,
        )
        == base
    )


def test_empty_input() -> None:
    sl = build_shortlist({}, [], {}, cfg())
    assert isinstance(sl, Shortlist)
    assert sl.items == ()


def test_ticker_normalization_to_uppercase() -> None:
    c = cfg(top_n=10, max_total=40)
    sl = build_shortlist({"AAA": 3.0}, positions=["abc"], event_tickers={"dde": 0.9}, cfg=c)
    tickers = {i.ticker for i in sl.items}
    assert "ABC" in tickers and "DDE" in tickers
