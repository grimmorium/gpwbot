"""Serwis księgowy portfela (S06): gotówka, rezerwacje, fills FIFO, dywidendy, splity.

Konwencje:
- metody flushują bez commitu — transakcję prowadzi wywołujący (`session_scope`);
- `cash_ledger.ts` to czas zapisu audytowego; data biznesowa wpływu = `available_from`;
- wartości jednostkowe (unit_cost, avg_cost) i `realized_pnl` trzymane są w pełnej
  precyzji Decimal — bez kwantyzacji do 0,01 — by tożsamość księgowa P/L zachodziła
  dokładnie (D-035); kwoty gotówkowe z natury mają 2 miejsca po przecinku.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from gpwbot.calendar import WARSAW, GpwCalendar
from gpwbot.clock import Clock
from gpwbot.config import AppConfig, PortfolioDef
from gpwbot.costs import net_dividend
from gpwbot.db.models import (
    CashLedgerEntry,
    CorporateAction,
    Fill,
    Instrument,
    Order,
    Portfolio,
    PortfolioSnapshot,
    PositionMeta,
    TaxLot,
)
from gpwbot.domain import Side
from gpwbot.errors import LedgerError
from gpwbot.portfolio.fifo import Lot, consume_lots

ZERO = Decimal("0")


@dataclass(frozen=True)
class Position:
    """Stan pozycji per ticker (wartości jednostkowe w pełnej precyzji — D-035)."""

    ticker: str
    qty: int
    avg_cost: Decimal
    last_price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal
    held_sessions: int
    stop_price: Decimal | None


class PortfolioService:
    """Księgowanie gotówki i pozycji jednego lub wielu portfeli (S06)."""

    def __init__(
        self, session: Session, clock: Clock, calendar: GpwCalendar, cfg: AppConfig
    ) -> None:
        self._s = session
        self._clock = clock
        self._cal = calendar
        self._cfg = cfg

    # --- portfel i gotówka ----------------------------------------------------

    def create_portfolio(self, pdef: PortfolioDef) -> int:
        """Tworzy portfel (idempotentnie po nazwie) z wpłatą początkową DEPOSIT."""
        existing = self._s.scalar(select(Portfolio.id).where(Portfolio.name == pdef.id))
        if existing is not None:
            return existing
        initial = self._cfg.portfolio.initial_cash_pln
        portfolio = Portfolio(
            name=pdef.id,
            strategy=pdef.strategy,
            initial_cash=initial,
            created_at=self._clock.now(),
        )
        self._s.add(portfolio)
        self._s.flush()
        self._entry(portfolio.id, "DEPOSIT", initial, available_from=None, ref=None)
        return portfolio.id

    def cash_total(self, pid: int) -> Decimal:
        """Suma wszystkich wpisów ledgera (wraz z wpłatą początkową)."""
        amounts = self._s.scalars(
            select(CashLedgerEntry.amount).where(CashLedgerEntry.portfolio_id == pid)
        ).all()
        return sum(amounts, ZERO)

    def cash_available(self, pid: int, on_date: dt.date) -> Decimal:
        """Gotówka dostępna `on_date`: wpisy z `available_from <= on_date` (NULL = zawsze)
        minus aktywne rezerwacje."""
        total = ZERO
        for amount, available in self._s.execute(
            select(CashLedgerEntry.amount, CashLedgerEntry.available_from).where(
                CashLedgerEntry.portfolio_id == pid
            )
        ):
            if available is None or available <= on_date:
                total += amount
        reserved = ZERO
        for (value,) in self._s.execute(
            select(Order.reserved_amount).where(
                Order.portfolio_id == pid, Order.reserved_amount.is_not(None)
            )
        ):
            if value is not None:  # WHERE wyklucza NULL; mypy nie zna filtra
                reserved += value
        return total - reserved

    def reserve(self, pid: int, order_id: int, amount: Decimal) -> None:
        """Blokuje gotówkę pod zlecenie BUY; nadpisuje wcześniejszą rezerwację zlecenia."""
        if amount < 0:
            raise ValueError(f"rezerwacja nie może być ujemna (otrzymano {amount})")
        order = self._s.get(Order, order_id)
        if order is None or order.portfolio_id != pid:
            raise LedgerError(
                f"zlecenie {order_id} nie istnieje w portfelu {pid}; rezerwacja niemożliwa"
            )
        order.reserved_amount = amount
        self._s.flush()

    def release(self, order_id: int) -> None:
        """Zwalnia rezerwację zlecenia (idempotentne)."""
        order = self._s.get(Order, order_id)
        if order is None:
            raise LedgerError(f"zlecenie {order_id} nie istnieje; release niemożliwy")
        order.reserved_amount = None
        self._s.flush()

    # --- fills ------------------------------------------------------------------

    def apply_fill(self, fill_id: int) -> None:
        """Księguje realizację zlecenia (BUY: nowa partia FIFO; SELL: konsumpcja FIFO)."""
        fill = self._s.get(Fill, fill_id)
        if fill is None:
            raise LedgerError(f"fill {fill_id} nie istnieje")
        order = self._s.get(Order, fill.order_id)
        assert order is not None  # FK orders.id — zawsze istnieje
        if fill.qty <= 0 or fill.price <= 0:
            msg = (
                f"fill {fill_id}: qty i cena muszą być dodatnie "
                f"(qty={fill.qty}, price={fill.price})"
            )
            raise LedgerError(msg)
        try:
            side = Side(order.side)
        except ValueError as exc:
            raise LedgerError(f"zlecenie {order.id}: nieznana strona '{order.side}'") from exc

        pid, inst_id = order.portfolio_id, order.instrument_id
        now = self._clock.now()
        if side is Side.BUY:
            self._apply_buy(pid, inst_id, fill)
        else:
            self._apply_sell(pid, inst_id, fill)
        self._release_reservation(order)
        if self.cash_total(pid) < 0:
            raise LedgerError(
                f"portfel {pid}: księgowanie filla {fill_id} dałoby ujemną gotówkę "
                f"({self.cash_total(pid)})"
            )
        _ = now

    def positions(self, pid: int, prices: Mapping[str, Decimal]) -> list[Position]:
        """Pozycje portfela (suma partii per instrument) wycenione po `prices` (ticker→cena)."""
        rows = self._s.execute(
            select(TaxLot.instrument_id, TaxLot.qty_remaining, TaxLot.buy_price).where(
                TaxLot.portfolio_id == pid, TaxLot.qty_remaining > 0
            )
        ).all()
        if not rows:
            return []
        by_instrument: dict[int, tuple[int, Decimal]] = {}
        for inst_id, qty, unit_cost in rows:
            total_qty, cost_sum = by_instrument.get(inst_id, (0, ZERO))
            by_instrument[inst_id] = (total_qty + qty, cost_sum + qty * unit_cost)

        tickers = dict(
            self._s.execute(
                select(Instrument.id, Instrument.ticker).where(Instrument.id.in_(by_instrument))
            ).all()
        )
        metas = {
            m.instrument_id: m
            for m in self._s.scalars(
                select(PositionMeta).where(PositionMeta.portfolio_id == pid)
            ).all()
        }
        today = self._clock.now().astimezone(WARSAW).date()

        out: list[Position] = []
        for inst_id, (qty, cost_sum) in sorted(by_instrument.items()):
            ticker = tickers.get(inst_id)
            if ticker is None:
                raise LedgerError(f"instrument {inst_id} bez wiersza w `instruments`")
            last_price = prices.get(ticker)
            if last_price is None:
                raise ValueError(
                    f"brak ceny dla pozycji {ticker}; `prices` musi obejmować wszystkie pozycje"
                )
            avg_cost = cost_sum / qty
            market_value = qty * last_price
            meta = metas.get(inst_id)
            opened = meta.opened_session if meta is not None else None
            held_sessions = 0
            if opened is not None and self._cal.is_session(opened) and opened < today:
                held_sessions = self._cal.count_sessions(opened, today)
            out.append(
                Position(
                    ticker=ticker,
                    qty=qty,
                    avg_cost=avg_cost,
                    last_price=last_price,
                    market_value=market_value,
                    unrealized_pnl=market_value - cost_sum,
                    held_sessions=held_sessions,
                    stop_price=meta.stop_price if meta is not None else None,
                )
            )
        return out

    def equity(self, pid: int, prices: Mapping[str, Decimal]) -> Decimal:
        """Wartość portfela = cała gotówka (nie tylko dostępna) + wartość pozycji."""
        total = self.cash_total(pid)
        for position in self.positions(pid, prices):
            total += position.market_value
        return total

    def peak_equity(self, pid: int) -> Decimal | None:
        """Maksymalna `equity` z zapisanych snapshotów; `None`, gdy brak snapshotów."""
        values = self._s.scalars(
            select(PortfolioSnapshot.equity).where(PortfolioSnapshot.portfolio_id == pid)
        ).all()
        return max(values, default=None)

    def snapshot(self, pid: int, date: dt.date, prices: Mapping[str, Decimal]) -> None:
        """Zapisuje (nadpisując po dacie) snapshot equity/gotówki/pozycji."""
        # Najpierw wartości, potem wiersz — autoflush nie może zainserować pustego snapshotu.
        positions = self.positions(pid, prices)
        payload: dict[str, object] = {
            p.ticker: {"qty": p.qty, "avg_cost": str(p.avg_cost), "last_price": str(p.last_price)}
            for p in positions
        }
        equity_value = self.equity(pid, prices)
        cash_value = self.cash_total(pid)
        row = self._s.get(PortfolioSnapshot, (pid, date))
        if row is None:
            row = PortfolioSnapshot(portfolio_id=pid, date=date)
            self._s.add(row)
        row.equity = equity_value
        row.cash = cash_value
        row.positions_json = payload
        self._s.flush()

    # --- zdarzenia korporacyjne -------------------------------------------------

    def apply_corporate_actions(self, pid: int, as_of: dt.date) -> int:
        """Księguje dywidendy i splity z `ex_date <= as_of`; idempotentne po `ref` w ledgerze.

        Dywidenda: dla akcji posiadanych na koniec sesji poprzedzającej `ex_date` (stan
        odtwarzany replayem fills); netto = brutto × (1 − podatek), wpływ od `pay_date`.
        Split: partie kupione przed `ex_date` mnożone przez współczynnik, koszt ÷;
        marker księgowania to wpis ADJUSTMENT o kwocie 0 (D-036).
        """
        actions = self._s.scalars(
            select(CorporateAction).where(CorporateAction.ex_date <= as_of)
        ).all()
        applied = 0
        for action in sorted(actions, key=lambda a: (a.ex_date, a.type)):
            if action.type == "DIVIDEND":
                ref = f"DIV:{action.instrument_id}:{action.ex_date.isoformat()}"
                if self._has_entry_ref(pid, ref):
                    continue
                record_date = self._cal.previous_session(action.ex_date)
                qty_held = self._position_qty_at(pid, action.instrument_id, record_date)
                if qty_held == 0:
                    continue
                net_per_share = net_dividend(action.value, self._cfg.costs.dividend_tax_pct)
                pay_date = action.pay_date if action.pay_date is not None else action.ex_date
                self._entry(
                    pid, "DIVIDEND", net_per_share * qty_held, available_from=pay_date, ref=ref
                )
            elif action.type == "SPLIT":
                ref = f"SPLIT:{action.instrument_id}:{action.ex_date.isoformat()}"
                if self._has_entry_ref(pid, ref):
                    continue
                factor = action.value
                if factor <= 0:
                    raise LedgerError(
                        f"split {ref}: współczynnik musi być dodatni (otrzymano {factor})"
                    )
                lots = self._s.scalars(
                    select(TaxLot).where(
                        TaxLot.portfolio_id == pid,
                        TaxLot.instrument_id == action.instrument_id,
                        TaxLot.qty_remaining > 0,
                        TaxLot.buy_date < action.ex_date,
                    )
                ).all()
                for lot in lots:
                    new_qty = Decimal(lot.qty_remaining) * factor
                    if new_qty != new_qty.to_integral_value():
                        raise LedgerError(
                            f"split {ref}: partia {lot.id} dałaby ułamkową liczbę akcji ({new_qty})"
                        )
                    lot.qty_remaining = int(new_qty)
                    lot.buy_price = lot.buy_price / factor
                self._entry(pid, "ADJUSTMENT", ZERO, available_from=action.ex_date, ref=ref)
            else:
                raise LedgerError(
                    f"instrument {action.instrument_id}: nieznany typ '{action.type}'"
                )
            applied += 1
        return applied

    # --- niezmienniki -------------------------------------------------------------

    def check_invariants(self, pid: int) -> None:
        """Sprawdza spójność księgi portfela; naruszenie rzuca `LedgerError`.

        Kontrolowane warunki: gotówka >= 0; partie o dodatniej ilości; każda sprzedaż ma
        rozliczone `realized_pnl`; rezerwacje tylko dla zleceń BUY.
        """
        if self.cash_total(pid) < 0:
            raise LedgerError(f"portfel {pid}: ujemna gotówka ({self.cash_total(pid)})")
        bad_lots = self._s.scalars(
            select(TaxLot.id).where(TaxLot.portfolio_id == pid, TaxLot.qty_remaining <= 0)
        ).all()
        if bad_lots:
            raise LedgerError(f"portfel {pid}: partie o niepoprawnej ilości: {bad_lots}")
        unsettled = self._s.scalars(
            select(Fill.id)
            .join(Order, Fill.order_id == Order.id)
            .where(Order.portfolio_id == pid, Order.side == "SELL", Fill.realized_pnl.is_(None))
        ).all()
        if unsettled:
            raise LedgerError(f"portfel {pid}: sprzedaże bez rozliczenia FIFO: {unsettled}")
        bad_reservations = self._s.scalars(
            select(Order.id).where(
                Order.portfolio_id == pid,
                Order.reserved_amount.is_not(None),
                Order.side != "BUY",
            )
        ).all()
        if bad_reservations:
            raise LedgerError(f"portfel {pid}: rezerwacje przy zleceniach innych niż BUY")

    # --- wewnętrzne -----------------------------------------------------------------

    def _apply_buy(self, pid: int, inst_id: int, fill: Fill) -> None:
        unit_cost = fill.price + fill.commission / fill.qty  # dokładny iloraz (D-035)
        had_position = (
            self._s.scalar(
                select(TaxLot.id).where(
                    TaxLot.portfolio_id == pid,
                    TaxLot.instrument_id == inst_id,
                    TaxLot.qty_remaining > 0,
                )
            )
            is not None
        )
        lot = TaxLot(
            portfolio_id=pid,
            instrument_id=inst_id,
            qty_remaining=fill.qty,
            buy_price=unit_cost,
            buy_date=fill.session_date,
            fill_id=fill.id,
        )
        self._s.add(lot)
        self._entry(
            pid, "BUY", -(fill.price * fill.qty), available_from=fill.session_date, ref=str(fill.id)
        )
        self._entry(
            pid, "COMMISSION", -fill.commission, available_from=fill.session_date, ref=str(fill.id)
        )
        meta = self._s.get(PositionMeta, (pid, inst_id))
        if meta is None:
            meta = PositionMeta(portfolio_id=pid, instrument_id=inst_id)
            self._s.add(meta)
        if not had_position:
            meta.opened_session = fill.session_date

    def _apply_sell(self, pid: int, inst_id: int, fill: Fill) -> None:
        rows = self._s.execute(
            select(TaxLot.id, TaxLot.buy_date, TaxLot.qty_remaining, TaxLot.buy_price).where(
                TaxLot.portfolio_id == pid,
                TaxLot.instrument_id == inst_id,
                TaxLot.qty_remaining > 0,
            )
        ).all()
        lots = [
            Lot(id=lot_id, buy_date=d, qty_remaining=q, unit_cost=c) for lot_id, d, q, c in rows
        ]
        consumptions = consume_lots(lots, fill.qty)
        cost_basis = sum((c.qty * c.unit_cost for c in consumptions), ZERO)
        proceeds = fill.price * fill.qty
        fill.realized_pnl = proceeds - cost_basis - fill.commission

        lot_rows = {
            lot.id: lot
            for lot in self._s.scalars(
                select(TaxLot).where(
                    TaxLot.portfolio_id == pid,
                    TaxLot.instrument_id == inst_id,
                    TaxLot.qty_remaining > 0,
                )
            ).all()
        }
        for consumption in consumptions:
            lot = lot_rows[consumption.lot_id]
            if consumption.qty >= lot.qty_remaining:
                self._s.delete(lot)
            else:
                lot.qty_remaining -= consumption.qty

        settlement_date = self._settlement_date(fill.session_date)
        self._entry(pid, "SELL", proceeds, available_from=settlement_date, ref=str(fill.id))
        self._entry(
            pid, "COMMISSION", -fill.commission, available_from=settlement_date, ref=str(fill.id)
        )
        meta = self._s.get(PositionMeta, (pid, inst_id))
        if meta is None:
            meta = PositionMeta(portfolio_id=pid, instrument_id=inst_id)
            self._s.add(meta)
        meta.last_sell_session = fill.session_date

    def _settlement_date(self, session_date: dt.date) -> dt.date:
        """Data dostępności wpływów ze sprzedaży (T+N przez kalendarz; D-036)."""
        if self._cfg.market.settle_instantly:
            return session_date
        try:
            return self._cal.add_sessions(session_date, self._cfg.market.settlement_days)
        except ValueError as exc:
            raise LedgerError(
                f"data sesji filla {session_date.isoformat()} nie jest dniem sesji GPW"
            ) from exc

    def _release_reservation(self, order: Order) -> None:
        if order.reserved_amount is not None:
            order.reserved_amount = None

    def _entry(
        self,
        pid: int,
        entry_type: str,
        amount: Decimal,
        *,
        available_from: dt.date | None,
        ref: str | None,
    ) -> None:
        self._s.add(
            CashLedgerEntry(
                portfolio_id=pid,
                ts=self._clock.now(),
                type=entry_type,
                amount=amount,
                available_from=available_from,
                ref=ref,
            )
        )
        self._s.flush()

    def _has_entry_ref(self, pid: int, ref: str) -> bool:
        return (
            self._s.scalar(
                select(CashLedgerEntry.id).where(
                    CashLedgerEntry.portfolio_id == pid, CashLedgerEntry.ref == ref
                )
            )
            is not None
        )

    def _position_qty_at(self, pid: int, inst_id: int, on_date: dt.date) -> int:
        """Akcje posiadane na koniec `on_date` (replay fills: kupione minus sprzedane)."""
        rows = self._s.execute(
            select(Fill.qty, Order.side)
            .join(Order, Fill.order_id == Order.id)
            .where(
                Order.portfolio_id == pid,
                Order.instrument_id == inst_id,
                Fill.session_date <= on_date,
            )
            .order_by(Fill.session_date, Fill.id)
        ).all()
        qty = 0
        for fill_qty, side in rows:
            qty += fill_qty if side == "BUY" else -fill_qty
        return max(qty, 0)
