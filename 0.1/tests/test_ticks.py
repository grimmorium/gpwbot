"""Testy S02: tick size i zaokrąglanie cen (`gpwbot.ticks`).

Zakres z docs/sessions/S02.md: granice pasm (cena rowna max_price), tryby UP/DOWN/NEAREST,
dokladnosc Decimal, cena <= 0 rzuca blad; wlasnosci hypothesis (kierunek, wielokrotnosc
ticka, idempotencja).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from gpwbot.errors import ConfigError
from gpwbot.ticks import RoundMode, TickTable

# Tabela ticków jest niezmienialna — fixture funkcji jest bezpieczna dla @given.
SETTINGS = settings(suppress_health_check=[HealthCheck.function_scoped_fixture])

REPO_TICK_SIZES = Path(__file__).resolve().parents[1] / "config" / "tick_sizes.yaml"

TICK_YAML = """\
groups:
  default:
    - {max_price: "10", tick: "0.05"}
    - {max_price: "100", tick: "0.10"}
    - {max_price: null, tick: "0.50"}
  small_cap:
    - {max_price: "20", tick: "0.01"}
    - {max_price: null, tick: "0.05"}
"""


@pytest.fixture
def table(tmp_path: Path) -> TickTable:
    path = tmp_path / "tick_sizes.yaml"
    path.write_text(TICK_YAML, encoding="utf-8")
    return TickTable.from_yaml(path)


# --- tick_for ---------------------------------------------------------------


class TestTickFor:
    def test_band_boundary_belongs_to_lower_band(self, table: TickTable) -> None:
        # cena rowna max_price nalezy do danego pasma (S02: granice pasm)
        assert table.tick_for(Decimal("10")) == Decimal("0.05")
        assert table.tick_for(Decimal("100")) == Decimal("0.10")

    def test_just_above_boundary_next_band(self, table: TickTable) -> None:
        assert table.tick_for(Decimal("10.01")) == Decimal("0.10")
        assert table.tick_for(Decimal("100.5")) == Decimal("0.50")

    def test_unbounded_last_band(self, table: TickTable) -> None:
        assert table.tick_for(Decimal("99999")) == Decimal("0.50")

    def test_other_group(self, table: TickTable) -> None:
        assert table.tick_for(Decimal("15"), group="small_cap") == Decimal("0.01")
        assert table.tick_for(Decimal("25"), group="small_cap") == Decimal("0.05")

    def test_unknown_group_raises(self, table: TickTable) -> None:
        with pytest.raises(ValueError):
            table.tick_for(Decimal("10"), group="nie_ma_takiej")

    @pytest.mark.parametrize("price", ["0", "-3.2"])
    def test_non_positive_price_raises(self, table: TickTable, price: str) -> None:
        with pytest.raises(ValueError):
            table.tick_for(Decimal(price))


# --- round_price -------------------------------------------------------------


class TestRoundPrice:
    def test_up(self, table: TickTable) -> None:
        assert table.round_price(Decimal("9.97"), RoundMode.UP) == Decimal("10.00")
        assert table.round_price(Decimal("150.32"), RoundMode.UP) == Decimal("150.50")

    def test_down(self, table: TickTable) -> None:
        assert table.round_price(Decimal("9.97"), RoundMode.DOWN) == Decimal("9.95")
        assert table.round_price(Decimal("150.32"), RoundMode.DOWN) == Decimal("150.00")

    def test_nearest(self, table: TickTable) -> None:
        # 9.97: odleglosci 0.02 (do 9.95) vs 0.03 (do 10.00) → w dol
        assert table.round_price(Decimal("9.97"), RoundMode.NEAREST) == Decimal("9.95")
        # 150.32: odleglosci 0.32 vs 0.18 → w gore
        assert table.round_price(Decimal("150.32"), RoundMode.NEAREST) == Decimal("150.50")

    def test_nearest_tie_rounds_up(self, table: TickTable) -> None:
        # srodek miedzy 9.95 a 10.00 → ROUND_HALF_UP (w gore)
        assert table.round_price(Decimal("9.975"), RoundMode.NEAREST) == Decimal("10.00")

    def test_exact_multiple_is_fixed_point(self, table: TickTable) -> None:
        for mode in (RoundMode.UP, RoundMode.DOWN, RoundMode.NEAREST):
            assert table.round_price(Decimal("10.00"), mode) == Decimal("10.00")

    def test_result_is_exact_decimal(self, table: TickTable) -> None:
        result = table.round_price(Decimal("9.97"), RoundMode.UP)
        assert isinstance(result, Decimal)
        assert str(result) == "10.00"  # bez artefaktow float

    def test_down_below_one_tick_raises(self, table: TickTable) -> None:
        with pytest.raises(ValueError):
            table.round_price(Decimal("0.03"), RoundMode.DOWN)

    @pytest.mark.parametrize("price", ["0", "-1"])
    def test_non_positive_price_raises(self, table: TickTable, price: str) -> None:
        with pytest.raises(ValueError):
            table.round_price(Decimal(price), RoundMode.UP)


# --- is_valid_price -----------------------------------------------------------


class TestIsValidPrice:
    @pytest.mark.parametrize("price", ["10.00", "9.95", "150.50", "3.20"])
    def test_multiples_are_valid(self, table: TickTable, price: str) -> None:
        assert table.is_valid_price(Decimal(price))

    @pytest.mark.parametrize("price", ["9.97", "150.32", "0.03"])
    def test_non_multiples_invalid(self, table: TickTable, price: str) -> None:
        assert not table.is_valid_price(Decimal(price))

    def test_cross_band_result_still_valid(self, table: TickTable) -> None:
        # DOWN z pasma >10 (tick 0.10) na granice — wynik musi byc wielokrotnoscia
        # ticka wlasnego pasma (konsekwencja projektu tabeli; patrz D-015).
        assert table.is_valid_price(table.round_price(Decimal("10.04"), RoundMode.DOWN))

    @pytest.mark.parametrize("price", ["0", "-2"])
    def test_non_positive_invalid(self, table: TickTable, price: str) -> None:
        assert not table.is_valid_price(Decimal(price))


# --- from_yaml -----------------------------------------------------------------


class TestFromYaml:
    def test_repo_tick_table_loads(self) -> None:
        """Plik dostarczany z repozytorium jest poprawny i ma grupe default."""
        loaded = TickTable.from_yaml(REPO_TICK_SIZES)
        assert loaded.tick_for(Decimal("5")) > 0

    def test_missing_file_raises_config_error(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError):
            TickTable.from_yaml(tmp_path / "brak.yaml")

    @pytest.mark.parametrize(
        ("text", "exc"),
        [
            ("groups: [zla-struktura]", ConfigError),  # groups nie jest mapą
            ('{max_price: "10"}\n', ConfigError),  # brak klucza groups
            (
                'groups:\n  default:\n    - {tick: "0.05"}\n',
                ConfigError,  # brak max_price
            ),
            (
                'groups:\n  default:\n    - {max_price: "10", tick: "0.05", extra: 1}\n',
                ConfigError,  # nieznany klucz
            ),
            ("not a mapping:", ConfigError),
            ('groups:\n  default: "nie-lista"\n', ConfigError),  # grupa nie jest listą
            (
                'groups:\n  default:\n    - "abc"\n',
                ConfigError,  # paso nie jest mapą
            ),
            (
                "groups:\n  default:\n    - {max_price: null, tick: true}\n",
                ConfigError,  # bool nie jest liczbą
            ),
            (
                'groups:\n  default:\n    - {max_price: "10", tick: "abc"}\n',
                ConfigError,  # nieprawidłowy string liczbowy
            ),
        ],
    )
    def test_bad_shape_raises(self, tmp_path: Path, text: str, exc: type[Exception]) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(exc):
            TickTable.from_yaml(path)

    @pytest.mark.parametrize(
        ("text",),
        [
            ('groups:\n  other:\n    - {max_price: null, tick: "0.1"}\n',),  # brak default
            ('groups:\n  default:\n    - {max_price: "10", tick: "0"}\n',),  # tick <= 0
            ('groups:\n  default:\n    - {max_price: "-5", tick: "0.1"}\n',),  # max_price < 0
        ],
    )
    def test_bad_values_raise_value_error(self, tmp_path: Path, text: str) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError):
            TickTable.from_yaml(path)

    def test_unquoted_numbers_load_exactly(self, tmp_path: Path) -> None:
        """Liczby bez cudzysłowów (int/float w YAML) są konwertowane dokładnie."""
        path = tmp_path / "unquoted.yaml"
        path.write_text(
            "groups:\n  default:\n    - {max_price: 10, tick: 0.05}\n", encoding="utf-8"
        )
        loaded = TickTable.from_yaml(path)
        assert loaded.tick_for(Decimal("7")) == Decimal("0.05")


# --- walidacja i typy wejsc ------------------------------------------------------


class TestValidationAndTypes:
    def test_empty_groups_raises(self) -> None:
        with pytest.raises(ValueError):
            TickTable({})

    def test_empty_band_list_raises(self) -> None:
        from gpwbot.ticks import TickBand

        with pytest.raises(ValueError):
            TickTable({"default": [TickBand(None, Decimal("0.1"))], "pusta": []})

    def test_duplicate_max_price_raises(self) -> None:
        from gpwbot.ticks import TickBand

        bands = [TickBand(Decimal("10"), Decimal("0.05")), TickBand(Decimal("10"), Decimal("0.1"))]
        with pytest.raises(ValueError):
            TickTable({"default": bands})

    def test_tick_for_rejects_non_decimal(self, table: TickTable) -> None:
        with pytest.raises(ValueError):
            table.tick_for(5)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            table.tick_for("10")  # type: ignore[arg-type]

    def test_round_price_rejects_non_decimal(self, table: TickTable) -> None:
        with pytest.raises(ValueError):
            table.round_price(5.0, RoundMode.UP)  # type: ignore[arg-type]

    def test_bounded_table_out_of_range(self) -> None:
        """Tabela bez pasma nieskończonego: cena ponad granice → błąd / False."""
        from gpwbot.ticks import TickBand

        bounded = TickTable({"default": [TickBand(Decimal("10"), Decimal("0.05"))]})
        with pytest.raises(ValueError):
            bounded.tick_for(Decimal("20"))
        assert not bounded.is_valid_price(Decimal("20"))


# --- wlasnosci (hypothesis) -----------------------------------------------------

PRICES = st.integers(min_value=10_000, max_value=500_0000).map(
    lambda i: Decimal(i) / Decimal(10**4)  # ceny 1.00–500.00 z dokladnoscia 0.0001
)


class TestProperties:
    @given(price=PRICES, mode=st.sampled_from(list(RoundMode)))
    @SETTINGS
    def test_rounding_properties(self, table: TickTable, price: Decimal, mode: RoundMode) -> None:
        tick = table.tick_for(price)
        result = table.round_price(price, mode)

        if mode is RoundMode.UP:
            assert result >= price
        elif mode is RoundMode.DOWN:
            assert 0 < result <= price
        else:
            assert abs(result - price) <= tick / 2

        # wynik jest wielokrotnoscia ticka pasma ceny i ticka wlasnego pasma
        assert result % tick == 0
        assert table.is_valid_price(result)
        # operacja idempotentna
        assert table.round_price(result, mode) == result

    @given(price=PRICES)
    @SETTINGS
    def test_tick_for_is_stable(self, table: TickTable, price: Decimal) -> None:
        tick = table.tick_for(price)
        assert isinstance(tick, Decimal) and tick > 0
