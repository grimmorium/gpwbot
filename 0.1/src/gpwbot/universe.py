"""Universe i instrumenty (S04): seed → baza + filtry płynności do shortlisty.

`sync_instruments` ładuje `config/universe_seed.yaml` do tabeli `instruments`
(upsert; usunięcie z seedu = `active=false`, nie kasowanie). `get_universe`
zwraca członków i powody wykluczeń (Część I sekcja 8) wyłącznie na danych o
dacie ≤ `as_of` (brak look-ahead). Kolejność filtrów — pierwszy trafiony
wygrywa: INACTIVE → SUSPENDED → STALE → SHORT_HISTORY → LOW_TURNOVER →
LOW_PRICE → NEEDS_REVIEW; brak jakichkolwiek cen = SHORT_HISTORY.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import yaml
from sqlalchemy.orm import Session

from gpwbot.calendar import GpwCalendar
from gpwbot.config import AppConfig
from gpwbot.context import AppContext
from gpwbot.db import repo
from gpwbot.db.models import Instrument
from gpwbot.db.session import session_scope
from gpwbot.errors import ConfigError
from gpwbot.quality import is_fresh

WARSAW = ZoneInfo("Europe/Warsaw")

# Domyślna ścieżka seedu — względem CWD (CLI działa z roota projektu; wzorzec D-017/D-025).
DEFAULT_SEED_PATH: Path = Path("config/universe_seed.yaml")

# Okno obrotu dla filtra płynności (Część I sekcja 8: "średni obrót 20 sesji").
TURNOVER_WINDOW_SESSIONS = 20


class ExclusionReason(StrEnum):
    """Powody wykluczenia instrumentu z universe (wartości to małe litery)."""

    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    LOW_TURNOVER = "low_turnover"
    LOW_PRICE = "low_price"
    SHORT_HISTORY = "short_history"
    NEEDS_REVIEW = "needs_review"
    STALE = "stale"


@dataclass(frozen=True)
class SeedInstrument:
    """Jeden instrument z pliku seedu."""

    ticker: str
    isin: str | None = None
    name: str | None = None
    segment: str | None = None
    sector: str | None = None
    indices: tuple[str, ...] = ()
    tick_group: str = "default"
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class SyncReport:
    """Wynik `sync_instruments`."""

    synced: int  # instrumenty z seedu (utworzone lub zaktualizowane)
    deactivated: int  # istniejące instrumenty spoza seedu → active=false


@dataclass(frozen=True)
class UniverseResult:
    """Wynik `get_universe`; członkowie i wykluczenia posortowane po tickerze."""

    members: tuple[str, ...] = ()
    excluded: dict[str, ExclusionReason] = field(default_factory=dict)


_SEED_KEYS = {"ticker", "isin", "name", "segment", "sector", "indices", "tick_group", "aliases"}


def _seed_str(raw: Any, key: str, index: int) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(f"wpis seedu #{index}: '{key}' musi być niepustym ciągiem")
    return raw


def _seed_str_list(raw: Any, key: str, index: int) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError(f"wpis seedu #{index}: '{key}' musi być listą")
    items = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"wpis seedu #{index}: elementy '{key}' muszą być niepustymi ciągami")
        items.append(item.strip())
    return tuple(items)


def load_seed(path: str | Path) -> list[SeedInstrument]:
    """Ładuje i waliduje plik seedu (lista map); błędy jako `ConfigError`.

    Brak pliku to błąd — sync z pustym seedem dezaktwyzowałby całą bazę.
    Tickery normalizowane do uppercase, indeksy do lowercase; duplikaty
    tickerów i nieznane klucze rzucają `ConfigError`.
    """
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"plik seedu nie istnieje: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"błędny YAML w {p}: {exc}") from exc
    if not isinstance(raw, list):
        raise ConfigError(f"{p} musi zawierać listę instrumentów")

    seeds: list[SeedInstrument] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ConfigError(f"wpis seedu #{i}: oczekiwano mapy")
        unknown = set(item) - _SEED_KEYS
        if unknown:
            raise ConfigError(f"wpis seedu #{i}: nieznane klucze {sorted(unknown)}")
        ticker_raw = item.get("ticker")
        if not isinstance(ticker_raw, str) or not ticker_raw.strip():
            raise ConfigError(f"wpis seedu #{i}: 'ticker' musi być niepustym ciągiem")
        ticker = ticker_raw.strip().upper()
        if ticker in seen:
            raise ConfigError(f"wpis seedu #{i}: zduplikowany ticker {ticker}")
        seen.add(ticker)

        tick_group = item.get("tick_group", "default")
        if not isinstance(tick_group, str) or not tick_group.strip():
            raise ConfigError(f"wpis seedu #{i}: 'tick_group' musi być niepustym ciągiem")

        indices = tuple(x.lower() for x in _seed_str_list(item.get("indices"), "indices", i))
        seeds.append(
            SeedInstrument(
                ticker=ticker,
                isin=_seed_str(item.get("isin"), "isin", i),
                name=_seed_str(item.get("name"), "name", i),
                segment=_seed_str(item.get("segment"), "segment", i),
                sector=_seed_str(item.get("sector"), "sector", i),
                indices=indices,
                tick_group=tick_group.strip(),
                aliases=_seed_str_list(item.get("aliases"), "aliases", i),
            )
        )
    return seeds


def sync_instruments(ctx: AppContext, seed_path: str | Path = DEFAULT_SEED_PATH) -> SyncReport:
    """Synchronizuje tabelę `instruments` z plikiem seedu (idempotentne).

    Instrumenty z seedu: upsert wszystkich pól + `active=true`. Istniejące
    instrumenty spoza seedu dostają `active=false` (nie są kasowane — wiersz
    zostaje, dane cenowe się nie gubią).
    """
    seeds = load_seed(seed_path)
    seed_tickers = {s.ticker for s in seeds}
    with session_scope(ctx.session_factory) as s:
        repo.upsert_instruments(
            s,
            [
                repo.InstrumentUpsert(
                    ticker=sd.ticker,
                    isin=sd.isin,
                    name=sd.name,
                    segment=sd.segment,
                    sector=sd.sector,
                    index_membership=list(sd.indices),
                    active=True,
                    aliases_json=list(sd.aliases) if sd.aliases else None,
                )
                for sd in seeds
            ],
        )
        deactivated = 0
        for inst in repo.list_instruments(s):
            if inst.ticker not in seed_tickers and inst.active:
                inst.active = False
                deactivated += 1
    return SyncReport(synced=len(seeds), deactivated=deactivated)


def _evaluate(
    session: Session, cfg: AppConfig, calendar: GpwCalendar, as_of: dt.datetime, inst: Instrument
) -> ExclusionReason | None:
    """Pierwszy trafiony powód wykluczenia albo `None` (instrument do members)."""
    if not inst.active:
        return ExclusionReason.INACTIVE

    end = as_of.astimezone(WARSAW).date()
    df = repo.get_prices_df(session, inst.id, end=end)
    if df.empty:
        return ExclusionReason.SHORT_HISTORY  # zero historii (decyzja D-029)

    dd = df[~df.index.duplicated(keep="first")].sort_index()
    last = dd.iloc[-1]
    volume, turnover = last["volume"], last["turnover"]
    if not pd.isna(volume) and volume == 0 and (pd.isna(turnover) or turnover <= 0):
        return ExclusionReason.SUSPENDED

    if not is_fresh(dd.index[-1].date(), as_of, calendar):
        return ExclusionReason.STALE

    if len(dd) < cfg.universe.min_history_sessions:
        return ExclusionReason.SHORT_HISTORY

    window = dd.tail(TURNOVER_WINDOW_SESSIONS)["turnover"].fillna(0.0)
    avg_turnover = sum((Decimal(str(v)) for v in window), Decimal("0")) / len(window)
    if avg_turnover < cfg.universe.min_avg_turnover_pln_20d:
        return ExclusionReason.LOW_TURNOVER

    close = Decimal(str(last["close"]))
    if close < cfg.universe.min_price_pln:
        return ExclusionReason.LOW_PRICE

    if (dd["quality_flag"] == "needs_review").any():
        return ExclusionReason.NEEDS_REVIEW

    return None


def get_universe(ctx: AppContext, as_of: dt.datetime) -> UniverseResult:
    """Członkowie universe i powody wykluczeń na chwilę `as_of` (aware datetime).

    Kandydaci: instrumenty z `index_membership` przecinającym się z
    `cfg.universe.indices`, poza `cfg.universe.exclude_tickers`. Dane wyłącznie
    o dacie ≤ `as_of` (bez look-ahead); wynik deterministyczny (kolejność po
    tickerze). Naive `as_of` rzuca `ValueError`.
    """
    if as_of.tzinfo is None or as_of.tzinfo.utcoffset(as_of) is None:
        raise ValueError("as_of musi być datą świadomą strefy czasowej")

    cfg = ctx.config
    calendar = GpwCalendar(cfg)
    wanted_indices = {i.lower() for i in cfg.universe.indices}
    excluded_tickers = {t.strip().upper() for t in cfg.universe.exclude_tickers if t.strip()}

    members: list[str] = []
    excluded: dict[str, ExclusionReason] = {}
    with ctx.session_factory() as session:
        candidates = [
            inst
            for inst in repo.list_instruments(session)
            if inst.ticker.upper() not in excluded_tickers
            and {m.lower() for m in (inst.index_membership or [])} & wanted_indices
        ]
        for inst in sorted(candidates, key=lambda i: i.ticker):
            reason = _evaluate(session, cfg, calendar, as_of, inst)
            if reason is None:
                members.append(inst.ticker)
            else:
                excluded[inst.ticker] = reason
    return UniverseResult(members=tuple(members), excluded=excluded)
