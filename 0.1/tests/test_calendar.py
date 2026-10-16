"""Testy S02: kalendarz sesji GPW (`gpwbot.calendar`).

Zakres z docs/sessions/S02.md: weekendy, swieta z fixture (oficjalny kalendarz GPW
2025-2026), `next_session_after`, strefa czasowa (DST), nadpisywania, wlasnosci
hypothesis (`next_session_after`, odwrotnosc `add_sessions`/`count_sessions`).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from gpwbot.calendar import GpwCalendar, SessionTimes
from gpwbot.config import AppConfig, MarketCfg, PathsCfg

# Kalendarz jest niezmienialny — fixture funkcji jest bezpieczna dla @given.
SETTINGS = settings(suppress_health_check=[HealthCheck.function_scoped_fixture])

WARSAW = ZoneInfo("Europe/Warsaw")
UTC = dt.UTC

FIXTURE_HOLIDAYS = Path(__file__).resolve().parent / "fixtures" / "gpw_holidays.yaml"


def make_config(tmp_path: Path, **market_overrides) -> AppConfig:
    market = MarketCfg(**market_overrides) if market_overrides else MarketCfg()
    return AppConfig(
        paths=PathsCfg(
            db_path=tmp_path / "x.db",
            data_dir=tmp_path,
            raw_dir=tmp_path,
            reports_dir=tmp_path,
        ),
        market=market,
    )


def make_calendar(tmp_path: Path, override_text: str | None = None) -> GpwCalendar:
    """Kalendarz z jawnym plikiem nadpisan (hermetic — bez zaleznosci od CWD)."""
    override = tmp_path / "override.yaml"
    if override_text is not None:
        override.write_text(override_text, encoding="utf-8")
    return GpwCalendar(make_config(tmp_path), override)


@pytest.fixture
def cal(tmp_path: Path) -> GpwCalendar:
    return make_calendar(tmp_path, "holidays: []\nsessions: []\n")


# --- dni sesyjne -----------------------------------------------------------


class TestWeekendsAndHolidays:
    @pytest.mark.parametrize(
        "iso",
        [
            "2025-01-04",  # sobota
            "2025-01-05",  # niedziela
            "2026-03-28",  # sobota (przed zmiana czasu)
            "2026-12-27",  # niedziela
        ],
    )
    def test_weekends_are_not_sessions(self, cal: GpwCalendar, iso: str) -> None:
        assert not cal.is_session(dt.date.fromisoformat(iso))

    def test_fixture_holidays_match_calendar(self, cal: GpwCalendar) -> None:
        """Swieta z oficjalnego kalendarza GPW 2025-2026 sa dniami niesesyjnymi."""
        data = yaml.safe_load(FIXTURE_HOLIDAYS.read_text(encoding="utf-8"))
        holidays = [dt.date.fromisoformat(str(d)) for d in data["holidays"]]
        assert len(holidays) == 24
        for day in holidays:
            assert not cal.is_session(day), f"{day} powinno byc swietem"

    def test_all_weekdays_2025_2026_consistent_with_fixture(self, cal: GpwCalendar) -> None:
        """Kazdy dzien roboczy 2025-2026 jest sesja wylacznie gdy nie figuruje w fixture.

        To domyka wymaganie S02: kazda rozbieznosc miedzy biblioteka XWAR a oficjalnym
        kalendarzem musialaby byc w pliku override (tu pusty) — test zawiazlby.
        """
        data = yaml.safe_load(FIXTURE_HOLIDAYS.read_text(encoding="utf-8"))
        official = {dt.date.fromisoformat(str(d)) for d in data["holidays"]}
        day = dt.date(2025, 1, 1)
        end = dt.date(2027, 1, 1)
        while day < end:
            if day.weekday() < 5:
                assert cal.is_session(day) == (day not in official), str(day)
            day += dt.timedelta(days=1)


# --- next_session_after ----------------------------------------------------


class TestNextSessionAfter:
    def test_friday_after_close_to_monday(self, cal: GpwCalendar) -> None:
        ts = dt.datetime(2026, 10, 16, 17, 30, tzinfo=WARSAW)  # piątek po zamknieciu
        assert cal.next_session_after(ts) == dt.date(2026, 10, 19)

    def test_weekend_to_next_monday(self, cal: GpwCalendar) -> None:
        ts = dt.datetime(2026, 3, 28, 12, 0, tzinfo=WARSAW)  # sobota
        assert cal.next_session_after(ts) == dt.date(2026, 3, 30)

    def test_before_open_same_day(self, cal: GpwCalendar) -> None:
        ts = dt.datetime(2026, 3, 31, 8, 45, tzinfo=WARSAW)  # wtorek przed 9:00
        assert cal.next_session_after(ts) == dt.date(2026, 3, 31)

    def test_at_preopen_start_same_day(self, cal: GpwCalendar) -> None:
        ts = dt.datetime(2026, 3, 31, 8, 30, tzinfo=WARSAW)  # start fazy przed otwarciem
        assert cal.next_session_after(ts) == dt.date(2026, 3, 31)

    def test_during_session_to_next_day(self, cal: GpwCalendar) -> None:
        ts = dt.datetime(2026, 3, 31, 12, 0, tzinfo=WARSAW)
        assert cal.next_session_after(ts) == dt.date(2026, 4, 1)

    def test_after_close_to_next_day(self, cal: GpwCalendar) -> None:
        ts = dt.datetime(2026, 3, 31, 17, 30, tzinfo=WARSAW)
        assert cal.next_session_after(ts) == dt.date(2026, 4, 1)

    def test_day_before_holiday_skips_it(self, cal: GpwCalendar) -> None:
        # poniedzialek przed 11.11 (Narodowe Swieto Niepodleglosci)
        ts = dt.datetime(2025, 11, 10, 17, 30, tzinfo=WARSAW)
        assert cal.next_session_after(ts) == dt.date(2025, 11, 12)

    def test_before_easter_block(self, cal: GpwCalendar) -> None:
        # czwartek przed Wielkim Piątkiem (3.4) i Poniedzialkiem Wielkanocnym (6.4)
        ts = dt.datetime(2026, 4, 2, 17, 30, tzinfo=WARSAW)
        assert cal.next_session_after(ts) == dt.date(2026, 4, 7)

    def test_naive_ts_raises(self, cal: GpwCalendar) -> None:
        with pytest.raises(ValueError):
            cal.next_session_after(dt.datetime(2026, 3, 31, 12, 0))


# --- strefa czasowa (DST) --------------------------------------------------


class TestTimezone:
    def test_open_before_dst_change_is_08_utc(self, cal: GpwCalendar) -> None:
        # 27.03.2026 — zima (UTC+1): otwarcie 9:00 lokalnie = 08:00 UTC
        open_ts = cal.session_open_ts(dt.date(2026, 3, 27))
        assert open_ts.astimezone(UTC) == dt.datetime(2026, 3, 27, 8, 0, tzinfo=UTC)

    def test_open_after_dst_change_is_07_utc(self, cal: GpwCalendar) -> None:
        # 30.03.2026 — lato (UTC+2): otwarcie 9:00 lokalnie = 07:00 UTC
        open_ts = cal.session_open_ts(dt.date(2026, 3, 30))
        assert open_ts.astimezone(UTC) == dt.datetime(2026, 3, 30, 7, 0, tzinfo=UTC)

    def test_phases_are_aware_warsaw(self, cal: GpwCalendar) -> None:
        phases = cal.session_phases(dt.date(2026, 10, 26))  # poniedziałek po zmianie czasu (25.10)
        for value in (
            phases.preopen_start,
            phases.continuous_start,
            phases.close_auction_end,
        ):
            assert value.tzinfo is not None
            assert str(value.utcoffset()) == "1:00:00"  # zima (UTC+1)


# --- fazy sesji -------------------------------------------------------------


class TestPhases:
    def test_phases_from_config(self, cal: GpwCalendar) -> None:
        phases = cal.session_phases(dt.date(2026, 10, 19))
        assert isinstance(phases, SessionTimes)
        assert phases.date == dt.date(2026, 10, 19)
        assert phases.preopen_start.hour == 8 and phases.preopen_start.minute == 30
        assert phases.preopen_end.hour == 9
        assert phases.continuous_start.hour == 9
        assert phases.continuous_end.hour == 16 and phases.continuous_end.minute == 50
        assert phases.close_auction_start.hour == 16 and phases.close_auction_start.minute == 50
        assert phases.close_auction_end.hour == 17

    def test_phases_come_from_cfg_not_library(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path, preopen_end=dt.time(10, 0), continuous_start=dt.time(10, 0))
        cal = GpwCalendar(cfg, tmp_path / "override.yaml")  # brak pliku = puste nadpisywania
        phases = cal.session_phases(dt.date(2026, 10, 19))
        assert phases.continuous_start.hour == 10
        assert cal.session_open_ts(dt.date(2026, 10, 19)).hour == 10

    def test_non_session_raises(self, cal: GpwCalendar) -> None:
        saturday = dt.date(2026, 3, 28)
        with pytest.raises(ValueError):
            cal.session_phases(saturday)
        with pytest.raises(ValueError):
            cal.session_open_ts(dt.date(2025, 11, 11))


# --- nadpisywania -----------------------------------------------------------


class TestOverrides:
    def test_extra_holiday_and_added_session(self, tmp_path: Path) -> None:
        override = (
            "holidays:\n"
            '  - "2026-03-31"\n'  # wtorek — zawieszenie notowan
            "sessions:\n"
            '  - "2026-04-04"\n'  # sobota — sesja nadrobiona
        )
        cal = make_calendar(tmp_path, override)
        assert not cal.is_session(dt.date(2026, 3, 31))
        assert cal.is_session(dt.date(2026, 4, 4))

    def test_next_session_skips_extra_holiday(self, tmp_path: Path) -> None:
        override = 'holidays:\n  - "2026-03-31"\nsessions: []\n'
        cal = make_calendar(tmp_path, override)
        ts = dt.datetime(2026, 3, 30, 17, 30, tzinfo=WARSAW)
        assert cal.next_session_after(ts) == dt.date(2026, 4, 1)

    def test_previous_session_finds_added_weekend(self, tmp_path: Path) -> None:
        override = 'holidays: []\nsessions:\n  - "2026-04-04"\n'
        cal = make_calendar(tmp_path, override)
        assert cal.previous_session(dt.date(2026, 4, 5)) == dt.date(2026, 4, 4)

    def test_missing_override_file_is_empty(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path)
        cal = GpwCalendar(cfg, tmp_path / "nieistniejacy.yaml")
        assert cal.is_session(dt.date(2026, 3, 31))

    def test_overlap_in_override_raises(self, tmp_path: Path) -> None:
        from gpwbot.errors import ConfigError

        override = 'holidays:\n  - "2026-03-31"\nsessions:\n  - "2026-03-31"\n'
        with pytest.raises(ConfigError):
            make_calendar(tmp_path, override)


# --- add_sessions / count_sessions ------------------------------------------


class TestSessionArithmetic:
    def test_add_sessions_forward_and_back(self, cal: GpwCalendar) -> None:
        monday = dt.date(2026, 10, 19)
        assert cal.add_sessions(monday, 0) == monday
        assert cal.add_sessions(monday, 1) == dt.date(2026, 10, 20)
        assert cal.add_sessions(monday, -1) == dt.date(2026, 10, 16)

    def test_add_sessions_across_holiday(self, cal: GpwCalendar) -> None:
        # poniedzialek przed 11.11 + 1 sesja = sreda (swieto pomijane)
        assert cal.add_sessions(dt.date(2025, 11, 10), 1) == dt.date(2025, 11, 12)

    def test_add_sessions_non_session_raises(self, cal: GpwCalendar) -> None:
        with pytest.raises(ValueError):
            cal.add_sessions(dt.date(2026, 3, 28), 1)

    def test_count_sessions_half_open_interval(self, cal: GpwCalendar) -> None:
        monday = dt.date(2026, 10, 19)
        assert cal.count_sessions(monday, monday) == 0
        # przedzial polootwarty (a, b]: poniedzialek wykluczony, piątek wliczony
        assert cal.count_sessions(monday, dt.date(2026, 10, 23)) == 4

    def test_count_sessions_invalid_range_raises(self, cal: GpwCalendar) -> None:
        with pytest.raises(ValueError):
            cal.count_sessions(dt.date(2026, 10, 23), dt.date(2026, 10, 19))


# --- wlasnosci (hypothesis) --------------------------------------------------

SESSION_DATES = st.dates(min_value=dt.date(2025, 1, 6), max_value=dt.date(2026, 12, 31))
RANDOM_TS = st.datetimes(
    min_value=dt.datetime(2025, 1, 1, tzinfo=UTC),
    max_value=dt.datetime(2026, 12, 31, 23, 59, tzinfo=UTC),
)


class TestProperties:
    @given(ts=RANDOM_TS)
    @SETTINGS
    def test_next_session_after_property(self, cal: GpwCalendar, ts: dt.datetime) -> None:
        result = cal.next_session_after(ts)
        assert cal.is_session(result)
        # wynik jest pozniejszy niz ts (otwarcie zwracanej sesji > ts)
        assert cal.session_open_ts(result) > ts
        day = ts.astimezone(WARSAW).date()
        if cal.is_session(day) and ts < cal.session_open_ts(day):
            assert result == day  # biezaca sesja, jesli ts przed jej otwarciem
        else:
            assert result > day
            cursor = day + dt.timedelta(days=1)
            while cursor < result:  # miedzy nimi nie ma innej sesji
                assert not cal.is_session(cursor), str(cursor)
                cursor += dt.timedelta(days=1)

    @given(d=SESSION_DATES, n=st.integers(min_value=-10, max_value=10))
    @SETTINGS
    def test_add_count_are_inverses(self, cal: GpwCalendar, d: dt.date, n: int) -> None:
        if not cal.is_session(d):
            return
        moved = cal.add_sessions(d, n)
        assert cal.is_session(moved)
        if n >= 0:
            assert cal.count_sessions(d, moved) == n
        else:
            assert cal.count_sessions(moved, d) == -n

    @given(a=SESSION_DATES, b=SESSION_DATES)
    @SETTINGS
    def test_count_add_roundtrip(self, cal: GpwCalendar, a: dt.date, b: dt.date) -> None:
        if not (cal.is_session(a) and cal.is_session(b)):
            return
        first, second = sorted((a, b))
        assert cal.add_sessions(first, cal.count_sessions(first, second)) == second

    @given(ts=RANDOM_TS)
    @SETTINGS
    def test_previous_session_is_before(self, cal: GpwCalendar, ts: dt.datetime) -> None:
        day = ts.astimezone(WARSAW).date()
        previous = cal.previous_session(day)
        assert previous < day
        assert cal.is_session(previous)
