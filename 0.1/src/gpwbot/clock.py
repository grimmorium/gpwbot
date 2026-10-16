"""Zegary: interfejs, zegar systemowy i fałszywy (testy, replay).

A3: wszystkie daty są świadome strefy; zakaz `datetime.now()` poza SystemClock.
"""

from __future__ import annotations

import datetime as dt
from typing import Protocol


class Clock(Protocol):
    """Abstrakcyjny zegar — jedyny sposób odczytu czasu w gpwbot."""

    def now(self) -> dt.datetime: ...


def _require_aware(ts: dt.datetime, name: str) -> None:
    if ts.tzinfo is None or ts.tzinfo.utcoffset(ts) is None:
        raise ValueError(f"{name} musi być datą świadomą strefy czasowej")


class SystemClock:
    """Zegar systemowy; zwraca aware UTC."""

    def now(self) -> dt.datetime:
        return dt.datetime.now(dt.UTC)


class FakeClock:
    """Deterministyczny zegar do testów i odtwarzania runów."""

    def __init__(self, start: dt.datetime) -> None:
        _require_aware(start, "start")
        self._now = start

    def now(self) -> dt.datetime:
        return self._now

    def advance(self, delta: dt.timedelta) -> None:
        """Przesuwa czas o `delta` (może być ujemne)."""
        self._now += delta

    def set(self, ts: dt.datetime) -> None:
        _require_aware(ts, "ts")
        self._now = ts
