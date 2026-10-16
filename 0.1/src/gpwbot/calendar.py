"""Kalendarz sesji GPW: exchange_calendars (XWAR) + nadpisywania z YAML; fazy z cfg.market.

A3: wszystkie daty świadome strefy (Europe/Warsaw); godziny faz pochodzą wyłącznie
z konfiguracji, nie z biblioteki. "Otwarcie sesji" = początek notowań ciągłych —
faza przed otwarciem to tylko składanie zleceń, bez transakcji (SZOG §5–§6; D-013).

Zrodla: SZOG GPW §5(1)(1) i gpw.pl "Szczegoly sesji" (dostep 2026-10-16);
patrz docs/decisions.md (D-014, V-01). Nadpisywania: `config/gpw_holidays_override.yaml`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from exchange_calendars import get_calendar

from gpwbot.config import AppConfig
from gpwbot.errors import ConfigError

WARSAW = ZoneInfo("Europe/Warsaw")
DEFAULT_OVERRIDE_PATH = Path("config") / "gpw_holidays_override.yaml"
_MAX_GAP_DAYS = 40


@dataclass(frozen=True)
class SessionTimes:
    """Godziny faz sesji dla jednego dnia (aware, Europe/Warsaw)."""

    date: dt.date
    preopen_start: dt.datetime
    preopen_end: dt.datetime
    continuous_start: dt.datetime
    continuous_end: dt.datetime
    close_auction_start: dt.datetime
    close_auction_end: dt.datetime


def _parse_date(value: object, path: Path) -> dt.date:
    if isinstance(value, dt.datetime):  # przed dt.date (datetime jest podklasą date)
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value.strip())
        except ValueError as exc:
            raise ConfigError(f"{path}: błędna data {value!r}") from exc
    raise ConfigError(f"{path}: oczekiwano daty YYYY-MM-DD, otrzymano {type(value).__name__}")


def _load_overrides(path: Path) -> tuple[frozenset[dt.date], frozenset[dt.date]]:
    """Ładuje plik nadpisań; brak pliku = puste nadpisywania (D-017)."""
    if not path.is_file():
        return frozenset(), frozenset()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"błędny YAML w {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: plik musi być mapą z kluczami holidays/sessions")

    def dates(key: str) -> frozenset[dt.date]:
        values = raw.get(key, []) or []
        if not isinstance(values, list):
            raise ConfigError(f"{path}: '{key}' musi być listą dat")
        return frozenset(_parse_date(v, path) for v in values)

    extra_holidays = dates("holidays")
    added_sessions = dates("sessions")
    overlap = sorted(d.isoformat() for d in (extra_holidays & added_sessions))
    if overlap:
        raise ConfigError(f"{path}: daty w holidays i sessions nie mogą się pokrywać: {overlap}")
    return extra_holidays, added_sessions


class GpwCalendar:
    """Kalendarz sesji GPW: XWAR + nadpisywania; godziny faz z `cfg.market`."""

    def __init__(self, cfg: AppConfig, override_path: str | Path | None = None) -> None:
        self._cfg = cfg
        self._cal = get_calendar("XWAR")
        path = Path(override_path) if override_path is not None else DEFAULT_OVERRIDE_PATH
        self._extra_holidays, self._added_sessions = _load_overrides(path)

    # --- dni sesyjne ---------------------------------------------------------

    def is_session(self, d: dt.date) -> bool:
        """Czy dzień `d` jest dniem sesji GPW (po nałożeniu nadpisań)."""
        if d in self._extra_holidays:
            return False
        if d in self._added_sessions:
            return True
        return bool(self._cal.is_session(d))

    def next_session_after(self, ts: dt.datetime) -> dt.date:
        """Pierwsza sesja, której otwarcie jest późniejsze niż `ts`.

        Jeśli dzień `ts` (Europe/Warsaw) jest sesją i `ts` leży przed jej otwarciem,
        zwraca ten sam dzień. `ts` musi być świadomy strefy (A3).
        """
        if ts.tzinfo is None or ts.tzinfo.utcoffset(ts) is None:
            raise ValueError("ts musi być datą świadomą strefy czasowej")
        local = ts.astimezone(WARSAW)
        d = local.date()
        if self.is_session(d) and local < self.session_open_ts(d):
            return d
        return self._step(d, +1)

    def previous_session(self, d: dt.date) -> dt.date:
        """Ostatnia sesja ściśle przed dniem `d` (`d` nie musi być sesją)."""
        return self._step(d, -1)

    def sessions_between(self, a: dt.date, b: dt.date) -> list[dt.date]:
        """Sesje w przedziale półotwartym `(a, b]`; wymaga `a <= b` (D-018)."""
        if a > b:
            raise ValueError("wymagano a <= b")
        out: list[dt.date] = []
        d = a + dt.timedelta(days=1)
        while d <= b:
            if self.is_session(d):
                out.append(d)
            d += dt.timedelta(days=1)
        return out

    def add_sessions(self, d: dt.date, n: int) -> dt.date:
        """Przesuwa dzień sesji `d` o `n` sesji (ujemne — wstecz)."""
        if not self.is_session(d):
            raise ValueError(f"{d.isoformat()} nie jest dniem sesji GPW")
        direction = 1 if n >= 0 else -1
        current = d
        for _ in range(abs(n)):
            current = self._step(current, direction)
        return current

    def count_sessions(self, a: dt.date, b: dt.date) -> int:
        """Liczba sesji w `(a, b]` — wzajemnie odwrotna do `add_sessions` (D-018)."""
        return len(self.sessions_between(a, b))

    # --- fazy sesji -----------------------------------------------------------

    def session_phases(self, d: dt.date) -> SessionTimes:
        """Godziny faz dla dnia sesyjnego; godziny wyłącznie z `cfg.market` (A3)."""
        if not self.is_session(d):
            raise ValueError(f"{d.isoformat()} nie jest dniem sesji GPW")
        market = self._cfg.market

        def at(t: dt.time) -> dt.datetime:
            return dt.datetime.combine(d, t, tzinfo=WARSAW)

        return SessionTimes(
            date=d,
            preopen_start=at(market.preopen_start),
            preopen_end=at(market.preopen_end),
            continuous_start=at(market.continuous_start),
            continuous_end=at(market.continuous_end),
            close_auction_start=at(market.close_auction_start),
            close_auction_end=at(market.close_auction_end),
        )

    def session_open_ts(self, d: dt.date) -> dt.datetime:
        """Otwarcie sesji = początek notowań ciągłych (aware, Europe/Warsaw)."""
        return self.session_phases(d).continuous_start

    def session_close_ts(self, d: dt.date) -> dt.datetime:
        """Zamknięcie sesji = koniec aukcji zamknięcia (aware, Europe/Warsaw)."""
        return self.session_phases(d).close_auction_end

    # --- wewnętrzne ------------------------------------------------------------

    def _step(self, from_d: dt.date, direction: int) -> dt.date:
        """Następna/previous sesja ściśle po/przed `from_d`; okno `_MAX_GAP_DAYS` dni."""
        for offset in range(1, _MAX_GAP_DAYS + 1):
            candidate = from_d + dt.timedelta(days=direction * offset)
            if self.is_session(candidate):
                return candidate
        raise ValueError(f"brak sesji w oknie {_MAX_GAP_DAYS} dni od {from_d.isoformat()}")
