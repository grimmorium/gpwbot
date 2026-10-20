"""Testy S03: parser CSV Stooq (`parse_stooq_csv`) — czysta funkcja, bez HTTP.

Zakres z docs/sessions/S03.md: nagłówki PL i EN, pusty plik, komunikat bez danych,
przecinek jako separator dziesiętny, zmienna liczba kolumn.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from gpwbot.errors import ProviderError
from gpwbot.providers.prices_stooq import parse_stooq_csv


class TestValidCsv:
    def test_naglowki_angielskie(self) -> None:
        text = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-10-14,98.50,99.20,97.80,98.90,1234567\n"
            "2026-10-15,98.90,100.10,98.40,99.75,2345678\n"
        )
        bars = parse_stooq_csv(text)
        assert len(bars) == 2
        first = bars[0]
        assert first.date == dt.date(2026, 10, 14)
        assert first.open == Decimal("98.50")
        assert first.high == Decimal("99.20")
        assert first.low == Decimal("97.80")
        assert first.close == Decimal("98.90")
        assert first.volume == 1_234_567
        assert first.adj_close is None

    def test_naglowki_polskie(self) -> None:
        text = (
            "Data,Otwarcie,Maksimum,Minimum,Zamknięcie,Wolumen\n"
            "2026-10-14,98.50,99.20,97.80,98.90,1234567\n"
        )
        bars = parse_stooq_csv(text)
        assert len(bars) == 1
        assert bars[0].open == Decimal("98.50")
        assert bars[0].close == Decimal("98.90")

    def test_wielkosc_liter_naglowków_nie_ma_znaczenia(self) -> None:
        text = "date,OPEN,HIGH,LOW,CLOSE\n2026-10-14,1,2,0.5,1.5\n"
        bars = parse_stooq_csv(text)
        assert bars[0].close == Decimal("1.5")

    def test_tylko_nagłówek_zwraca_pustą_listę(self) -> None:
        assert parse_stooq_csv("Date,Open,High,Low,Close\n") == []


class TestNoData:
    def test_pusty_plik_rzuca_provider_error(self) -> None:
        with pytest.raises(ProviderError):
            parse_stooq_csv("")

    def test_tylko_białe_znaki_rzuca_provider_error(self) -> None:
        with pytest.raises(ProviderError, match="pusta"):
            parse_stooq_csv("  \n\n")

    def test_komunikat_zamiast_csv_rzuca_provider_error(self) -> None:
        with pytest.raises(ProviderError, match="No data available"):
            parse_stooq_csv("No data available")

    def test_dowolny_tekst_bez_nagłówka_cenowego(self) -> None:
        with pytest.raises(ProviderError):
            parse_stooq_csv("symbol not found\ntry another symbol")


class TestDecimalComma:
    def test_przecinek_jako_separator_dziesiętny(self) -> None:
        # Wartości 97,80 / 98,10 / 97,50 / 98,50 — każda zajmuje dwie komórki.
        text = "Date,Open,High,Low,Close\n2026-10-14,97,80;98,10;97,50;98,50\n".replace(";", ",")
        bars = parse_stooq_csv(text)
        assert bars[0].open == Decimal("97.80")
        assert bars[0].high == Decimal("98.10")
        assert bars[0].low == Decimal("97.50")
        assert bars[0].close == Decimal("98.50")

    def test_przecinki_w_kolumnach_volume_i_close(self) -> None:
        row = "2026-10-14,97,80" + "," + "98,10" + "," + "97,50" + "," + "98,50" + "," + "1234,56"
        text = f"Date,Open,High,Low,Close,Volume\n{row}\n"
        bars = parse_stooq_csv(text)
        assert bars[0].close == Decimal("98.50")
        assert bars[0].volume == 1_234

    def test_zacytowane_wartości_z_przecinkiem(self) -> None:
        text = 'Date,Open,High,Low,Close\n2026-10-14,"97,80","98,10","97,50","98,50"\n'
        bars = parse_stooq_csv(text)
        assert bars[0].open == Decimal("97.80")
        assert bars[0].close == Decimal("98.50")


class TestVariableColumns:
    def test_brak_kolumny_volume(self) -> None:
        text = "Date,Open,High,Low,Close\n2026-10-14,1,2,0.5,1.5\n"
        bars = parse_stooq_csv(text)
        assert bars[0].volume is None

    def test_kolumna_turnover_jest_pobierana(self) -> None:
        text = (
            "Date,Open,High,Low,Close,Volume,Turnover\n"
            "2026-10-14,98.5,99.2,97.8,98.9,1000,98900.0\n"
        )
        bars = parse_stooq_csv(text)
        assert bars[0].turnover_pln == Decimal("98900.0")

    def test_kolumna_adj_close_ustawia_wartość(self) -> None:
        text = "Date,Open,High,Low,Close,Adj Close\n2026-10-14,98.5,99.2,97.8,98.9,95.1\n"
        bars = parse_stooq_csv(text)
        assert bars[0].adj_close == Decimal("95.1")


class TestMalformed:
    def test_błędna_data_rzuca_provider_error(self) -> None:
        with pytest.raises(ProviderError, match="data"):
            parse_stooq_csv("Date,Open,High,Low,Close\n2026-13-99,1,2,0.5,1.5\n")

    def test_błędna_wartość_ceny_rzuca_provider_error(self) -> None:
        with pytest.raises(ProviderError, match="close"):
            parse_stooq_csv("Date,Open,High,Low,Close\n2026-10-14,1,2,0.5,abc\n")

    def test_zbyt_malo_kolumn_w_wierszu(self) -> None:
        with pytest.raises(ProviderError):
            parse_stooq_csv("Date,Open,High,Low,Close\n2026-10-14,1,2\n")
