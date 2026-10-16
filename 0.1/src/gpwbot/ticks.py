"""Tick size GPW: tabela pasm cenowych z YAML i zaokrąglanie cen (Decimal).

A3: ceny jako Decimal; arytmetyka dokładna — dzielenie w kontekście 28 cyfr jest
dokładne dla cen/ticków o skończonych rozwinięciach dziesiętnych, a gdy iloraz nie
jest całkowity, odległość od najbliższej granicy pasma przekracza błąd zaokrąglenia
(patrz D-015). Zródło tabeli: docs/decisions.md (V-02 — wartości przykładowe).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from pathlib import Path

import yaml

from gpwbot.errors import ConfigError

DEFAULT_GROUP = "default"


@dataclass(frozen=True)
class TickBand:
    """Paso cenowe: ceny <= `max_price` (None — bez górnego ograniczenia)."""

    max_price: Decimal | None
    tick: Decimal


class RoundMode(Enum):
    """Tryb zaokrąglania do ticka."""

    UP = "UP"
    DOWN = "DOWN"
    NEAREST = "NEAREST"


def _to_decimal(value: object, what: str) -> Decimal:
    if isinstance(value, bool):
        raise ConfigError(f"{what}: wartość {value!r} nie jest liczbą")
    if isinstance(value, (int, float)):
        value = str(value)  # najkrótsza postać — dokładna dla typowych wartości YAML
    if not isinstance(value, str):
        raise ConfigError(f"{what}: oczekiwano liczby, otrzymano {type(value).__name__}")
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ConfigError(f"{what}: nieprawidłowa wartość liczbową {value!r}") from exc


class TickTable:
    """Tabela ticków: grupy pasm cenowych; `default` jest wymagana."""

    def __init__(self, groups: Mapping[str, Sequence[TickBand]]) -> None:
        if not groups:
            raise ValueError("tabela ticków nie może być pusta")
        if DEFAULT_GROUP not in groups:
            raise ValueError(f"brak grupy '{DEFAULT_GROUP}' w tabeli ticków")

        parsed: dict[str, tuple[TickBand, ...]] = {}
        for name, bands in groups.items():
            if not bands:
                raise ValueError(f"grupa '{name}': pusta lista pasm")
            ordered: list[tuple[Decimal | None, TickBand]] = []
            for band in bands:
                if band.tick <= 0:
                    raise ValueError(
                        f"grupa '{name}': tick musi być dodatni (otrzymano {band.tick})"
                    )
                if band.max_price is not None and band.max_price <= 0:
                    raise ValueError(
                        f"grupa '{name}': max_price musi być dodatnia lub null "
                        f"(otrzymano {band.max_price})"
                    )
                ordered.append((band.max_price, band))
            # sortowanie po górnym ograniczeniu (null = +∞); granice muszą rosnąć ściśle
            ordered.sort(key=lambda item: (item[0] is None, item[0] or Decimal(0)))
            for (prev_max, _), (cur_max, _) in zip(ordered[:-1], ordered[1:], strict=True):
                if prev_max is not None and cur_max is not None and cur_max <= prev_max:
                    raise ValueError(f"grupa '{name}': granice pasm muszą rosnąć ściśle")
            parsed[name] = tuple(band for _, band in ordered)
        self._groups = parsed

    @classmethod
    def from_yaml(cls, path: str | Path) -> TickTable:
        """Ładuje tabelę z YAML; błędy pliku/schematu jako `ConfigError`."""
        file_path = Path(path)
        if not file_path.is_file():
            raise ConfigError(f"plik ticków nie istnieje: {file_path}")
        try:
            raw = yaml.safe_load(file_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ConfigError(f"błędny YAML w {file_path}: {exc}") from exc
        if not isinstance(raw, dict) or "groups" not in raw:
            raise ConfigError(f"{file_path}: oczekiwano mapy z kluczem 'groups'")
        groups_raw = raw["groups"]
        if not isinstance(groups_raw, dict):
            raise ConfigError(f"{file_path}: 'groups' musi być mapą nazwa → lista pasm")

        groups: dict[str, list[TickBand]] = {}
        for name, bands in groups_raw.items():
            if not isinstance(bands, list) or not all(isinstance(b, dict) for b in bands):
                raise ConfigError(f"{file_path}: grupa '{name}' musi być listą pasm")
            parsed_bands: list[TickBand] = []
            for band in bands:
                unknown = set(band) - {"max_price", "tick"}
                missing = {"max_price", "tick"} - set(band)
                if unknown or missing:
                    raise ConfigError(
                        f"{file_path}: paso grupy '{name}' ma klucze {sorted(band)}; "
                        f"oczekiwano ['max_price', 'tick']"
                    )
                max_raw = band["max_price"]
                parsed_bands.append(
                    TickBand(
                        max_price=None if max_raw is None else _to_decimal(max_raw, "max_price"),
                        tick=_to_decimal(band["tick"], "tick"),
                    )
                )
            groups[str(name)] = parsed_bands
        return cls(groups)

    def tick_for(self, price: Decimal, group: str = DEFAULT_GROUP) -> Decimal:
        """Tick dla ceny z grupy; cena równa `max_price` należy do danego pasma."""
        if not isinstance(price, Decimal):
            raise ValueError("price musi być typu Decimal")
        if price <= 0:
            raise ValueError(f"cena musi być dodatnia (otrzymano {price})")
        bands = self._groups.get(group)
        if bands is None:
            raise ValueError(f"nieznana grupa ticków: '{group}'")
        for band in bands:
            if band.max_price is None or price <= band.max_price:
                return band.tick
        raise ValueError(f"cena {price} poza zakresem tabeli ticków (grupa '{group}')")

    def round_price(
        self, price: Decimal, mode: RoundMode, *, group: str = DEFAULT_GROUP
    ) -> Decimal:
        """Zaokrągla cenę do wielokrotności ticka; tryby UP/DOWN/NEAREST (HALF_UP)."""
        if not isinstance(price, Decimal):
            raise ValueError("price musi być typu Decimal")
        if price <= 0:
            raise ValueError(f"cena musi być dodatnia (otrzymano {price})")
        tick = self.tick_for(price, group)
        ratio = price / tick
        k_floor = int(ratio.to_integral_value(rounding=ROUND_FLOOR))
        lower = Decimal(k_floor) * tick
        if mode is RoundMode.DOWN:
            if lower <= 0:
                raise ValueError(
                    f"zaokrąglenie w dół ceny {price} daje wartość nie większą od zera "
                    f"(tick {tick})"
                )
            return lower
        if price == lower:
            return lower  # dokładna wielokrotność — punkt stały we wszystkich trybach
        upper = lower + tick
        if mode is RoundMode.UP:
            return upper
        k_nearest = int(ratio.to_integral_value(rounding=ROUND_HALF_UP))
        return Decimal(k_nearest) * tick

    def is_valid_price(self, price: Decimal, group: str = DEFAULT_GROUP) -> bool:
        """Czy cena jest dodatnią wielokrotnością ticka swojego pasma."""
        if not isinstance(price, Decimal) or price <= 0:
            return False
        try:
            tick = self.tick_for(price, group)
        except ValueError:
            return False
        k_floor = int((price / tick).to_integral_value(rounding=ROUND_FLOOR))
        return Decimal(k_floor) * tick == price
