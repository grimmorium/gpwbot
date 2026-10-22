"""Testy SimBroker (S07): submit, process_session i scenariusz demonstracyjny.

Kalendarz: czwartek 2026-10-15 (zegar domyślnie 8:15 UTC = 10:15 CEST — w trakcie sesji),
piątek 2026-10-16, poniedziałek 2026-10-19. Prowizja 0.30%/min 5 PLN; tick 0.10 (jedno pasmo).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from gpwbot.calendar import GpwCalendar
from gpwbot.clock import FakeClock
from gpwbot.config import AppConfig, MarketCfg, PathsCfg, PortfolioDef
from gpwbot.costs import commission as calc_commission
from gpwbot.db.models import CashLedgerEntry, Fill, Instrument, Order, PricesDaily, TaxLot
from gpwbot.domain import OrderIntent, Side
from gpwbot.execution.orders import OrderReason, OrderStatus
from gpwbot.execution.sim_broker import SimBroker
from gpwbot.portfolio.service import PortfolioService
from gpwbot.ticks import TickBand, TickTable

D = Decimal
DATE = dt.date.fromisoformat
TICKS = TickTable({"default": [TickBand(None, D("0.10"))]})


def _paths(tmp_path: Path) -> PathsCfg:
    return PathsCfg(
        db_path=tmp_path / "t.db",
        data_dir=tmp_path / "data",
        raw_dir=tmp_path / "raw",
        reports_dir=tmp_path / "reports",
    )


def _make_env(tmp_db, settle_instantly: bool = False) -> dict[str, object]:
    engine, factory = tmp_db
    market = MarketCfg(settle_instantly=settle_instantly) if settle_instantly else MarketCfg()
    cfg = AppConfig(paths=_paths(Path(engine.url.database or ".")), market=market)
    clock = FakeClock(dt.datetime(2026, 10, 15, 8, 15, tzinfo=dt.UTC))
    calendar = GpwCalendar(cfg, override_path=Path("nieistniejacy_override.yaml"))
    session: Session = factory()
    svc = PortfolioService(session, clock, calendar, cfg)
    broker = SimBroker(session, svc, calendar, TICKS, cfg, clock)
    pid = svc.create_portfolio(PortfolioDef(id="p1", strategy="baseline"))
    return {
        "cfg": cfg,
        "clock": clock,
        "cal": calendar,
        "s": session,
        "svc": svc,
        "broker": broker,
        "pid": pid,
    }


@pytest.fixture
def env(tmp_db) -> dict[str, object]:
    """Środowisko domyślne: T+2, zegar w trakcie czwartkowej sesji."""
    return _make_env(tmp_db)


def _inst(s: Session, ticker: str) -> Instrument:
    inst = Instrument(ticker=ticker)
    s.add(inst)
    s.flush()
    return inst


def _bar_row(
    s: Session, inst_id: int, date: dt.date, open_: Decimal, volume: int | None = None
) -> None:
    row = PricesDaily(
        instrument_id=inst_id,
        date=date,
        source="stooq",
        open=open_,
        high=open_,
        low=open_,
        close=open_,
        volume=volume,
        ingested_at=dt.datetime(2026, 10, 15, tzinfo=dt.UTC),
    )
    s.add(row)
    s.flush()


def _direct_buy(
    env: dict[str, object], inst_id: int, date: dt.date, price: Decimal, qty: int
) -> None:
    """Kupno poza symulatorem (wzorzec testów S06): Order + Fill + apply_fill."""
    svc, s, pid = env["svc"], env["s"], env["pid"]  # type: ignore[arg-type]
    cfg = env["cfg"]
    order = Order(
        portfolio_id=pid,
        instrument_id=inst_id,
        side="BUY",
        qty=qty,
        order_type="limit",
        limit_price=None,
        status="filled",
        created_at=env["clock"].now(),  # type: ignore[union-attr]
        valid_from_session=date,
    )
    s.add(order)
    s.flush()
    fill = Fill(
        order_id=order.id,
        session_date=date,
        price=price,
        qty=qty,
        commission=calc_commission(price * qty, cfg.costs),  # type: ignore[union-attr]
    )
    s.add(fill)
    s.flush()
    svc.apply_fill(fill.id)


# --- submit ---------------------------------------------------------------------


def test_submit_buy_rezerwacja_gotówki(env):
    broker, svc, s, pid = env["broker"], env["svc"], env["s"], env["pid"]  # type: ignore[assignment]
    _inst(s, "AAA")

    ids = broker.submit(
        pid, None, [OrderIntent("AAA", Side.BUY, 100, D("10.00"), reason="teza testowa")]
    )

    order = s.get(Order, ids[0])
    assert order is not None
    assert order.status == OrderStatus.PENDING.value
    assert order.order_type == "limit_at_open"
    # czwartek w trakcie sesji → pierwsza nadchodząca sesja to piątek 16.10
    assert order.valid_from_session == DATE("2026-10-16")
    # rezerwacja = 100 × 10.00 + prowizja(1000) = 1000 + 5.00 (minimalna, bo 3.00 < 5)
    assert order.reserved_amount == D("1005.00")
    assert svc.cash_available(pid, DATE("2026-10-16")) == D("98995.00")
    assert order.reason == "teza testowa"


def test_submit_buy_no_cash(env):
    broker, svc, s, pid = env["broker"], env["svc"], env["s"], env["pid"]  # type: ignore[assignment]
    _inst(s, "AAA")

    ids = broker.submit(pid, None, [OrderIntent("AAA", Side.BUY, 20_000, D("10.00"))])

    order = s.get(Order, ids[0])
    # rezerwacja 200 000 + prowizja 600 > 100 000 → REJECTED/NO_CASH (wiersz zostaje w audycie)
    assert order is not None and order.status == OrderStatus.REJECTED.value
    assert order.reason == OrderReason.NO_CASH.value
    assert order.reserved_amount is None
    assert svc.cash_available(pid, DATE("2026-10-16")) == D("100000")


def test_submit_sell_ponad_stan(env):
    broker, s, pid = env["broker"], env["s"], env["pid"]  # type: ignore[assignment]
    inst = _inst(s, "AAA")

    ids = broker.submit(pid, None, [OrderIntent("AAA", Side.SELL, 10, D("10.00"))])
    order = s.get(Order, ids[0])
    assert order is not None and order.status == OrderStatus.REJECTED.value
    assert order.reason == OrderReason.NO_POSITION.value

    _direct_buy(env, inst.id, DATE("2026-10-14"), D("9.50"), 100)
    ids = broker.submit(pid, None, [OrderIntent("AAA", Side.SELL, 70, D("10.00"))])
    first = s.get(Order, ids[0])
    assert first is not None and first.status == OrderStatus.PENDING.value
    assert first.reserved_amount is None  # SELL nie rezerwuje gotówki

    ids = broker.submit(pid, None, [OrderIntent("AAA", Side.SELL, 30, D("10.00"))])
    second = s.get(Order, ids[0])
    assert second is not None and second.status == OrderStatus.PENDING.value  # granica: 100 − 70

    ids = broker.submit(pid, None, [OrderIntent("AAA", Side.SELL, 31, D("10.00"))])
    third = s.get(Order, ids[0])
    assert third is not None and third.status == OrderStatus.REJECTED.value
    assert third.reason == OrderReason.NO_POSITION.value


@pytest.mark.parametrize(
    ("ts", "expected"),
    [
        # czwartek 6:30 UTC = 8:30 CEST — przed otwarciem (9:00) → bieżąca sesja
        (dt.datetime(2026, 10, 15, 6, 30, tzinfo=dt.UTC), DATE("2026-10-15")),
        # czwartek w trakcie sesji → piątek
        (dt.datetime(2026, 10, 15, 8, 15, tzinfo=dt.UTC), DATE("2026-10-16")),
        # piątek wieczorem (17:30 CEST — po aukcji zamknięcia) → poniedziałek
        (dt.datetime(2026, 10, 16, 15, 30, tzinfo=dt.UTC), DATE("2026-10-19")),
    ],
    ids=["przed_otwarcie", "w_trakcie_sesji", "piątek_wieczorem"],
)
def test_submit_valid_from_session(env, ts: dt.datetime, expected: dt.date):
    broker, s, pid = env["broker"], env["s"], env["pid"]  # type: ignore[assignment]
    clock = env["clock"]
    _inst(s, "AAA")
    clock.set(ts)

    ids = broker.submit(pid, None, [OrderIntent("AAA", Side.BUY, 10, D("5.00"))])

    order = s.get(Order, ids[0])
    assert order is not None and order.valid_from_session == expected


def test_submit_nieznany_ticker_rzuca_value_error(env):
    broker, pid = env["broker"], env["pid"]  # type: ignore[assignment]
    with pytest.raises(ValueError, match="nieznany ticker"):
        broker.submit(pid, None, [OrderIntent("ZZZ", Side.BUY, 10, D("5.00"))])


@pytest.mark.parametrize(
    "intent",
    [
        OrderIntent("AAA", Side.BUY, 10, limit_price=None),
        OrderIntent("AAA", Side.SELL, 10, limit_price=D("0")),
        OrderIntent("AAA", Side.BUY, 0, D("5.00")),
    ],
    ids=["brak_limitu", "limit_zero", "qty_zero"],
)
def test_submit_nieważne_zlecenie_rzuca_value_error(env, intent: OrderIntent):
    broker, s, pid = env["broker"], env["s"], env["pid"]  # type: ignore[assignment]
    _inst(s, "AAA")
    with pytest.raises(ValueError):
        broker.submit(pid, None, [intent])


# --- process_session --------------------------------------------------------------


def test_process_sell_przed_buy(env):
    """SELL przetwarzany przed BUY mimo większego id (kolejność wg specyfikacji)."""
    broker, svc, s, pid = env["broker"], env["svc"], env["s"], env["pid"]  # type: ignore[assignment]
    inst_aaa = _inst(s, "AAA")
    inst_bbb = _inst(s, "BBB")
    inst_ccc = _inst(s, "CCC")
    _direct_buy(env, inst_aaa.id, DATE("2026-10-14"), D("9.50"), 100)
    _direct_buy(env, inst_bbb.id, DATE("2026-10-14"), D("9.50"), 100)

    ids = broker.submit(
        pid,
        None,
        [
            OrderIntent("CCC", Side.BUY, 10, D("5.00")),
            OrderIntent("AAA", Side.SELL, 100, D("9.40")),
        ],
    )
    buy_id, sell_id = ids[0], ids[1]
    assert buy_id < sell_id

    _bar_row(s, inst_ccc.id, DATE("2026-10-16"), D("5.00"))
    _bar_row(s, inst_aaa.id, DATE("2026-10-16"), D("9.50"))

    report = broker.process_session(pid, DATE("2026-10-16"))

    assert report.filled_order_ids == (sell_id, buy_id)  # SELL najpierw
    assert report.expired_orders == ()
    # SELL: open 9.50 ≥ limit → cena po poślizgu/ticku = 9.40; wpływy T+2 → wtorek 20.10
    sell_entry = next(
        e for e in s.query(CashLedgerEntry).filter_by(portfolio_id=pid) if e.type == "SELL"
    )
    assert sell_entry.available_from == DATE("2026-10-20")
    # piątek: wpływy ze sprzedaży jeszcze niedostępne (T+2); kupno CCC już tak
    assert svc.cash_available(pid, DATE("2026-10-16")) == D("98035.00")


def test_process_settle_instantly_sprzedaz_finansuje_zakup(tmp_db):
    """Przy `settle_instantly` wpływy ze sprzedaży są dostępne od razu i finansują nowe kupno."""
    env = _make_env(tmp_db, settle_instantly=True)
    broker, svc, s, pid, clock = (  # type: ignore[assignment]
        env["broker"],
        env["svc"],
        env["s"],
        env["pid"],
        env["clock"],
    )
    inst_aaa = _inst(s, "AAA")
    _inst(s, "BBB")  # wiersz instrumentu potrzebny do submit po tickerze
    # środa: kupno 9500 @ 9.80 → koszt 93 100 + prowizja 279.30; gotówka 6 620.70
    _direct_buy(env, inst_aaa.id, DATE("2026-10-14"), D("9.80"), 9_500)

    ids = broker.submit(pid, None, [OrderIntent("AAA", Side.SELL, 9_500, D("9.90"))])
    _bar_row(s, inst_aaa.id, DATE("2026-10-16"), D("10.00"))

    report = broker.process_session(pid, DATE("2026-10-16"))

    assert report.filled_order_ids == (ids[0],)
    # fill po 9.90 (poślizg+tick w dół); wpływy od razu: 94 050 − prowizja 282.15 = +93 767.85
    assert svc.cash_available(pid, DATE("2026-10-16")) == D("100388.55")

    # piątek wieczorem: kupno finansowane właśnie tą sprzedażą (rezerwacja 100 259.88)
    clock.set(dt.datetime(2026, 10, 16, 15, 30, tzinfo=dt.UTC))
    ids2 = broker.submit(pid, None, [OrderIntent("BBB", Side.BUY, 9_800, D("10.20"))])
    order2 = s.get(Order, ids2[0])
    assert order2 is not None and order2.status == OrderStatus.PENDING.value
    assert order2.reserved_amount == D("100259.88")
    # 100 388.55 − rezerwacja 100 259.88 = 128.67
    assert svc.cash_available(pid, DATE("2026-10-19")) == D("128.67")


def test_process_t_plus_2_sprzedaz_jeszcze_nie_finansuje(tmp_db):
    """Kontrast: przy T+2 wpływy ze sprzedaży (wtorek) nie pokrywają rezerwacji na poniedziałek."""
    env = _make_env(tmp_db, settle_instantly=False)
    broker, svc, s, pid, clock = (  # type: ignore[assignment]
        env["broker"],
        env["svc"],
        env["s"],
        env["pid"],
        env["clock"],
    )
    inst_aaa = _inst(s, "AAA")
    _inst(s, "BBB")  # wiersz instrumentu potrzebny do submit po tickerze
    _direct_buy(env, inst_aaa.id, DATE("2026-10-14"), D("9.80"), 9_500)

    broker.submit(pid, None, [OrderIntent("AAA", Side.SELL, 9_500, D("9.90"))])
    _bar_row(s, inst_aaa.id, DATE("2026-10-16"), D("10.00"))

    broker.process_session(pid, DATE("2026-10-16"))

    assert svc.cash_available(pid, DATE("2026-10-16")) == D("6620.70")  # wpływy dopiero wtorek
    clock.set(dt.datetime(2026, 10, 16, 15, 30, tzinfo=dt.UTC))
    ids = broker.submit(pid, None, [OrderIntent("BBB", Side.BUY, 9_800, D("10.20"))])
    order = s.get(Order, ids[0])
    assert order is not None and order.status == OrderStatus.REJECTED.value
    assert order.reason == OrderReason.NO_CASH.value


def test_process_no_bar(env):
    broker, svc, s, pid = env["broker"], env["svc"], env["s"], env["pid"]  # type: ignore[assignment]
    _inst(s, "AAA")

    ids = broker.submit(pid, None, [OrderIntent("AAA", Side.BUY, 10, D("5.00"))])

    report = broker.process_session(pid, DATE("2026-10-16"))  # brak świecy na piątek

    assert report.filled_order_ids == ()
    assert report.expired_orders == ((ids[0], OrderReason.NO_BAR.value),)
    order = s.get(Order, ids[0])
    assert order is not None and order.status == OrderStatus.EXPIRED.value
    assert order.reason == OrderReason.NO_BAR.value
    assert order.processed_session == DATE("2026-10-16")
    assert order.reserved_amount is None  # rezerwacja zwolniona
    assert svc.cash_available(pid, DATE("2026-10-19")) == D("100000")


def test_process_wygasanie_limit_wolumenu(env):
    broker, s, pid = env["broker"], env["s"], env["pid"]  # type: ignore[assignment]
    inst = _inst(s, "AAA")

    ids = broker.submit(pid, None, [OrderIntent("AAA", Side.BUY, 1_001, D("5.00"))])
    _bar_row(s, inst.id, DATE("2026-10-16"), D("4.90"), volume=20_000)

    report = broker.process_session(
        pid, DATE("2026-10-16")
    )  # limit wolumenu: 5% × 20 000 = 1000 < 1001

    assert report.expired_orders == ((ids[0], OrderReason.VOLUME_LIMIT.value),)
    order = s.get(Order, ids[0])
    assert order is not None and order.status == OrderStatus.EXPIRED.value
    assert order.reserved_amount is None


def test_process_idempotentne(env):
    broker, svc, s, pid = env["broker"], env["svc"], env["s"], env["pid"]  # type: ignore[assignment]
    inst = _inst(s, "AAA")

    ids = broker.submit(pid, None, [OrderIntent("AAA", Side.BUY, 10, D("5.00"))])
    _bar_row(s, inst.id, DATE("2026-10-16"), D("5.00"))

    first = broker.process_session(pid, DATE("2026-10-16"))
    assert first.filled_order_ids == (ids[0],)
    fills_after_first = len(s.query(Fill).filter_by(order_id=ids[0]).all())
    cash_after_first = svc.cash_total(pid)

    second = broker.process_session(pid, DATE("2026-10-16"))  # ponowne wywołanie — no-op

    assert second.filled_order_ids == () and second.expired_orders == ()
    assert len(s.query(Fill).filter_by(order_id=ids[0]).all()) == fills_after_first == 1
    assert svc.cash_total(pid) == cash_after_first


def test_process_zlecenie_przyszlej_sesji_czeka(env):
    """Zlecenie z `valid_from` w przyszłości nie jest brane pod uwagę wcześniej."""
    broker, svc, s, pid, clock = (  # type: ignore[assignment]
        env["broker"],
        env["svc"],
        env["s"],
        env["pid"],
        env["clock"],
    )
    inst = _inst(s, "AAA")
    clock.set(dt.datetime(2026, 10, 16, 15, 30, tzinfo=dt.UTC))  # piątek wieczorem → poniedziałek

    ids = broker.submit(pid, None, [OrderIntent("AAA", Side.BUY, 10, D("5.00"))])
    _bar_row(s, inst.id, DATE("2026-10-16"), D("4.80"))

    friday = broker.process_session(pid, DATE("2026-10-16"))
    assert friday.filled_order_ids == () and friday.expired_orders == ()  # jeszcze nie jego sesja
    order = s.get(Order, ids[0])
    assert order is not None and order.status == OrderStatus.PENDING.value

    _bar_row(s, inst.id, DATE("2026-10-19"), D("4.80"))
    monday = broker.process_session(pid, DATE("2026-10-19"))
    assert monday.filled_order_ids == (ids[0],)  # fill po 4.90 (tick UP), prowizja minimalna
    assert svc.cash_total(pid) == D("99946")


# --- scenariusz demonstracyjny (akceptacja S07): kupno, wygaśnięcie, sprzedaż -------


def test_scenariusz_demonstracyjny_kupno_wygasniecie_sprzedaz(env):
    broker, svc, s, pid, clock = (  # type: ignore[assignment]
        env["broker"],
        env["svc"],
        env["s"],
        env["pid"],
        env["clock"],
    )
    inst_aaa = _inst(s, "AAA")
    inst_bbb = _inst(s, "BBB")

    # czwartek w trakcie sesji: dwa kupna na piątek (oba rezerwują po 955.00)
    ids = broker.submit(
        pid,
        None,
        [
            OrderIntent("AAA", Side.BUY, 100, D("9.50")),
            OrderIntent("BBB", Side.BUY, 100, D("9.50")),
        ],
    )
    aaa_id, bbb_id = ids[0], ids[1]

    # piątek: AAA otwiera się pod limitem (fill), BBB nad limitem (wygaśnięcie)
    _bar_row(s, inst_aaa.id, DATE("2026-10-16"), D("9.40"))
    _bar_row(s, inst_bbb.id, DATE("2026-10-16"), D("9.60"))

    r1 = broker.process_session(pid, DATE("2026-10-16"))
    assert r1.filled_order_ids == (aaa_id,)
    assert r1.expired_orders == ((bbb_id, OrderReason.LIMIT_NOT_MET.value),)
    # AAA: poślizg+tick daje 9.50 = limit; koszt 950 + prowizja minimalna 5 → gotówka 99 045
    assert svc.cash_total(pid) == D("99045")
    aaa_order, bbb_order = s.get(Order, aaa_id), s.get(Order, bbb_id)
    assert aaa_order is not None and aaa_order.status == OrderStatus.FILLED.value
    assert aaa_order.reserved_amount is None  # zwolniona przez apply_fill
    assert bbb_order is not None and bbb_order.status == OrderStatus.EXPIRED.value
    assert bbb_order.reason == OrderReason.LIMIT_NOT_MET.value
    assert bbb_order.reserved_amount is None  # zwolniona przy wygaśnięciu

    # piątek wieczorem: sprzedaż AAA na poniedziałek (limit 9.30)
    clock.set(dt.datetime(2026, 10, 16, 15, 30, tzinfo=dt.UTC))
    ids2 = broker.submit(pid, None, [OrderIntent("AAA", Side.SELL, 100, D("9.30"))])
    _bar_row(s, inst_aaa.id, DATE("2026-10-19"), D("9.40"))

    r2 = broker.process_session(pid, DATE("2026-10-19"))
    assert r2.filled_order_ids == (ids2[0],) and r2.expired_orders == ()
    # fill po 9.30; wpływy T+2 od środy 21.10; gotówka = 99 045 + 930 − prowizja 5
    assert svc.cash_total(pid) == D("99970")
    sell_entry = next(
        e for e in s.query(CashLedgerEntry).filter_by(portfolio_id=pid) if e.type == "SELL"
    )
    assert sell_entry.available_from == DATE("2026-10-21")

    # pozycja zamknięta (partia FIFO zużyta w całości — usunięta); realized P/L = 930 − 955 − 5
    open_lots = [
        lot for lot in s.query(TaxLot).filter_by(portfolio_id=pid).all() if lot.qty_remaining > 0
    ]
    assert open_lots == []
    sell_fill = s.get(Fill, next(f.id for f in s.query(Fill).filter_by(order_id=ids2[0]).all()))
    assert sell_fill is not None and sell_fill.realized_pnl == D("-30.00")

    svc.check_invariants(pid)  # audyt: niezmienniki ledgeru po całym scenariuszu
