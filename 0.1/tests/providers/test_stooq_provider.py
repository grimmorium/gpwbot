"""Testy S03: `StooqPriceProvider` — HTTP mockowane przez respx (bez sieci).

Zakres z docs/sessions/S03.md: retry 500 → 500 → 200 daje sukces; 404 nie jest
ponawiany; po 3 błędach `ProviderError`; mapowanie symboli z nadpisaniami YAML.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx
import pytest
import respx

from gpwbot.config import ProvidersCfg, StooqCfg
from gpwbot.errors import ConfigError, ProviderError
from gpwbot.providers.prices_stooq import (
    RateLimiter,
    StooqPriceProvider,
    load_symbol_map,
    map_symbol,
)

CSV_OK = "Date,Open,High,Low,Close,Volume\n2026-10-14,98.5,99.2,97.8,98.9,1000\n"


def make_cfg(**stooq_overrides) -> ProvidersCfg:
    """Konfiguracja bez backoffu i z wysokim limitem — testy nie śpią."""
    stooq = StooqCfg(
        requests_per_second=10_000, timeout_seconds=5.0, max_retries=3, retry_backoff_base_s=0.0
    )
    if stooq_overrides:
        stooq = type(stooq)(**{**stooq.model_dump(), **stooq_overrides})
    return ProvidersCfg(stooq=stooq)


def make_provider(tmp_path: Path, cfg: ProvidersCfg | None = None) -> StooqPriceProvider:
    map_file = tmp_path / "symbol_map.yaml"
    if not map_file.exists():
        map_file.write_text("ABC: abc_x\n", encoding="utf-8")
    return StooqPriceProvider(cfg or make_cfg(), symbol_map_path=map_file)


def url_for(symbol: str, start: dt.date, end: dt.date) -> str:
    from gpwbot.providers.prices_stooq import STOOQ_DAILY_URL

    return (
        f"{STOOQ_DAILY_URL}?s={symbol}&i=d"
        f"&d1={start.strftime('%Y%m%d')}&d2={end.strftime('%Y%m%d')}"
    )


START, END = dt.date(2026, 10, 14), dt.date(2026, 10, 15)


class TestRetry:
    def test_500_500_200_daje_sukces(self, tmp_path: Path) -> None:
        provider = make_provider(tmp_path)
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(500) if calls["n"] < 3 else httpx.Response(200, text=CSV_OK)

        with respx.mock:
            route = respx.get(url_for("pkn", START, END)).mock(side_effect=handler)
            raw = provider.fetch_raw("pkn", START, END)
        assert raw == CSV_OK
        assert calls["n"] == 3
        assert route.calls.call_count == 3

    def test_404_nie_jest_ponawiany(self, tmp_path: Path) -> None:
        provider = make_provider(tmp_path)
        with respx.mock:
            route = respx.get(url_for("pkn", START, END)).mock(return_value=httpx.Response(404))
            with pytest.raises(ProviderError, match="HTTP 404"):
                provider.fetch_raw("pkn", START, END)
        assert route.calls.call_count == 1

    def test_po_trzech_błędach_provider_error(self, tmp_path: Path) -> None:
        provider = make_provider(tmp_path)
        with respx.mock:
            route = respx.get(url_for("pkn", START, END)).mock(return_value=httpx.Response(503))
            with pytest.raises(ProviderError, match="3 nieudanych prób"):
                provider.fetch_raw("pkn", START, END)
        assert route.calls.call_count == 3

    def test_timeout_jest_ponawiany(self, tmp_path: Path) -> None:
        provider = make_provider(tmp_path)
        calls = {"n": 0}

        def handler(request: httpx.Request) -> object:
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ReadTimeout("timeout")
            return httpx.Response(200, text=CSV_OK)

        with respx.mock:
            respx.get(url_for("pkn", START, END)).mock(side_effect=handler)
            raw = provider.fetch_raw("pkn", START, END)
        assert raw == CSV_OK
        assert calls["n"] == 2

    def test_get_daily_parsuje_odpowiedź(self, tmp_path: Path) -> None:
        provider = make_provider(tmp_path)
        with respx.mock:
            response = httpx.Response(200, text=CSV_OK)
            respx.get(url_for("pkn", START, END)).mock(return_value=response)
            bars = provider.get_daily("pkn", START, END)
        assert len(bars) == 1 and bars[0].close is not None


class TestSymbolMapping:
    def test_domyślnie_małe_liter(self, tmp_path: Path) -> None:
        provider = make_provider(tmp_path)
        assert provider.symbol_for("PKN") == "pkn"

    def test_nadpisanie_z_yaml(self, tmp_path: Path) -> None:
        provider = make_provider(tmp_path)  # mapa ma ABC → abc_x
        assert provider.symbol_for("ABC") == "abc_x"
        assert provider.symbol_for("abc") == "abc_x"

    def test_brak_pliku_mapy_to_puste_nadpisywania(self, tmp_path: Path) -> None:
        cfg = make_cfg()
        provider = StooqPriceProvider(cfg, symbol_map_path=tmp_path / "brak.yaml")
        assert provider.symbol_for("WIG20") == "wig20"

    def test_load_symbol_map_błędny_yaml(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("a: [nieskończona\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_symbol_map(bad)

    def test_load_symbol_map_nie_jest_mapą(self, tmp_path: Path) -> None:
        notmap = tmp_path / "notmap.yaml"
        notmap.write_text("- a\n- b\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_symbol_map(notmap)

    def test_load_symbol_map_błędna_wartość(self, tmp_path: Path) -> None:
        badval = tmp_path / "badval.yaml"
        badval.write_text("ABC: 123\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_symbol_map(badval)

    def test_map_symbol_czystej_funkcji(self) -> None:
        overrides = {"WIG20": "wig20"}
        assert map_symbol("WIG20", overrides) == "wig20"
        assert map_symbol("pkn", overrides) == "pkn"


class TestRateLimiter:
    def test_druga_zapytanie_czeka(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import gpwbot.providers.prices_stooq as mod

        sleeps: list[float] = []
        monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(s))
        limiter = RateLimiter(requests_per_second=10.0)  # interwał 0.1 s
        limiter.wait()
        limiter.wait()
        assert len(sleeps) == 1
        assert 0 < sleeps[0] <= 0.1 + 1e-6

    def test_pierwsza_zapytanie_nie_czeka(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import gpwbot.providers.prices_stooq as mod

        sleeps: list[float] = []
        monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(s))
        RateLimiter(requests_per_second=1.0).wait()
        assert sleeps == []

    def test_nieprawidłowe_rps(self) -> None:
        with pytest.raises(ValueError):
            RateLimiter(requests_per_second=0.0)
