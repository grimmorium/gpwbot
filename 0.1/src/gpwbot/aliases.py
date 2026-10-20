"""Słownik aliasów do linkowania tekstów (S04; używany w S10).

Walidacja wg specyfikacji S04: ten sam alias przypisany do więcej niż jednego
tickera jest błędem, chyba że oznaczono `ambiguous: true` (we wszystkich
wpisach z tym aliasem); aliasy krótsze niż 4 znaki wymagają flagi
`exact_case: true`. Błędy pliku/schematu/walidacji jako `ConfigError`; brak
pliku = pusty słownik (wzorzec D-025).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from gpwbot.errors import ConfigError

# Alias krótszy niż ta liczba znaków wymaga `exact_case: true` (S04).
MIN_ALIAS_LEN = 4


@dataclass(frozen=True)
class AliasEntry:
    """Jeden wpis słownika: alias → tickery (+ flagi walidacyjne)."""

    alias: str
    tickers: tuple[str, ...]
    ambiguous: bool = False
    exact_case: bool = False


_ENTRY_KEYS = {"alias", "tickers", "ambiguous", "exact_case"}


def _parse_entry(raw: Any, index: int) -> AliasEntry:
    if not isinstance(raw, dict):
        raise ConfigError(f"wpis aliasów #{index}: oczekiwano mapy")
    unknown = set(raw) - _ENTRY_KEYS
    if unknown:
        raise ConfigError(f"wpis aliasów #{index}: nieznane klucze {sorted(unknown)}")

    alias = raw.get("alias")
    if not isinstance(alias, str) or not alias.strip():
        raise ConfigError(f"wpis aliasów #{index}: 'alias' musi być niepustym ciągiem")

    tickers_raw = raw.get("tickers")
    if not isinstance(tickers_raw, list) or not tickers_raw:
        raise ConfigError(f"wpis aliasów #{index}: 'tickers' musi być niepustą listą")
    tickers: list[str] = []
    for item in tickers_raw:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(
                f"wpis aliasów #{index}: elementy 'tickers' muszą być niepustymi ciągami"
            )
        tickers.append(item.strip().upper())

    ambiguous = raw.get("ambiguous", False)
    exact_case = raw.get("exact_case", False)
    if not isinstance(ambiguous, bool) or not isinstance(exact_case, bool):
        raise ConfigError(f"wpis aliasów #{index}: 'ambiguous'/'exact_case' muszą być boolami")

    return AliasEntry(
        alias=alias.strip(), tickers=tuple(tickers), ambiguous=ambiguous, exact_case=exact_case
    )


def _validate(entries: list[AliasEntry]) -> None:
    """Reguły S04: krótkie aliasy → `exact_case`; kolizje tickerów → `ambiguous`."""
    for i, entry in enumerate(entries):
        if len(entry.alias) < MIN_ALIAS_LEN and not entry.exact_case:
            raise ConfigError(
                f"wpis aliasów #{i}: alias {entry.alias!r} krótszy niż {MIN_ALIAS_LEN} "
                "znaków wymaga exact_case: true"
            )

    groups: dict[str, list[AliasEntry]] = {}
    for entry in entries:
        groups.setdefault(entry.alias.lower(), []).append(entry)
    for key in sorted(groups):
        group = groups[key]
        tickers = {t for e in group for t in e.tickers}
        if len(tickers) > 1 and not all(e.ambiguous for e in group):
            raise ConfigError(
                f"alias {key!r} przypisany do wielu tickerów ({', '.join(sorted(tickers))}) — "
                "wymagane ambiguous: true we wszystkich wpisach z tym aliasem"
            )


def load_aliases(path: str | Path) -> list[AliasEntry]:
    """Ładuje i waliduje słownik aliasów z YAML; brak pliku = pusty słownik.

    Format: mapa z kluczem `aliases` — lista wpisów `alias`/`tickers`
    (+ opcjonalnie `ambiguous`, `exact_case`). Tickery normalizowane do
    uppercase; nieznane klucze i naruszenia reguł walidacji rzucają
    `ConfigError`.
    """
    p = Path(path)
    if not p.is_file():
        return []
    try:
        raw: Any = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"błędny YAML w {p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{p} musi zawierać mapę z kluczem 'aliases'")
    unknown = set(raw) - {"aliases"}
    if unknown:
        raise ConfigError(f"nieznane klucze w {p}: {sorted(unknown)}")

    entries_raw = raw.get("aliases", [])
    if not isinstance(entries_raw, list):
        raise ConfigError(f"'aliases' w {p} musi być listą")
    entries = [_parse_entry(item, i) for i, item in enumerate(entries_raw)]
    _validate(entries)
    return entries
