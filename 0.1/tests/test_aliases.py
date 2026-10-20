"""Testy S04: słownik aliasów (`aliases.load_aliases`) i reguły walidacji.

Zakres z docs/sessions/S04.md: kolizja (ten sam alias → dwa tickery) jest odrzucona,
`ambiguous: true` ją dopuszcza; krótki alias (< 4 znaki) bez `exact_case: true`
jest odrzucony. Błędy jako `ConfigError`; brak pliku = pusty słownik.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gpwbot.aliases import AliasEntry, load_aliases
from gpwbot.errors import ConfigError


def write_yaml(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "aliases.yaml"
    path.write_text(text, encoding="utf-8")
    return path


class TestLoadAliases:
    def test_validny_plik_ładuje_wpisy(self, tmp_path: Path) -> None:
        path = write_yaml(
            tmp_path,
            "aliases:\n"
            "  - alias: Orlen\n"
            "    tickers: [pkn]\n"
            "  - alias: CDR\n"
            "    tickers: [CDR]\n"
            "    exact_case: true\n",
        )

        entries = load_aliases(path)

        assert entries == [
            AliasEntry(alias="Orlen", tickers=("PKN",)),
            AliasEntry(alias="CDR", tickers=("CDR",), exact_case=True),
        ]

    def test_brak_pliku_daje_pusty_słownik(self, tmp_path: Path) -> None:
        assert load_aliases(tmp_path / "brak.yaml") == []


class TestWalidacjaKolizji:
    def test_kolizja_bez_flagi_jest_odrzucana(self, tmp_path: Path) -> None:
        path = write_yaml(
            tmp_path,
            "aliases:\n  - alias: Bank\n    tickers: [AAA]\n  - alias: Bank\n    tickers: [BBB]\n",
        )

        with pytest.raises(ConfigError):
            load_aliases(path)

    def test_wielotickerowy_wpis_bez_flagi_jest_odrzucany(self, tmp_path: Path) -> None:
        path = write_yaml(
            tmp_path,
            "aliases:\n  - alias: Bank\n    tickers: [AAA, BBB]\n",
        )

        with pytest.raises(ConfigError):
            load_aliases(path)

    def test_ambiguous_true_dopuszcza_kolizję(self, tmp_path: Path) -> None:
        path = write_yaml(
            tmp_path,
            "aliases:\n"
            "  - alias: Bank\n"
            "    tickers: [AAA]\n"
            "    ambiguous: true\n"
            "  - alias: bank\n"
            "    tickers: [BBB]\n"
            "    ambiguous: true\n",
        )

        entries = load_aliases(path)

        assert len(entries) == 2
        assert all(e.ambiguous for e in entries)

    def test_kolizja_częściowo_oznaczona_jest_odrzucana(self, tmp_path: Path) -> None:
        path = write_yaml(
            tmp_path,
            "aliases:\n"
            "  - alias: Bank\n"
            "    tickers: [AAA]\n"
            "  - alias: bank\n"
            "    tickers: [BBB]\n"
            "    ambiguous: true\n",
        )

        with pytest.raises(ConfigError):
            load_aliases(path)


class TestKrótkieAliasy:
    def test_krótki_alias_bez_exact_case_jest_odrzucany(self, tmp_path: Path) -> None:
        path = write_yaml(
            tmp_path,
            "aliases:\n  - alias: CD\n    tickers: [CDR]\n",
        )

        with pytest.raises(ConfigError):
            load_aliases(path)

    def test_krótki_alias_z_exact_case_przechodzi(self, tmp_path: Path) -> None:
        path = write_yaml(
            tmp_path,
            "aliases:\n  - alias: CD\n    tickers: [CDR]\n    exact_case: true\n",
        )

        (entry,) = load_aliases(path)

        assert entry.exact_case is True


class TestSchemat:
    def test_nieznany_klucz_wpisu_jest_błędem(self, tmp_path: Path) -> None:
        path = write_yaml(
            tmp_path,
            "aliases:\n  - alias: Orlen\n    tickers: [PKN]\n    flaga: true\n",
        )

        with pytest.raises(ConfigError):
            load_aliases(path)

    def test_puste_tickery_są_błędem(self, tmp_path: Path) -> None:
        path = write_yaml(
            tmp_path,
            "aliases:\n  - alias: Orlen\n    tickers: []\n",
        )

        with pytest.raises(ConfigError):
            load_aliases(path)

    def test_plik_musi_być_mapą(self, tmp_path: Path) -> None:
        path = write_yaml(tmp_path, "- alias: Orlen\n  tickers: [PKN]\n")

        with pytest.raises(ConfigError):
            load_aliases(path)
