"""Testy własnościowe symulatora wykonania (S07): losowe zlecenia i ceny sesja po sesji.

Własność: po każdym kroku `check_invariants` przechodzi — także gdy część zleceń jest
odrzucona (NO_CASH/NO_POSITION) albo wygasa (LIMIT_NOT_MET/VOLUME_LIMIT/NO_BAR).
Zachowanie wzorca testów S06: cały krok w savepoincie, deterministyczny kalendarz.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, settings
from sqlalchemy import select
from sqlalchemy.orm import Session

from gpwbot.calendar import GpwCalendar
from gpwbot.clock import FakeClock
from gpwbot.config import AppConfig, PathsCfg, PortfolioDef
from gpwbot.db.models import Instrument, PricesDaily
from gpwbot.domain import OrderIntent, Side
from gpwbot.execution.sim_broker import SimBroker
from gpwbot.portfolio.service import PortfolioService
from gpwbot.ticks import TickBand, TickTable

TICKERS = ("AAA", "BBB")
INITIAL_CASH = Decimal("100000")
TICKS = TickTable({"default": [TickBand(None, Decimal("0.10"))]})

# krok: (czy składać zlecenia, lista intencji, otwarcia na sesję ticker→cena|None)
_intent = st.tuples(
    st.sampled_from([Side.BUY, Side.SELL]),
    st.sampled_from(TICKERS),
    st.integers(min_value=1, max_value=300),
    st.decimals(min_value=Decimal("4.00"), max_value=Decimal("21.00"), places=2),
)
_opens = st.dictionaries(
    keys=st.sampled_from(TICKERS),
    values=st.one_of(
        st.none(),  # brak świecy → NO_BAR
        st.decimals(min_value=Decimal("4.50"), max_value=Decimal("20.50"), places=2),
    ),
)
_step = st.tuples(st.booleans(), st.lists(_intent, min_size=1, max_size=3), _opens)


@pytest.fixture
def prop_env(tmp_db):
    engine, factory = tmp_db
    paths = PathsCfg(
        db_path=Path(engine.url.database or ".").parent / "prop.db",
        data_dir=Path(engine.url.database or ".").parent / "data",
        raw_dir=Path(engine.url.database or ".").parent / "raw",
        reports_dir=Path(engine.url.database or ".").parent / "reports",
    )
    cfg = AppConfig(paths=paths)
    clock = FakeClock(dt.datetime(2026, 10, 15, 8, 15, tzinfo=dt.UTC))
    calendar = GpwCalendar(cfg, override_path=Path("nieistniejacy_override.yaml"))
    session: Session = factory()
    service = PortfolioService(session, clock, calendar, cfg)
    broker = SimBroker(session, service, calendar, TICKS, cfg, clock)
    pid = service.create_portfolio(PortfolioDef(id="prop", strategy="baseline"))
    inst_ids: dict[str, int] = {}
    for ticker in TICKERS:
        session.add(Instrument(ticker=ticker))
    session.flush()
    for ticker in TICKERS:
        inst_ids[ticker] = session.query(Instrument).filter_by(ticker=ticker).one().id
    return {
        "cfg": cfg,
        "clock": clock,
        "cal": calendar,
        "s": session,
        "svc": service,
        "broker": broker,
        "pid": pid,
        "inst": inst_ids,
    }


@settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(st.lists(_step, min_size=1, max_size=12))
def test_wlasnosci_symulatora(prop_env, steps):
    clock, cal, s = prop_env["clock"], prop_env["cal"], prop_env["s"]  # type: ignore[assignment]
    svc, broker, pid = prop_env["svc"], prop_env["broker"], prop_env["pid"]  # type: ignore[assignment]
    inst_ids = prop_env["inst"]

    last_processed: dt.date | None = None
    for do_submit, intents, opens in steps:
        with s.begin_nested():  # jak w produkcji: cały krok w jednej transakcji
            if do_submit:
                broker.submit(
                    pid,
                    None,
                    [
                        OrderIntent(ticker=ticker, side=side, qty=qty, limit_price=limit)
                        for side, ticker, qty, limit in intents
                    ],
                )

            clock.advance(dt.timedelta(hours=20))  # skok w czasie; sesje mogą się pomijać
            session_date = cal.next_session_after(clock.now())
            if session_date != last_processed:
                # fixture jest function-scoped (stan kumuluje się między przykładami
                # hypothesis) — świecę dodajemy tylko, jeśli dla tej daty jej jeszcze nie ma
                existing = set(
                    s.execute(
                        select(PricesDaily.instrument_id).where(
                            PricesDaily.date == session_date,
                            PricesDaily.source == "stooq",
                            PricesDaily.instrument_id.in_(list(inst_ids.values())),
                        )
                    ).scalars()
                )
                for ticker, open_ in opens.items():
                    if open_ is not None and inst_ids[ticker] not in existing:
                        s.add(
                            PricesDaily(
                                instrument_id=inst_ids[ticker],
                                date=session_date,
                                source="stooq",
                                open=open_,
                                high=open_,
                                low=open_,
                                close=open_,
                                volume=None,
                                ingested_at=clock.now(),
                            )
                        )
                s.flush()
                broker.process_session(pid, session_date)
                last_processed = session_date

        svc.check_invariants(
            pid
        )  # własność: zawsze przechodzi, także po odrzuceniach/wygaśnięciach

    # na koniec: gotówka nie może być ujemna (niezmiennik wprost z ledgeru)
    assert svc.cash_total(pid) >= Decimal("0")
