"""Testy integracyjne PortfolioService (S06): fills, rezerwacje, T+2, dywidendy, splity."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from gpwbot.calendar import GpwCalendar
from gpwbot.clock import FakeClock
from gpwbot.config import AppConfig, MarketCfg, PathsCfg
from gpwbot.costs import commission as calc_commission
from gpwbot.db.models import (
    CashLedgerEntry,
    CorporateAction,
    Fill,
    Instrument,
    Order,
    PortfolioSnapshot,
    TaxLot,
)
from gpwbot.errors import LedgerError
from gpwbot.portfolio.service import PortfolioService

D = dt.date.fromisoformat


def _paths(tmp_path: Path) -> PathsCfg:
    return PathsCfg(
        db_path=tmp_path / "t.db",
        data_dir=tmp_path / "data",
        raw_dir=tmp_path / "raw",
        reports_dir=tmp_path / "reports",
    )


@pytest.fixture
def env(tmp_db):
    """Standardowe środowisko: cfg (T+2, prowizja 0.3%/min 5), kalendarz bez nadpisań."""
    engine, factory = tmp_db
    cfg = AppConfig(paths=_paths(Path(engine.url.database or ".")))
    clock = FakeClock(dt.datetime(2026, 10, 15, 8, 0, tzinfo=dt.UTC))
    calendar = GpwCalendar(cfg, override_path=Path("nieistniejacy_override.yaml"))
    session: Session = factory()
    service = PortfolioService(session, clock, calendar, cfg)
    return {"cfg": cfg, "clock": clock, "cal": calendar, "s": session, "svc": service}


def _instrument(s: Session, ticker: str) -> Instrument:
    inst = Instrument(ticker=ticker)
    s.add(inst)
    s.flush()
    return inst


def _order(s: Session, pid: int, inst_id: int, side: str, qty: int, date: dt.date) -> Order:
    order = Order(
        portfolio_id=pid,
        instrument_id=inst_id,
        side=side,
        qty=qty,
        order_type="limit",
        limit_price=None,
        status="filled",
        created_at=dt.datetime(2026, 10, 15, 8, 0, tzinfo=dt.UTC),
        valid_from_session=date,
    )
    s.add(order)
    s.flush()
    return order


def _fill(s: Session, order: Order, date: dt.date, price: Decimal, qty: int, comm: Decimal) -> Fill:
    fill = Fill(order_id=order.id, session_date=date, price=price, qty=qty, commission=comm)
    s.add(fill)
    s.flush()
    return fill


def _entries(s: Session, pid: int) -> list[CashLedgerEntry]:
    rows = s.query(CashLedgerEntry).filter_by(portfolio_id=pid).order_by(CashLedgerEntry.id).all()
    return rows


# --- portfel i gotówka -----------------------------------------------------------


def test_create_portfolio_wpłata_i_idempotencja(env):
    svc, s = env["svc"], env["s"]
    from gpwbot.config import PortfolioDef

    pid = svc.create_portfolio(PortfolioDef(id="p1", strategy="baseline"))
    assert svc.cash_total(pid) == Decimal("100000")
    entries = _entries(s, pid)
    assert [(e.type, str(e.amount), e.available_from) for e in entries] == [
        ("DEPOSIT", "100000", None)
    ]
    again = svc.create_portfolio(PortfolioDef(id="p1", strategy="baseline"))
    assert again == pid
    assert len(_entries(s, pid)) == 1


def test_cash_available_minus_rezerwacje(env):
    svc, s = env["svc"], env["s"]
    from gpwbot.config import PortfolioDef

    pid = svc.create_portfolio(PortfolioDef(id="p1", strategy="baseline"))
    inst = _instrument(s, "AAA")
    order = _order(s, pid, inst.id, "BUY", 100, D("2026-10-15"))
    today = D("2026-10-15")
    assert svc.cash_available(pid, today) == Decimal("100000")

    svc.reserve(pid, order.id, Decimal("4000"))
    assert svc.cash_available(pid, today) == Decimal("96000")
    # nadpisanie rezerwacji tego samego zlecenia
    svc.reserve(pid, order.id, Decimal("2500"))
    assert svc.cash_available(pid, today) == Decimal("97500")

    svc.release(order.id)
    assert svc.cash_available(pid, today) == Decimal("100000")
    # release idempotentne
    svc.release(order.id)
    with pytest.raises(ValueError):
        svc.reserve(pid, order.id, Decimal("-1"))
    other = svc.create_portfolio(PortfolioDef(id="p2", strategy="baseline"))
    with pytest.raises(LedgerError):
        svc.reserve(other, order.id, Decimal("100"))


# --- fills: BUY / SELL -------------------------------------------------------------


def test_apply_fill_buy_partia_i_wpisy(env):
    """Kupno 100 po 10 z prowizją minimalną 5 PLN → unit_cost = 10.05."""
    svc, s, cfg = env["svc"], env["s"], env["cfg"]
    from gpwbot.config import PortfolioDef

    pid = svc.create_portfolio(PortfolioDef(id="p1", strategy="baseline"))
    inst = _instrument(s, "AAA")
    price, qty = Decimal("10.00"), 100
    comm = calc_commission(price * qty, cfg.costs)
    assert comm == Decimal("5.00")  # min. prowizja (3.00 < 5.00)

    order = _order(s, pid, inst.id, "BUY", qty, D("2026-10-14"))
    fill = _fill(s, order, D("2026-10-14"), price, qty, comm)
    svc.apply_fill(fill.id)

    lot = s.query(TaxLot).filter_by(portfolio_id=pid).one()
    assert lot.qty_remaining == 100
    assert lot.buy_price == Decimal("10.05")  # cena + prowizja/qty (D-035)
    assert svc.cash_total(pid) == Decimal("100000") - price * qty - comm

    rows = [(e.type, str(e.amount), e.available_from, e.ref) for e in _entries(s, pid)]
    assert ("BUY", "-1000.00", D("2026-10-14"), str(fill.id)) in rows
    assert ("COMMISSION", "-5.00", D("2026-10-14"), str(fill.id)) in rows


def test_apply_fill_sell_fifo_realized_pnl(env):
    """Kupno 100 po 10 i 50 po 12, sprzedaż 120 → zużycie 100+20, P/L z prowizjami."""
    svc, s, cfg = env["svc"], env["s"], env["cfg"]
    from gpwbot.config import PortfolioDef

    pid = svc.create_portfolio(PortfolioDef(id="p1", strategy="baseline"))
    inst = _instrument(s, "AAA")

    def buy(date: dt.date, price: Decimal, qty: int) -> None:
        comm = calc_commission(price * qty, cfg.costs)
        order = _order(s, pid, inst.id, "BUY", qty, date)
        fill = _fill(s, order, date, price, qty, comm)
        svc.apply_fill(fill.id)

    buy(D("2026-10-14"), Decimal("10.00"), 100)  # unit_cost 10.05 (min. prowizja 5/100 = 0.05)
    buy(D("2026-10-15"), Decimal("12.00"), 50)  # unit_cost 12.10 (prowizja 5/50 = 0.10)

    sell_price, sell_qty = Decimal("14.00"), 120
    sell_comm = calc_commission(sell_price * sell_qty, cfg.costs)  # max(5.04, 5) = 5.04
    assert sell_comm == Decimal("5.04")
    order = _order(s, pid, inst.id, "SELL", sell_qty, D("2026-10-15"))
    fill = _fill(s, order, D("2026-10-15"), sell_price, sell_qty, sell_comm)
    svc.apply_fill(fill.id)

    s.refresh(fill)
    # przychód 1680 − koszt (100×10.05 + 20×12.10 = 1247) − prowizja 5.04
    assert fill.realized_pnl == Decimal("427.96")

    lot = s.query(TaxLot).filter_by(portfolio_id=pid).one()
    assert lot.qty_remaining == 30
    assert lot.buy_price == Decimal("12.10")
    assert svc.cash_total(pid) == (
        Decimal("100000")
        - Decimal("1005.00")
        - Decimal("605.00")
        + sell_price * sell_qty
        - sell_comm
    )


def test_apply_fill_sell_wieksza_niz_stan(env):
    svc, s = env["svc"], env["s"]
    from gpwbot.config import PortfolioDef

    pid = svc.create_portfolio(PortfolioDef(id="p1", strategy="baseline"))
    inst = _instrument(s, "AAA")
    order = _order(s, pid, inst.id, "SELL", 10, D("2026-10-15"))
    fill = _fill(s, order, D("2026-10-15"), Decimal("10.00"), 10, Decimal("3.00"))
    with pytest.raises(LedgerError):
        svc.apply_fill(fill.id)


def test_apply_fill_buy_przekraczajacy_gotówkę(env):
    """Księgowanie kupna przekraczającego gotówkę rzuca LedgerError (niezmiennik)."""
    svc, s = env["svc"], env["s"]
    from gpwbot.config import PortfolioDef

    pid = svc.create_portfolio(PortfolioDef(id="p1", strategy="baseline"))
    inst = _instrument(s, "AAA")
    order = _order(s, pid, inst.id, "BUY", 20_000, D("2026-10-15"))
    fill = _fill(
        s,
        order,
        D("2026-10-15"),
        Decimal("50.00"),
        20_000,
        calc_commission(Decimal("1000000"), env["cfg"].costs),
    )
    with pytest.raises(LedgerError):
        svc.apply_fill(fill.id)


# --- rozliczenie T+2 -----------------------------------------------------------------


def test_t_plus_2_czwartek_poniedzialek(env):
    """Sprzedaż w czwartek → gotówka dostępna w poniedziałek (T+2 przez kalendarz)."""
    svc, s, cfg = env["svc"], env["s"], env["cfg"]
    from gpwbot.config import PortfolioDef

    pid = svc.create_portfolio(PortfolioDef(id="p1", strategy="baseline"))
    inst = _instrument(s, "AAA")
    buy_order = _order(s, pid, inst.id, "BUY", 100, D("2026-10-14"))
    buy_fill = _fill(
        s,
        buy_order,
        D("2026-10-14"),
        Decimal("10.00"),
        100,
        calc_commission(Decimal("1000"), cfg.costs),
    )
    svc.apply_fill(buy_fill.id)

    sell_price = Decimal("14.00")
    sell_order = _order(s, pid, inst.id, "SELL", 100, D("2026-10-15"))
    sell_fill = _fill(
        s,
        sell_order,
        D("2026-10-15"),
        sell_price,
        100,
        calc_commission(sell_price * 100, cfg.costs),
    )
    svc.apply_fill(sell_fill.id)

    sell_entry = next(e for e in _entries(s, pid) if e.type == "SELL")
    assert sell_entry.available_from == D("2026-10-19")  # czw + 2 sesje (pią, pon)

    before = svc.cash_available(pid, D("2026-10-16"))  # piątek — wpływy jeszcze nie
    after = svc.cash_available(pid, D("2026-10-19"))  # poniedziałek — już tak
    assert after - before == sell_price * 100 - calc_commission(sell_price * 100, cfg.costs)


def test_settle_instantly_odrazu(tmp_db):
    engine, factory = tmp_db
    cfg = AppConfig(
        paths=_paths(Path(engine.url.database or ".")), market=MarketCfg(settle_instantly=True)
    )
    clock = FakeClock(dt.datetime(2026, 10, 15, 8, 0, tzinfo=dt.UTC))
    calendar = GpwCalendar(cfg, override_path=Path("nieistniejacy_override.yaml"))
    s: Session = factory()
    svc = PortfolioService(s, clock, calendar, cfg)

    from gpwbot.config import PortfolioDef

    pid = svc.create_portfolio(PortfolioDef(id="p1", strategy="baseline"))
    inst = _instrument(s, "AAA")
    buy_order = _order(s, pid, inst.id, "BUY", 100, D("2026-10-14"))
    buy_fill = _fill(
        s,
        buy_order,
        D("2026-10-14"),
        Decimal("10.00"),
        100,
        calc_commission(Decimal("1000"), cfg.costs),
    )
    svc.apply_fill(buy_fill.id)
    sell_price = Decimal("14.00")
    sell_order = _order(s, pid, inst.id, "SELL", 100, D("2026-10-15"))
    sell_fill = _fill(
        s,
        sell_order,
        D("2026-10-15"),
        sell_price,
        100,
        calc_commission(sell_price * 100, cfg.costs),
    )
    svc.apply_fill(sell_fill.id)

    sell_entry = next(e for e in _entries(s, pid) if e.type == "SELL")
    assert sell_entry.available_from == D("2026-10-15")  # od razu w dniu sesji


def test_rozliczenie_przesuwa_sie_o_wakacje(tmp_db):
    """Święto (nadpisanie kalendarza) przesuuje termin rozliczenia o kolejne sesje."""
    engine, factory = tmp_db
    override = Path(engine.url.database or ".").parent / "override.yaml"
    override.write_text("holidays:\n  - '2026-10-23'\nsessions: []\n", encoding="utf-8")
    cfg = AppConfig(paths=_paths(override.parent))
    clock = FakeClock(dt.datetime(2026, 10, 22, 8, 0, tzinfo=dt.UTC))
    calendar = GpwCalendar(cfg, override_path=override)
    s: Session = factory()
    svc = PortfolioService(s, clock, calendar, cfg)

    from gpwbot.config import PortfolioDef

    pid = svc.create_portfolio(PortfolioDef(id="p1", strategy="baseline"))
    inst = _instrument(s, "AAA")
    buy_order = _order(s, pid, inst.id, "BUY", 100, D("2026-10-21"))
    buy_fill = _fill(
        s,
        buy_order,
        D("2026-10-21"),
        Decimal("10.00"),
        100,
        calc_commission(Decimal("1000"), cfg.costs),
    )
    svc.apply_fill(buy_fill.id)
    sell_price = Decimal("14.00")
    sell_order = _order(s, pid, inst.id, "SELL", 100, D("2026-10-22"))
    sell_fill = _fill(
        s,
        sell_order,
        D("2026-10-22"),
        sell_price,
        100,
        calc_commission(sell_price * 100, cfg.costs),
    )
    svc.apply_fill(sell_fill.id)

    # czw 22.10 + 2 sesje: piątek 23.10 święto → pon 26.10 (+1), wt 27.10 (+2)
    sell_entry = next(e for e in _entries(s, pid) if e.type == "SELL")
    assert sell_entry.available_from == D("2026-10-27")


# --- dywidendy ------------------------------------------------------------------------


def test_dywidenda_netto_i_pay_date(env):
    """Netto = brutto × (1 − 19%); wpływ od pay_date; pomija pozycje kupione w dniu ex."""
    svc, s, cfg = env["svc"], env["s"], env["cfg"]
    from gpwbot.config import PortfolioDef

    pid = svc.create_portfolio(PortfolioDef(id="p1", strategy="baseline"))
    inst_aaa = _instrument(s, "AAA")  # kupiona przed ex_date → dywidenda tak
    inst_bbb = _instrument(s, "BBB")  # kupiona w dniu ex_date → dywidenda nie

    def buy(inst: Instrument, date: dt.date) -> None:
        order = _order(s, pid, inst.id, "BUY", 100, date)
        fill = _fill(
            s, order, date, Decimal("10.00"), 100, calc_commission(Decimal("1000"), cfg.costs)
        )
        svc.apply_fill(fill.id)

    buy(inst_aaa, D("2026-10-14"))
    buy(inst_bbb, D("2026-10-15"))  # = ex_date: nie posiadana na koniec poprzedniej sesji

    s.add(
        CorporateAction(
            instrument_id=inst_aaa.id,
            type="DIVIDEND",
            ex_date=D("2026-10-15"),
            pay_date=D("2026-11-02"),
            value=Decimal("2.00"),
        )
    )
    s.flush()

    applied = svc.apply_corporate_actions(pid, D("2026-11-03"))
    assert applied == 1
    divs = [e for e in _entries(s, pid) if e.type == "DIVIDEND"]
    assert len(divs) == 1
    # netto na akcję = 2.00 × (1 − 0.19) = 1.62; × 100 akcji
    assert divs[0].amount == Decimal("162.00")
    assert divs[0].available_from == D("2026-11-02")

    before_pay = svc.cash_available(pid, D("2026-11-01"))
    on_pay = svc.cash_available(pid, D("2026-11-02"))
    assert on_pay - before_pay == Decimal("162.00")


def test_dywidenda_brak_podwojonego_księgowania(env):
    """Powtórne uruchomienie nie księguje dywidendy drugi raz (idempotencja po ref)."""
    svc, s, cfg = env["svc"], env["s"], env["cfg"]
    from gpwbot.config import PortfolioDef

    pid = svc.create_portfolio(PortfolioDef(id="p1", strategy="baseline"))
    inst = _instrument(s, "AAA")
    order = _order(s, pid, inst.id, "BUY", 100, D("2026-10-14"))
    fill = _fill(
        s,
        order,
        D("2026-10-14"),
        Decimal("10.00"),
        100,
        calc_commission(Decimal("1000"), cfg.costs),
    )
    svc.apply_fill(fill.id)
    s.add(
        CorporateAction(
            instrument_id=inst.id,
            type="DIVIDEND",
            ex_date=D("2026-10-15"),
            pay_date=D("2026-11-02"),
            value=Decimal("2.00"),
        )
    )
    s.flush()

    assert svc.apply_corporate_actions(pid, D("2026-11-03")) == 1
    assert svc.apply_corporate_actions(pid, D("2026-11-04")) == 0
    divs = [e for e in _entries(s, pid) if e.type == "DIVIDEND"]
    assert len(divs) == 1


# --- splity ------------------------------------------------------------------------------


def test_split_2do1(env):
    """Split 2:1 → ilość ×2, koszt jednostkowy ÷2; wartość pozycji bez zmian."""
    svc, s, cfg = env["svc"], env["s"], env["cfg"]
    from gpwbot.config import PortfolioDef

    pid = svc.create_portfolio(PortfolioDef(id="p1", strategy="baseline"))
    inst = _instrument(s, "AAA")
    order = _order(s, pid, inst.id, "BUY", 100, D("2026-10-14"))
    fill = _fill(
        s,
        order,
        D("2026-10-14"),
        Decimal("10.00"),
        100,
        calc_commission(Decimal("1000"), cfg.costs),
    )
    svc.apply_fill(fill.id)

    before = svc.positions(pid, {"AAA": Decimal("10.05")})[0]
    assert (before.qty, str(before.avg_cost)) == (100, "10.05")

    s.add(
        CorporateAction(
            instrument_id=inst.id,
            type="SPLIT",
            ex_date=D("2026-10-16"),
            pay_date=None,
            value=Decimal("2"),
        )
    )
    s.flush()
    assert svc.apply_corporate_actions(pid, D("2026-10-16")) == 1

    after = svc.positions(pid, {"AAA": Decimal("5.025")})[0]
    assert after.qty == 200
    assert str(after.avg_cost) == "5.025"
    # wartość bez zmian: 100 × 10.05 == 200 × 5.025
    assert before.market_value == after.market_value

    marker = [e for e in _entries(s, pid) if e.type == "ADJUSTMENT"]
    assert len(marker) == 1 and marker[0].amount == Decimal("0")

    # idempotencja: drugi przebieg nie mnoży ponownie
    assert svc.apply_corporate_actions(pid, D("2026-10-20")) == 0
    again = svc.positions(pid, {"AAA": Decimal("5.025")})[0]
    assert (again.qty, str(again.avg_cost)) == (200, "5.025")


def test_split_ulamkowe_akcje_rzuca_ledger_error(env):
    """Split, który daje ułamkową liczbę akcji, musi rzucić LedgerError."""
    svc, s = env["svc"], env["s"]
    from gpwbot.config import PortfolioDef

    pid = svc.create_portfolio(PortfolioDef(id="p1", strategy="baseline"))
    inst = _instrument(s, "AAA")
    order = _order(s, pid, inst.id, "BUY", 100, D("2026-10-14"))
    fill = _fill(s, order, D("2026-10-14"), Decimal("10.00"), 100, Decimal("5.00"))
    svc.apply_fill(fill.id)

    # Split 1.5:1 dla 100 akcji = 150 (OK), ale dla 101 akcji = 151.5 (Błąd)
    # Zmienimy ilość w partii bezpośrednio, by wymusić błąd
    lot = s.query(TaxLot).one()
    lot.qty_remaining = 101
    s.flush()

    s.add(
        CorporateAction(
            instrument_id=inst.id,
            type="SPLIT",
            ex_date=D("2026-10-16"),
            pay_date=None,
            value=Decimal("1.5"),
        )
    )
    s.flush()

    with pytest.raises(LedgerError, match="dałaby ułamkową liczbę akcji"):
        svc.apply_corporate_actions(pid, D("2026-10-16"))


# --- pozycje, equity, snapshoty -------------------------------------------------------------


def test_positions_pola_i_held_sessions(env):
    svc, s, cfg = env["svc"], env["s"], env["cfg"]
    from gpwbot.config import PortfolioDef

    pid = svc.create_portfolio(PortfolioDef(id="p1", strategy="baseline"))
    inst = _instrument(s, "AAA")
    order = _order(s, pid, inst.id, "BUY", 100, D("2026-10-14"))
    fill = _fill(
        s,
        order,
        D("2026-10-14"),
        Decimal("10.00"),
        100,
        calc_commission(Decimal("1000"), cfg.costs),
    )
    svc.apply_fill(fill.id)

    # zegar: środa 2026-10-14 (Warszawa) → jedna sesja od otwarcia pozycji (czw 14.10? nie —
    # kupno 14.10, dziś 14.10 → 0; przesuwamy zegar na piątek 15.10 → 1 sesja)
    env["clock"].set(dt.datetime(2026, 10, 15, 9, 0, tzinfo=dt.UTC))
    pos = svc.positions(pid, {"AAA": Decimal("12.00")})[0]
    assert pos.ticker == "AAA" and pos.qty == 100
    assert str(pos.avg_cost) == "10.05"
    assert pos.market_value == Decimal("1200.00")
    assert pos.unrealized_pnl == Decimal("1200.00") - Decimal("1005.00")
    assert pos.held_sessions == 1
    assert pos.stop_price is None

    with pytest.raises(ValueError):
        svc.positions(pid, {})


def test_equity_snapshot_peak(env):
    svc, s, cfg = env["svc"], env["s"], env["cfg"]
    from gpwbot.config import PortfolioDef

    pid = svc.create_portfolio(PortfolioDef(id="p1", strategy="baseline"))
    inst = _instrument(s, "AAA")
    order = _order(s, pid, inst.id, "BUY", 100, D("2026-10-14"))
    fill = _fill(
        s,
        order,
        D("2026-10-14"),
        Decimal("10.00"),
        100,
        calc_commission(Decimal("1000"), cfg.costs),
    )
    svc.apply_fill(fill.id)

    prices_low = {"AAA": Decimal("9.00")}
    prices_high = {"AAA": Decimal("15.00")}
    assert svc.equity(pid, prices_low) == svc.cash_total(pid) + Decimal("900")

    assert svc.peak_equity(pid) is None  # jeszcze brak snapshotów
    svc.snapshot(pid, D("2026-10-15"), prices_low)
    row = s.get(PortfolioSnapshot, (pid, D("2026-10-15")))
    assert row.cash == svc.cash_total(pid)
    assert row.positions_json["AAA"]["qty"] == 100

    svc.snapshot(pid, D("2026-10-16"), prices_high)
    assert svc.peak_equity(pid) == svc.equity(pid, prices_high)


def test_izolacja_portfeli(env):
    """Dwa portfele: pozycje i gotówka jednego nie widać w drugim."""
    svc, s, cfg = env["svc"], env["s"], env["cfg"]
    from gpwbot.config import PortfolioDef

    pid_a = svc.create_portfolio(PortfolioDef(id="pa", strategy="baseline"))
    pid_b = svc.create_portfolio(PortfolioDef(id="pb", strategy="llm"))
    inst = _instrument(s, "AAA")
    order = _order(s, pid_a, inst.id, "BUY", 100, D("2026-10-14"))
    fill = _fill(
        s,
        order,
        D("2026-10-14"),
        Decimal("10.00"),
        100,
        calc_commission(Decimal("1000"), cfg.costs),
    )
    svc.apply_fill(fill.id)

    assert svc.positions(pid_b, {"AAA": Decimal("10.00")}) == []
    assert svc.cash_total(pid_b) == Decimal("100000")
    assert len(svc.positions(pid_a, {"AAA": Decimal("10.00")})) == 1
    svc.check_invariants(pid_a)
    svc.check_invariants(pid_b)
