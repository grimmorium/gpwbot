"""Testy @network (domyślnie pomijane, A4): jedno rzeczywiste zapytanie do Stooq.

Cel: sprawdzenie, że format odpowiedzi nadal pasuje do parsera i wzorzec adresu
działa (V-03). Uruchomienie jawnie: `pytest -m network tests/providers/`.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import httpx
import pytest

from gpwbot.clock import SystemClock
from gpwbot.providers.prices_stooq import STOOQ_DAILY_URL, parse_stooq_csv

WARSAW = ZoneInfo("Europe/Warsaw")


@pytest.mark.network
def test_rzeczywisty_format_stooq_pasuje_do_parsera() -> None:
    end = SystemClock().now().astimezone(WARSAW).date()
    start = end - dt.timedelta(days=14)
    url = f"{STOOQ_DAILY_URL}?s=pkn&i=d&d1={start.strftime('%Y%m%d')}&d2={end.strftime('%Y%m%d')}"
    response = httpx.get(url, timeout=30.0)
    assert response.status_code == 200, f"Stooq zwrócił HTTP {response.status_code}"

    bars = parse_stooq_csv(response.text)
    assert bars, "parser nie wyprodukował ani jednej świecy — format się zmienił?"
    for bar in bars:
        assert start <= bar.date <= end
        assert bar.close > 0
