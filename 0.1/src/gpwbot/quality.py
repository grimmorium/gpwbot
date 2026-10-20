"""Kontrola jakości świec dziennych (S03): reguły na DataFrame + świeżość vs kalendarz.

Reguły `check_bars` (Część I sekcja 7, S03): ceny ≤ 0, `high < low`, `open`/`close`
poza `[low, high]`, zduplikowane daty, luki względem kalendarza sesji GPW i dzienna
zmiana > 25% (flaga `needs_review`). Próg skoku jest stałą algorytmu wg specyfikacji
(decyzja D-023). DataFrame ma kształt `repo.get_prices_df`: indeks `date`
(`DatetimeIndex`) + kolumny `open high low close` (+ opcjonalnie `adj_close`).
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd

from gpwbot.calendar import GpwCalendar

WARSAW = ZoneInfo("Europe/Warsaw")

# Dzienna zmiana powyżej tego progu (w %) oznacza anomalię → flaga `needs_review`.
LARGE_CHANGE_PCT: Decimal = Decimal("25")

_REQUIRED_COLUMNS = ("open", "high", "low", "close")


@dataclass(frozen=True)
class QualityIssue:
    """Jeden problem jakościowy wykryty w serii świec.

    `rule`: non_positive_price | high_below_low | ohlc_out_of_range | duplicate_date
    | gap | large_change; `flag` niesie oznaczenie do zapisu (np. `needs_review`).
    """

    rule: str
    date: dt.date
    message: str
    flag: str | None = None


def _require_columns(df: pd.DataFrame) -> None:
    missing = [col for col in _REQUIRED_COLUMNS if col not in df.columns]
    if missing or df.index.name != "date":
        raise ValueError(
            f"df musi mieć indeks 'date' i kolumny {_REQUIRED_COLUMNS}; brak: {missing}"
        )


def check_bars(df: pd.DataFrame, calendar: GpwCalendar) -> list[QualityIssue]:
    """Sprawdza serię świec pod kątem reguł jakości; zwraca listę problemów."""
    _require_columns(df)
    issues: list[QualityIssue] = []
    if df.empty:
        return issues

    # Zduplikowane daty — osobna reguła (po jednej na każdą zduplikowaną datę).
    dup_mask = df.index.duplicated(keep=False)
    for d in sorted({ts.date() for ts in df.index[dup_mask]}):
        issues.append(QualityIssue("duplicate_date", d, f"zduplikowana data {d.isoformat()}"))

    # Reguły wierszowe na wszystkich wierszach (także duplikatach).
    o = df["open"].to_numpy(dtype="float64")
    h = df["high"].to_numpy(dtype="float64")
    lo = df["low"].to_numpy(dtype="float64")
    c = df["close"].to_numpy(dtype="float64")
    for i, ts in enumerate(df.index):
        d = ts.date()
        bad = [
            name
            for name, v in zip(_REQUIRED_COLUMNS, (o[i], h[i], lo[i], c[i]), strict=True)
            if v <= 0
        ]
        if bad:
            issues.append(QualityIssue("non_positive_price", d, f"ceny ≤ 0: {', '.join(bad)}"))
        if h[i] < lo[i]:
            issues.append(QualityIssue("high_below_low", d, "high poniżej low"))
        if o[i] < lo[i] or o[i] > h[i] or c[i] < lo[i] or c[i] > h[i]:
            issues.append(
                QualityIssue("ohlc_out_of_range", d, "open/close poza przedziałem [low, high]")
            )

    # Luki i skoki — na unikalnych, posortowanych datach.
    unique = df[~df.index.duplicated(keep="first")].sort_index()
    dates = [ts.date() for ts in unique.index]
    uc = unique["close"].to_numpy(dtype="float64")
    has_adj = "adj_close" in unique.columns
    ua = unique["adj_close"].to_numpy(dtype="float64") if has_adj else None

    threshold = float(LARGE_CHANGE_PCT)
    for i in range(1, len(dates)):
        d_prev, d_cur = dates[i - 1], dates[i]
        missing = [x for x in calendar.sessions_between(d_prev, d_cur) if x < d_cur]
        for m in missing:
            issues.append(QualityIssue("gap", m, f"brak świecy dla sesji {m.isoformat()}"))

        base, cur = uc[i - 1], uc[i]
        if ua is not None and not (math.isnan(ua[i - 1]) or math.isnan(ua[i])):
            # Korekta dywidend/splitów — skok liczymy na cenach skorygowanych.
            base, cur = ua[i - 1], ua[i]
        if base > 0 and abs(cur / base - 1.0) * 100.0 > threshold:
            pct = (cur / base - 1.0) * 100.0
            issues.append(
                QualityIssue(
                    "large_change",
                    d_cur,
                    f"dzienna zmiana {pct:+.1f}% przekracza ±{LARGE_CHANGE_PCT}%",
                    flag="needs_review",
                )
            )
    return issues


def is_fresh(last_date: dt.date | None, as_of: dt.datetime, calendar: GpwCalendar) -> bool:
    """Czy ostatnia świeca nie jest starsza niż jedna sesja względem `as_of`.

    Świeżość mierzymy wobec ostatniej w pełni zakończonej sesji (zamknięcie ≤
    `as_of`): w trakcie bieżącej sesji jej świeca dzienna nie jest jeszcze
    kompletna, więc nie jest wymagana. `as_of` musi być świadomy strefy (A3).
    """
    if last_date is None:
        return False
    if as_of.tzinfo is None or as_of.tzinfo.utcoffset(as_of) is None:
        raise ValueError("as_of musi być datą świadomą strefy czasowej")

    d = as_of.astimezone(WARSAW).date()
    for _ in range(40):  # okno bezpieczeństwa — jak w kalendarzu (D-018)
        if calendar.is_session(d) and calendar.session_close_ts(d) <= as_of:
            return last_date >= d
        d = calendar.previous_session(d)
    raise ValueError("brak zakończonej sesji GPW w oknie 40 dni przed as_of")
