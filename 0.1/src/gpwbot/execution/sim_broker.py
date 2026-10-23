"""Symulator wykonania zleceń (S07): `SimBroker` — składanie i realizacja w sesjach.

Zlecenia są ważne jeden dzień: PENDING od `valid_from_session`, a w pierwszej sesji,
w której je przetworzono (`processed_session IS NULL`), albo się realizują, albo
wygasają (Część I sekcja 12). Metody flushują bez commitu — transakcję prowadzi
wywołujący (A3: I/O oddzielone od logiki; czysty fill model w `fill_model`).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

import structlog
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from gpwbot.calendar import GpwCalendar
from gpwbot.clock import Clock
from gpwbot.config import AppConfig
from gpwbot.costs import commission as calc_commission
from gpwbot.db.models import Fill, Instrument, Order, PricesDaily, TaxLot
from gpwbot.db.repo import PriceBar
from gpwbot.domain import OrderIntent, Side
from gpwbot.execution.fill_model import rejection_reason, simulate_fill
from gpwbot.execution.orders import OrderReason, OrderStatus, OrderType
from gpwbot.portfolio.service import PortfolioService
from gpwbot.ticks import TickTable

log = structlog.get_logger().bind(component="sim_broker")


@dataclass(frozen=True)
class SessionExecutionReport:
    """Wynik przetworzenia jednej sesji przez `SimBroker.process_session`."""

    session_date: dt.date
    filled_order_ids: tuple[int, ...] = ()
    expired_orders: tuple[tuple[int, str], ...] = ()  # (order_id, kod powodu)


def _precheck_reason(order: Order, bar: PriceBar | None) -> str | None:
    """Powód wygaśnięcia przed symulacją filla albo `None`, gdy można symulować.

    Brak świecy → NO_BAR; wiersz niezgodny z LIMIT_AT_OPEN (strona/typ/limit), świeca z flagą
    jakości, cena otwarcia ≤ 0 lub ujemny wolumen → BAD_DATA — filla nie da się policzyć, a
    wygaszenie jest bezpieczniejsze niż przerwanie sesji w połowie pętli (D-049).
    """
    if bar is None:
        return OrderReason.NO_BAR.value
    malformed = order.side not in (Side.BUY.name, Side.SELL.name) or (
        order.order_type != OrderType.LIMIT_AT_OPEN.value or order.limit_price is None
    )
    bad_bar = (
        bar.quality_flag is not None or bar.open <= 0 or (bar.volume is not None and bar.volume < 0)
    )
    if malformed or bad_bar:
        return OrderReason.BAD_DATA.value
    return None


class SimBroker:
    """Składa i realizuje zlecenia w symulowanych sesjach GPW.

    `submit` tworzy zlecenia PENDING (BUY rezerwują gotówkę), a `process_session`
    rozlicza je na otwarciu pierwszej nadchodzącej sesji — najpierw SELL, potem BUY,
    w obrębie typu rosnąco po `id`. Metody flushują bez commitu.
    """

    def __init__(
        self,
        session: Session,
        portfolio_service: PortfolioService,
        calendar: GpwCalendar,
        tick_table: TickTable,
        cfg: AppConfig,
        clock: Clock,
    ) -> None:
        self._s = session
        self._svc = portfolio_service
        self._cal = calendar
        self._ticks = tick_table
        self._cfg = cfg
        self._clock = clock

    # --- składanie -------------------------------------------------------------

    def submit(
        self, portfolio_id: int, run_id: int | None, intents: list[OrderIntent]
    ) -> list[int]:
        """Tworzy zlecenia PENDING; zwraca id wszystkich utworzonych wierszy (kolejność wejścia).

        `valid_from_session = calendar.next_session_after(clock.now())` — wspólne dla całej
        partii. BUY rezerwuje `qty × limit + prowizja`; przy braku środków wiersz trafia do
        bazy ze statusem REJECTED i powodem `NO_CASH`. SELL ponad stan (posiadane minus już
        zlecone do sprzedaży) → REJECTED/`NO_POSITION`. Nieznany ticker lub brak limitu →
        `ValueError` (LIMIT_AT_OPEN to jedyny typ; D-042).
        """
        if not intents:
            return []
        valid_from = self._cal.next_session_after(self._clock.now())
        held_total = dict(
            self._s.execute(
                select(TaxLot.instrument_id, func.coalesce(func.sum(TaxLot.qty_remaining), 0))
                .where(TaxLot.portfolio_id == portfolio_id)
                .group_by(TaxLot.instrument_id)
            ).all()
        )

        pending_sell_qty = dict(
            self._s.execute(
                select(Order.instrument_id, func.coalesce(func.sum(Order.qty), 0))
                .where(
                    Order.portfolio_id == portfolio_id,
                    Order.side == Side.SELL.name,
                    Order.status == OrderStatus.PENDING.value,
                )
                .group_by(Order.instrument_id)
            ).all()
        )

        now = self._clock.now()
        order_ids: list[int] = []
        for intent in intents:
            if intent.qty <= 0:
                raise ValueError(f"qty musi być dodatnie (otrzymano {intent.qty})")
            limit = intent.limit_price
            if limit is None or limit <= 0:
                msg = f"{intent.ticker}: zlecenie LIMIT_AT_OPEN wymaga dodatniego limitu"
                raise ValueError(msg)

            instrument = self._s.execute(
                select(Instrument).where(Instrument.ticker == intent.ticker)
            ).scalar_one_or_none()
            if instrument is None:
                raise ValueError(f"nieznany ticker: {intent.ticker}")

            order = Order(
                portfolio_id=portfolio_id,
                run_id=run_id,
                instrument_id=instrument.id,
                side=intent.side.name,
                qty=intent.qty,
                order_type=OrderType.LIMIT_AT_OPEN.value,
                limit_price=limit,
                status=OrderStatus.PENDING.value,
                created_at=now,
                valid_from_session=valid_from,
                reason=intent.reason or None,
            )

            if intent.side is Side.BUY:
                reservation = Decimal(intent.qty) * limit + calc_commission(
                    Decimal(intent.qty) * limit, self._cfg.costs
                )
                available = self._svc.cash_available(portfolio_id, valid_from)
                if reservation > available:
                    order.status = OrderStatus.REJECTED.value
                    order.reason = OrderReason.NO_CASH.value
                    log.warning(
                        "order_rejected",
                        ticker=intent.ticker,
                        reason=OrderReason.NO_CASH.value,
                        needed=str(reservation),
                        available=str(available),
                    )
                else:
                    self._s.add(order)
                    self._s.flush()
                    self._svc.reserve(portfolio_id, order.id, reservation)
            else:  # SELL
                sellable = held_total.get(instrument.id, 0) - pending_sell_qty.get(instrument.id, 0)
                if intent.qty > sellable:
                    order.status = OrderStatus.REJECTED.value
                    order.reason = OrderReason.NO_POSITION.value
                    log.warning(
                        "order_rejected",
                        ticker=intent.ticker,
                        reason=OrderReason.NO_POSITION.value,
                        qty=intent.qty,
                        sellable=sellable,
                    )
                else:
                    pending_sell_qty[instrument.id] = (
                        pending_sell_qty.get(instrument.id, 0) + intent.qty
                    )

            self._s.add(order)
            self._s.flush()
            order_ids.append(order.id)
        log.info(
            "orders_submitted",
            portfolio_id=portfolio_id,
            count=len(order_ids),
            valid_from=str(valid_from),
        )
        return order_ids

    # --- realizacja ------------------------------------------------------------

    def process_session(self, portfolio_id: int, session_date: dt.date) -> SessionExecutionReport:
        """Rozlicza zlecenia PENDING na otwarciu `session_date` (zlecenia ważne jeden dzień).

        Zbiór: `valid_from_session ≤ session_date` i `processed_session IS NULL`; kolejność:
        najpierw SELL, potem BUY, w obrębie typu rosnąco po `id`. Brak świecy → EXPIRED/
        `NO_BAR`; nieprawidłowe dane (flaga jakości, open ≤ 0, ujemny wolumen) lub wiersz
        niezgodny z LIMIT_AT_OPEN → EXPIRED/`BAD_DATA` (D-049); niespełniony limit lub limit
        wolumenu → EXPIRED z odpowiednim kodem.
        Po każdym zleceniu: `processed_session = session_date` i zwolnienie rezerwacji;
        dla filli dodatkowo `apply_fill`. Ponowne wywołanie dla tej samej sesji nic nie
        zmienia (wszystkie wiersze mają już `processed_session`). Flush bez commitu.
        """
        orders = list(
            self._s.execute(
                select(Order)
                .where(
                    Order.portfolio_id == portfolio_id,
                    Order.status == OrderStatus.PENDING.value,
                    Order.valid_from_session <= session_date,
                    Order.processed_session.is_(None),
                )
                # SELL przed BUY jawnie (nie przez porównanie stringów; przegląd C3)
                .order_by(case((Order.side == Side.SELL.name, 0), else_=1), Order.id.asc())
            ).scalars()
        )
        if not orders:
            return SessionExecutionReport(session_date=session_date)

        inst_ids = [o.instrument_id for o in orders]
        tickers = dict(
            self._s.execute(
                select(Instrument.id, Instrument.ticker).where(Instrument.id.in_(inst_ids))
            ).all()
        )
        bars: dict[int, PriceBar] = {}
        rows = self._s.execute(
            select(PricesDaily)
            .where(PricesDaily.instrument_id.in_(inst_ids), PricesDaily.date == session_date)
            .order_by(PricesDaily.source.asc())
        ).scalars()
        for row in rows:
            if row.instrument_id not in bars:  # deterministycznie: pierwszy source rosnąco
                bars[row.instrument_id] = PriceBar(
                    date=row.date,
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    adj_close=row.adj_close,
                    volume=row.volume,
                    turnover_pln=row.turnover_pln,
                    quality_flag=row.quality_flag,
                    adjusted=row.adjusted,
                )

        filled_ids: list[int] = []
        expired: list[tuple[int, str]] = []
        for order in orders:
            ticker = tickers.get(order.instrument_id)
            if ticker is None:
                msg = f"zlecenie {order.id}: instrument bez wiersza w `instruments`"
                raise ValueError(msg)
            bar = bars.get(order.instrument_id)
            reason_code: str | None = _precheck_reason(order, bar)
            fill_row: Fill | None = None
            if reason_code is None and bar is not None:
                intent = OrderIntent(
                    ticker=ticker,
                    side=Side(order.side),
                    qty=order.qty,
                    limit_price=order.limit_price,
                    reason=order.reason or "",
                )
                try:
                    result = simulate_fill(intent, bar, self._cfg.costs, self._ticks)
                except ValueError as exc:
                    # Resztkowe nieprawidłowe ceny (np. SELL z open poniżej ticka po poślizgu):
                    # wygaszenie zamiast przerwania sesji w połowie pętli (D-049).
                    reason_code = OrderReason.BAD_DATA.value
                    log.warning("order_bad_data", ticker=ticker, order_id=order.id, detail=str(exc))
                else:
                    if result is None:
                        reason_code = rejection_reason(intent, bar, self._cfg.costs)
                        assert reason_code is not None  # simulate_fill=None ⇒ powód istnieje
                    else:
                        fill_row = Fill(
                            order_id=order.id,
                            session_date=session_date,
                            price=result.price,
                            qty=result.qty,
                            commission=result.commission,
                            slippage_cost=result.slippage_cost,
                        )
                        self._s.add(fill_row)
                        self._s.flush()
                        self._svc.apply_fill(fill_row.id)  # księguje i zwalnia rezerwację

            if fill_row is not None:
                order.status = OrderStatus.FILLED.value
                filled_ids.append(order.id)
                log.info(
                    "order_filled",
                    ticker=ticker,
                    price=str(fill_row.price),
                    qty=fill_row.qty,
                )
            else:
                order.status = OrderStatus.EXPIRED.value
                order.reason = reason_code
                self._svc.release(order.id)  # idempotentne; zwalnia rezerwację BUY
                expired.append((order.id, reason_code or ""))
                log.info("order_expired", ticker=ticker, reason=reason_code)
            order.processed_session = session_date

        self._s.flush()
        return SessionExecutionReport(
            session_date=session_date,
            filled_order_ids=tuple(filled_ids),
            expired_orders=tuple(expired),
        )
