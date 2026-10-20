"""Testy S03: kontrola jakości świec (`gpwbot.quality`).

Zakres z docs/sessions/S03.md: osobny przypadek dla każdej reguły (sztuczne dane),
luka względem kalendarza uwzględnia święta, skok +30% ustawia `needs_review`,
`is_fresh` na granicy (piątek wieczorem, poniedziałek rano).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from gpwbot.calendar import GpwCalendar
from gpwbot.config import AppConfig, PathsCfg
from gpwbot.quality import QualityIssue, check_bars, is_fresh

WARSAW = ZoneInfo("Europe/Warsaw")


def make_calendar(tmp_path: Path) -> GpwCalendar:
    override = tmp_path / "override.yaml"
    override.write_text("holidays: []\nsessions: []\n", encoding="utf-8")
    cfg = AppConfig(
        paths=PathsCfg(
            db_path=tmp_path / "x.db", data_dir=tmp_path, raw_dir=tmp_path, reports_dir=tmp_path
        )
    )
    return GpwCalendar(cfg, override)


@pytest.fixture
def cal(tmp_path: Path) -> GpwCalendar:
    return make_calendar(tmp_path)


def make_df(
    rows: list[tuple[dt.date, float, float, float, float]],
    adj_close: dict[dt.date, float] | None = None,
) -> pd.DataFrame:
    """DataFrame w kształcie `repo.get_prices_df` (indeks `date`, kolumny OHLC)."""
    dates = [r[0] for r in rows]
    df = pd.DataFrame(
        [(o, h, lo, c) for (_, o, h, lo, c) in rows], columns=["open", "high", "low", "close"]
    )
    if adj_close is not None:
        df["adj_close"] = [adj_close.get(d) for d in dates]
    index = pd.DatetimeIndex([dt.datetime.combine(d, dt.time()) for d in dates])
    index.name = "date"
    df.index = index
    return df


def ok_row(date: dt.date, close: float = 10.0) -> tuple[dt.date, float, float, float, float]:
    """Poprawny bar wokół `close` (bez naruszeń reguł)."""
    return (date, close - 0.2, close + 0.3, close - 0.4, close)


def rules(issues: list[QualityIssue]) -> set[str]:
    return {i.rule for i in issues}


class TestCheckBarsRules:
    def test_poprawne_swiec_brak_problemow(self, cal: GpwCalendar) -> None:
        df = make_df([ok_row(dt.date(2026, 10, 19)), ok_row(dt.date(2026, 10, 20), 10.5)])
        assert check_bars(df, cal) == []

    def test_pusty_dataframe_brak_problemow(self, cal: GpwCalendar) -> None:
        df = make_df([]).astype(float)
        assert check_bars(df, cal) == []

    def test_ceny_pozytywne_zero_i_ujemna(self, cal: GpwCalendar) -> None:
        rows = [
            ok_row(dt.date(2026, 10, 19)),
            (dt.date(2026, 10, 20), 9.8, 10.3, 9.4, 0.0),  # close = 0
            ok_row(dt.date(2026, 10, 21)),
        ]
        issues = check_bars(make_df(rows), cal)
        assert "non_positive_price" in rules(issues)
        assert [i.date for i in issues if i.rule == "non_positive_price"] == [dt.date(2026, 10, 20)]

    def test_high_ponizej_low(self, cal: GpwCalendar) -> None:
        # Przy high < low przedział [low, high] jest pusty — druga reguła też się zgłasza.
        rows = [ok_row(dt.date(2026, 10, 19)), (dt.date(2026, 10, 20), 10.0, 9.0, 9.5, 9.8)]
        issues = check_bars(make_df(rows), cal)
        assert "high_below_low" in rules(issues)

    def test_open_poza_przedziale_low_high(self, cal: GpwCalendar) -> None:
        rows = [ok_row(dt.date(2026, 10, 19)), (dt.date(2026, 10, 20), 11.5, 10.3, 9.4, 10.0)]
        issues = check_bars(make_df(rows), cal)
        assert rules(issues) == {"ohlc_out_of_range"}

    def test_close_poza_przedziale_low_high(self, cal: GpwCalendar) -> None:
        rows = [ok_row(dt.date(2026, 10, 19)), (dt.date(2026, 10, 20), 9.8, 10.3, 9.4, 8.5)]
        issues = check_bars(make_df(rows), cal)
        assert rules(issues) == {"ohlc_out_of_range"}

    def test_zduplikowane_daty(self, cal: GpwCalendar) -> None:
        rows = [ok_row(dt.date(2026, 10, 19)), ok_row(dt.date(2026, 10, 20), 10.5)] * 2
        issues = check_bars(make_df(rows), cal)
        dupes = [i for i in issues if i.rule == "duplicate_date"]
        assert {i.date for i in dupes} == {dt.date(2026, 10, 19), dt.date(2026, 10, 20)}

    def test_luka_pomiędzy_sesjami(self, cal: GpwCalendar) -> None:
        # poniedziałek i czwartek — brak wtorku i środy (dwie sesje).
        df = make_df([ok_row(dt.date(2026, 10, 19)), ok_row(dt.date(2026, 10, 22), 10.5)])
        issues = check_bars(df, cal)
        gaps = [i for i in issues if i.rule == "gap"]
        assert [g.date for g in gaps] == [dt.date(2026, 10, 20), dt.date(2026, 10, 21)]

    def test_weekend_nie_jest_luka(self, cal: GpwCalendar) -> None:
        df = make_df([ok_row(dt.date(2026, 10, 16)), ok_row(dt.date(2026, 10, 19), 10.5)])
        assert rules(check_bars(df, cal)) == set()

    def test_swieta_nie_sa_luka(self, cal: GpwCalendar) -> None:
        # 2025-11-11 (wtorek) — Narodowe Święto Niepodległości (fixture S02).
        df = make_df([ok_row(dt.date(2025, 11, 10)), ok_row(dt.date(2025, 11, 12), 10.5)])
        assert rules(check_bars(df, cal)) == set()

    def test_skok_30_procent_ustawia_needs_review(self, cal: GpwCalendar) -> None:
        df = make_df([ok_row(dt.date(2026, 10, 19), 10.0), ok_row(dt.date(2026, 10, 20), 13.0)])
        issues = check_bars(df, cal)
        jumps = [i for i in issues if i.rule == "large_change"]
        assert len(jumps) == 1
        assert jumps[0].date == dt.date(2026, 10, 20)
        assert jumps[0].flag == "needs_review"

    def test_zmiana_do_25_procent_nie_flaguje(self, cal: GpwCalendar) -> None:
        df = make_df([ok_row(dt.date(2026, 10, 19), 10.0), ok_row(dt.date(2026, 10, 20), 12.4)])
        assert rules(check_bars(df, cal)) == set()

    def test_spadek_30_procent_też_flaguje(self, cal: GpwCalendar) -> None:
        df = make_df([ok_row(dt.date(2026, 10, 19), 10.0), ok_row(dt.date(2026, 10, 20), 7.0)])
        assert rules(check_bars(df, cal)) == {"large_change"}

    def test_adj_close_wygasza_fałszywy_skok_po_dywidendzie(self, cal: GpwCalendar) -> None:
        # close spada o 40% (dywidenda), ale adj_close jest gładki → brak flagi.
        rows = [
            ok_row(dt.date(2026, 10, 19), 10.0),
            (dt.date(2026, 10, 20), 5.8, 6.3, 5.4, 6.0),
        ]
        adj = {dt.date(2026, 10, 19): 10.0, dt.date(2026, 10, 20): 10.1}
        assert rules(check_bars(make_df(rows, adj_close=adj), cal)) == set()

    def test_brak_kolumny_adj_close_nie_zmienia_wyniku(self, cal: GpwCalendar) -> None:
        df = make_df([ok_row(dt.date(2026, 10, 19)), ok_row(dt.date(2026, 10, 20), 10.5)])
        assert "adj_close" not in df.columns
        assert check_bars(df, cal) == []

    def test_brak_wymaganej_kolumny_rzuca_value_error(self, cal: GpwCalendar) -> None:
        df = pd.DataFrame({"open": [1.0]}, index=pd.DatetimeIndex([], name="date"))
        with pytest.raises(ValueError, match="close"):
            check_bars(df, cal)


class TestIsFresh:
    def test_piatek_wieczorem_ostatnia_swieca_z_piątku(self, cal: GpwCalendar) -> None:
        as_of = dt.datetime(2026, 10, 16, 20, 0, tzinfo=WARSAW)
        assert is_fresh(dt.date(2026, 10, 16), as_of, cal) is True

    def test_piatek_wieczorem_ostatnia_swieca_z_czwartku(self, cal: GpwCalendar) -> None:
        as_of = dt.datetime(2026, 10, 16, 20, 0, tzinfo=WARSAW)
        assert is_fresh(dt.date(2026, 10, 15), as_of, cal) is False

    def test_poniedzialek_rano_przed_otwarciem(self, cal: GpwCalendar) -> None:
        # Przed otwarciem poniedziałkowej sesji ostatnią zakończoną sesją jest piątek.
        as_of = dt.datetime(2026, 10, 19, 8, 0, tzinfo=WARSAW)
        assert is_fresh(dt.date(2026, 10, 16), as_of, cal) is True

    def test_poniedzialek_rano_stara_swieca(self, cal: GpwCalendar) -> None:
        as_of = dt.datetime(2026, 10, 19, 8, 0, tzinfo=WARSAW)
        assert is_fresh(dt.date(2026, 10, 14), as_of, cal) is False

    def test_w_trakcie_sesji_nie_wymaga_bieżącej_swiecy(self, cal: GpwCalendar) -> None:
        # W trakcie poniedziałkowej sesji świeca dzisiejsza nie jest jeszcze kompletna.
        as_of = dt.datetime(2026, 10, 19, 12, 0, tzinfo=WARSAW)
        assert is_fresh(dt.date(2026, 10, 16), as_of, cal) is True

    def test_po_zamknieciu_wymaga_bieżącej_swiecy(self, cal: GpwCalendar) -> None:
        as_of = dt.datetime(2026, 10, 19, 17, 30, tzinfo=WARSAW)
        assert is_fresh(dt.date(2026, 10, 16), as_of, cal) is False
        assert is_fresh(dt.date(2026, 10, 19), as_of, cal) is True

    def test_swieta_w_pomiędzy(self, cal: GpwCalendar) -> None:
        # Poniedziałek 2025-11-17 przed otwarciem; ostatnia zakończona sesja = piątek
        # 14.11 (wtorek 11.11 — święto). Świeca z piątku jest świeża.
        as_of = dt.datetime(2025, 11, 17, 8, 30, tzinfo=WARSAW)
        assert is_fresh(dt.date(2025, 11, 14), as_of, cal) is True

    def test_brak_swiec_zwraca_false(self, cal: GpwCalendar) -> None:
        as_of = dt.datetime(2026, 10, 19, 17, 30, tzinfo=WARSAW)
        assert is_fresh(None, as_of, cal) is False

    def test_naive_as_of_rzuca_value_error(self, cal: GpwCalendar) -> None:
        with pytest.raises(ValueError):
            is_fresh(dt.date(2026, 10, 19), dt.datetime(2026, 10, 19, 17, 30), cal)
