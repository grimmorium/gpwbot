"""Testy własnościowe ledgera (S06): losowe ciągi kupna i sprzedaży.

Własności: po każdym kroku `check_invariants` przechodzi; `equity = gotówka + pozycje`;
skumulowany realized P/L + niezrealizowany = equity − kapitał początkowy (dywidendy tu
brak). Odchylanie ≤ 0.01 pochodzi wyłącznie z zaokrąglania ilorazów Decimal w unit_cost
(D-035) — realne błędy księgowe dają odchylenie ≥ 0.01.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, settings
from sqlalchemy.orm import Session

from gpwbot.calendar import GpwCalendar
from gpwbot.clock import FakeClock
from gpwbot.config import AppConfig, PathsCfg, PortfolioDef
from gpwbot.costs import commission as calc_commission
from gpwbot.db.models import Fill, Instrument, Order
from gpwbot.errors import LedgerError
from gpwbot.portfolio.service import PortfolioService

TICKERS = ("AAA", "BBB")
INITIAL_CASH = Decimal("100000")

_op = st.tuples(
    st.sampled_from(["BUY", "SELL"]),
    st.sampled_from(TICKERS),
    st.integers(min_value=1, max_value=300),
    st.decimals(min_value=Decimal("1.00"), max_value=Decimal("500.00"), places=2),
)


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
    clock = FakeClock(dt.datetime(2026, 10, 15, 8, 0, tzinfo=dt.UTC))
    calendar = GpwCalendar(cfg, override_path=Path("nieistniejacy_override.yaml"))
    session: Session = factory()
    service = PortfolioService(session, clock, calendar, cfg)
    pid = service.create_portfolio(PortfolioDef(id="prop", strategy="baseline"))
    instruments = {}
    for ticker in TICKERS:
        inst = Instrument(ticker=ticker)
        session.add(inst)
    session.flush()
    for ticker in TICKERS:
        instruments[ticker] = session.query(Instrument).filter_by(ticker=ticker).one().id
    return {
        "cfg": cfg,
        "clock": clock,
        "cal": calendar,
        "s": session,
        "svc": service,
        "pid": pid,
        "inst": instruments,
    }


@settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(st.lists(_op, min_size=1, max_size=20))
def test_wlasnosci_ledgera(prop_env, seq):
    cfg, clock, cal, s, svc = (
        prop_env["cfg"],
        prop_env["clock"],
        prop_env["cal"],
        prop_env["s"],
        prop_env["svc"],
    )
    pid, inst_ids = prop_env["pid"], prop_env["inst"]

    last_price: dict[str, Decimal] = {t: Decimal("10.00") for t in TICKERS}
    for side, ticker, qty, price in seq:
        clock.advance(dt.timedelta(days=1))
        session_date = cal.next_session_after(clock.now())
        last_price[ticker] = price
        comm = calc_commission(price * qty, cfg.costs)
        try:
            with s.begin_nested():  # jak w produkcji: cała operacja w jednej transakcji
                order = Order(
                    portfolio_id=pid,
                    instrument_id=inst_ids[ticker],
                    side=side,
                    qty=qty,
                    order_type="limit",
                    limit_price=None,
                    status="filled",
                    created_at=clock.now(),
                    valid_from_session=session_date,
                )
                s.add(order)
                s.flush()
                fill = Fill(
                    order_id=order.id,
                    session_date=session_date,
                    price=price,
                    qty=qty,
                    commission=comm,
                )
                s.add(fill)
                s.flush()
                svc.apply_fill(fill.id)
        except LedgerError:
            pass  # odrzucona operacja (brak gotówki/stanu) — stan cofnięty do savepointu

        svc.check_invariants(pid)  # własność: zawsze przechodzi, także po odrzuceniach

    prices = last_price
    positions = svc.positions(pid, prices)
    equity = svc.equity(pid, prices)
    cash = svc.cash_total(pid)
    assert equity == cash + sum((p.market_value for p in positions), Decimal("0"))

    realized = Decimal("0")
    rows = (
        s.query(Fill.realized_pnl)
        .join(Order, Fill.order_id == Order.id)
        .filter(Order.portfolio_id == pid, Order.side == "SELL", Fill.realized_pnl.isnot(None))
        .all()
    )
    for (value,) in rows:
        realized += value
    unrealized = sum((p.unrealized_pnl for p in positions), Decimal("0"))
    drift = abs((equity - INITIAL_CASH) - (realized + unrealized))
    assert drift <= Decimal("0.01"), f"odchylenie tożsamości P/L: {drift}"
