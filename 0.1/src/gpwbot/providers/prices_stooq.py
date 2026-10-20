"""Adapter cen dziennych Stooq (S03): CSV, retry 5xx/timeouty, limiter zapytań.

Wzorzec adresu do weryfikacji na żywej odpowiedzi (V-03):
`https://stooq.pl/q/d/l/?s=<symbol>&i=d&d1=YYYYMMDD&d2=YYYYMMDD`. Nagłówki PL/EN
oraz przecinek jako separator dziesiętny są obsługiwane, ale rzeczywisty kształt
odpowiedzi Stooq wymaga potwierdzenia (decyzje D-024/D-025).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import time
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx
import yaml
from tenacity import (
    RetryError,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from gpwbot.config import ProvidersCfg
from gpwbot.errors import ConfigError, ProviderError
from gpwbot.providers.base import PriceBar

STOOQ_DAILY_URL = "https://stooq.pl/q/d/l/"
DEFAULT_SYMBOL_MAP_PATH = Path("config") / "symbol_map.yaml"

# Nagłówki Stooq: angielskie i polskie (V-03 — do weryfikacji na żywej odpowiedzi).
_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("date", "data"),
    "open": ("open", "otwarcie"),
    "high": ("high", "max", "maksimum"),
    "low": ("low", "min", "minimum"),
    "close": ("close", "zamknięcie", "zamkniecie"),
    "volume": ("volume", "vol", "wolumen"),
    "turnover": ("turnover", "obrot", "obrót"),
    "adj_close": ("adj_close", "adj close", "adjusted close", "close_adj"),
}


class _StooqServerError(Exception):
    """Błąd 5xx — jedyny błąd HTTP ponawiany (poza timeoutami)."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, (_StooqServerError, httpx.TimeoutException))


# --- parser CSV -----------------------------------------------------------------


def parse_stooq_csv(text: str) -> list[PriceBar]:
    """Parsuje surową odpowiedź Stooq (CSV); rzuca `ProviderError` przy braku danych.

    Obsługuje nagłówki polskie i angielskie, zmienną liczbę kolumn oraz przecinek
    jako separator dziesiętny — w postaci zacytowanej (`"98,50"`) lub nie (wtedy
    pary komórek cyfrowych są łączone; heurystyka D-024).
    """
    lines = [ln for ln in text.replace("\r\n", "\n").splitlines() if ln.strip()]
    if not lines:
        raise ProviderError("stooq: pusta odpowiedź (brak CSV)")

    header_line = lines[0].lstrip("\ufeff")
    headers = [h.strip().lower() for h in next(csv.reader(io.StringIO(header_line)))]
    col_index: dict[str, int] = {}
    for i, name in enumerate(headers):
        for canonical, aliases in _HEADER_ALIASES.items():
            if name in aliases and canonical not in col_index:
                col_index[canonical] = i
                break

    missing = [c for c in ("date", "open", "high", "low", "close") if c not in col_index]
    if missing:
        raise ProviderError(f"stooq: brak danych w odpowiedzi (nagłówek: {header_line!r})")

    bars: list[PriceBar] = []
    for line_no, row in enumerate(csv.reader(io.StringIO("\n".join(lines[1:]))), start=2):
        if not row or all(not cell.strip() for cell in row):
            continue
        bars.append(_parse_row(row, col_index, len(headers), line_no))
    return bars


def _reconcile_row(row: list[str], n_cols: int, line_no: int) -> list[str]:
    """Dopasowuje liczbę kolumn wiersza do nagłówka.

    Gdy separator dziesiętny to przecinek (niezacytowany), wartość zajmuje kilka
    komórek — łączymy pary czysto cyfrowe od lewej, dopóki długość się nie zgadzie.
    """
    if len(row) == n_cols:
        return row
    if len(row) < n_cols:
        raise ProviderError(f"stooq: wiersz {line_no}: za mało kolumn ({len(row)} < {n_cols})")

    needed = len(row) - n_cols
    out: list[str] = []
    i = 0
    while i < len(row):
        if (
            needed > 0
            and i + 1 < len(row)
            and row[i].strip().isdigit()
            and row[i + 1].strip().isdigit()
        ):
            out.append(f"{row[i].strip()}.{row[i + 1].strip()}")
            i += 2
            needed -= 1
        else:
            out.append(row[i])
            i += 1
    if len(out) != n_cols:
        raise ProviderError(
            f"stooq: wiersz {line_no}: nieoczekiwana liczba kolumn ({len(row)} ≠ {n_cols})"
        )
    return out


def _to_decimal(raw: str, what: str, line_no: int) -> Decimal:
    value = raw.strip().replace(",", ".")  # przecinek jako separator dziesiętny (S03)
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ProviderError(f"stooq: wiersz {line_no}: błędna wartość '{what}'={raw!r}") from exc
    if not result.is_finite():
        raise ProviderError(f"stooq: wiersz {line_no}: niekończąca się wartość '{what}'={raw!r}")
    return result


def _parse_row(row: list[str], col_index: dict[str, int], n_cols: int, line_no: int) -> PriceBar:
    row = _reconcile_row(row, n_cols, line_no)

    try:
        d = dt.date.fromisoformat(row[col_index["date"]].strip())
    except ValueError as exc:
        raise ProviderError(f"stooq: wiersz {line_no}: błędna data {row[0]!r}") from exc

    def num(canonical: str) -> Decimal | None:
        if canonical not in col_index:
            return None
        raw = row[col_index[canonical]]
        if not raw.strip():
            return None
        return _to_decimal(raw, canonical, line_no)

    o, h, lo, c = num("open"), num("high"), num("low"), num("close")
    if o is None or h is None or lo is None or c is None:  # nagłówek gwarantuje kolumny
        raise ProviderError(f"stooq: wiersz {line_no}: brak wartości OHLC")

    volume = num("volume")
    return PriceBar(
        date=d,
        open=o,
        high=h,
        low=lo,
        close=c,
        adj_close=num("adj_close"),
        volume=None if volume is None else int(volume),
        turnover_pln=num("turnover"),
    )


# --- mapowanie symboli -----------------------------------------------------------


def load_symbol_map(path: str | Path) -> dict[str, str]:
    """Nadpisywania ticker→symbol Stooq; brak pliku = pusta mapa (analogicznie D-017).

    Klucze normalizowane do wielkich liter; błędy pliku/schematu jako `ConfigError`.
    """
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"błędny YAML w {p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{p}: plik musi być mapą ticker → symbol")
    result: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{p}: wartość dla {key!r} musi być niepustym ciągiem")
        result[str(key).strip().upper()] = value.strip()
    return result


def map_symbol(ticker: str, overrides: Mapping[str, str]) -> str:
    """Symbol Stooq dla tickera: nadpisanie z mapy (klucz uppercase) albo ticker małymi."""
    return overrides.get(ticker.upper(), ticker.lower())


# --- limiter i provider -----------------------------------------------------------


class RateLimiter:
    """Prosty limiter zapytań: nie częściej niż `requests_per_second` (S03)."""

    def __init__(self, requests_per_second: float) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second musi być > 0")
        self._interval = 1.0 / requests_per_second
        self._last: float | None = None

    def wait(self) -> None:
        now = time.monotonic()
        if self._last is not None:
            delay = self._interval - (now - self._last)
            if delay > 0:
                time.sleep(delay)
        self._last = time.monotonic()


class StooqPriceProvider:
    """Dostawca cen dziennych ze Stooq; retry wg konfiguracji, limiter zapytań."""

    def __init__(
        self,
        cfg: ProvidersCfg,
        symbol_map_path: str | Path = DEFAULT_SYMBOL_MAP_PATH,
        client: httpx.Client | None = None,
    ) -> None:
        stooq = cfg.stooq
        self._cfg = stooq
        self._overrides = load_symbol_map(symbol_map_path)
        self._client = client if client is not None else httpx.Client()
        self._limiter = RateLimiter(stooq.requests_per_second)

    def close(self) -> None:
        """Zamyka klient HTTP (provider tworzy go sam tylko wtedy, gdy nie wstrzyknięto)."""
        self._client.close()

    def symbol_for(self, ticker: str) -> str:
        """Symbol Stooq dla tickera GPW (mapowanie + nadpisywania z YAML)."""
        return map_symbol(ticker, self._overrides)

    def fetch_raw(self, symbol: str, start: dt.date, end: dt.date) -> str:
        """Surowa odpowiedź CSV (tekst); 5xx i timeouty ponawiane, 4xx — nie.

        Po wyczerpaniu prób rzuca `ProviderError` (S03).
        """
        if start > end:
            raise ValueError("wymagano start <= end")
        url = (
            f"{STOOQ_DAILY_URL}?s={symbol}&i=d"
            f"&d1={start.strftime('%Y%m%d')}&d2={end.strftime('%Y%m%d')}"
        )
        attempts = self._cfg.max_retries
        try:
            return Retrying(
                stop=stop_after_attempt(attempts),
                wait=wait_exponential(multiplier=self._cfg.retry_backoff_base_s, max=60.0),
                retry=retry_if_exception(_is_retryable),
            )(self._get_once, url)
        except RetryError as exc:
            last = exc.last_attempt.exception() if exc.last_attempt is not None else None
            raise ProviderError(
                f"stooq: {attempts} nieudanych prób dla '{symbol}': {last}"
            ) from exc
        except httpx.HTTPError as exc:  # np. ConnectError — bez ponawiania (S03)
            raise ProviderError(f"stooq: błąd HTTP dla '{symbol}': {exc}") from exc

    def _get_once(self, url: str) -> str:
        self._limiter.wait()
        try:
            response = self._client.get(url, timeout=self._cfg.timeout_seconds)
        except httpx.TimeoutException:
            raise  # ponawiane przez Retrying (predykat `_is_retryable`)
        if response.status_code >= 500:
            raise _StooqServerError(response.status_code)
        if response.status_code >= 400:
            raise ProviderError(f"stooq: HTTP {response.status_code} dla '{url}' (bez ponawiania)")
        return response.text

    def get_daily(self, symbol: str, start: dt.date, end: dt.date) -> list[PriceBar]:
        """Ceny dzienne dla symbolu źródła (protokół `PriceProvider`)."""
        return parse_stooq_csv(self.fetch_raw(symbol, start, end))
