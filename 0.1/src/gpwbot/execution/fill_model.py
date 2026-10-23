"""Model realizacji zleceń (S07): czysta funkcja `simulate_fill` i powody braku filla.

A3: czysta funkcja — bez I/O ani bazy; pieniądze jako Decimal, arytmetyka dokładna.
Realizacja po cenie otwarcia ± slippage (zawsze niekorzystnie dla portfela), zaokrąglona
do ticka i przycięta do limitu (Część I sekcja 12). Bez częściowych realizacji: albo
pełna ilość, albo brak filla. Parametry wyłącznie z `CostsCfg`/tabeli ticków.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from gpwbot.config import CostsCfg
from gpwbot.costs import apply_slippage
from gpwbot.costs import commission as calc_commission
from gpwbot.db.repo import PriceBar
from gpwbot.domain import OrderIntent, Side
from gpwbot.execution.orders import OrderReason
from gpwbot.ticks import DEFAULT_GROUP, RoundMode, TickTable


@dataclass(frozen=True)
class FillResult:
    """Wynik symulowanej realizacji (pełna ilość; bez częściowych)."""

    price: Decimal
    qty: int
    commission: Decimal
    slippage_cost: Decimal


def _limit_ok(side: Side, open_price: Decimal, limit: Decimal | None) -> bool:
    """Czy otwarcie spełnia limit (BUY: `open ≤ limit`; SELL: `open ≥ limit`)."""
    if limit is None:
        return True
    if side is Side.BUY:
        return open_price <= limit
    return open_price >= limit


def _volume_ok(qty: int, volume: int | None, pct: Decimal) -> bool:
    """Czy ilość mieści się w limicie udziału w wolumenie sesji (`pct` %).

    `volume is None` (brak danych o obrotach) nie blokuje — udziału nie da się policzyć.
    Granica jest dozwolona: fill tylko gdy `qty ≤ volume × pct / 100`. Ujemny wolumen to dane
    niespójne z definicją limitu (maksymalna ilość ujemna) — każda dodatnia `qty` go przekracza,
    więc realizacja jest odrzucana (`VOLUME_LIMIT`; D-049).
    """
    if volume is None:
        return True
    max_qty = Decimal(volume) * pct / Decimal(100)
    return Decimal(qty) <= max_qty


def rejection_reason(order: OrderIntent, bar: PriceBar, costs_cfg: CostsCfg) -> str | None:
    """Powód braku realizacji (`LIMIT_NOT_MET`/`VOLUME_LIMIT`) albo `None`, gdy fill możliwy.

    Używany przez SimBroker do wypełnienia `orders.reason`; współdzieli predykaty z
    `simulate_fill`, więc oba wyniki są zawsze spójne.
    """
    if not _limit_ok(order.side, bar.open, order.limit_price):
        return OrderReason.LIMIT_NOT_MET.value
    if not _volume_ok(order.qty, bar.volume, costs_cfg.max_volume_participation_pct):
        return OrderReason.VOLUME_LIMIT.value
    return None


def simulate_fill(
    order: OrderIntent,
    bar: PriceBar,
    costs_cfg: CostsCfg,
    tick_table: TickTable,
    group: str = DEFAULT_GROUP,
) -> FillResult | None:
    """Symuluje realizację zlecenia limitowego na otwarciu sesji (Część I sekcja 12).

    Kolejność reguł:
      1. otwarcie musi spełniać limit — inaczej brak realizacji (`LIMIT_NOT_MET`);
      2. ilość nie może przekroczyć `volume × max_volume_participation_pct / 100`
         (inaczej `VOLUME_LIMIT`) — bez częściowych realizacji;
      3. cena = `apply_slippage(open, side, slippage_bps)` zaokrąglona do ticka
         (BUY w górę, SELL w dół), a potem przycięta do limitu (BUY: nie wyżej,
         SELL: nie niżej);
      4. `commission` z `costs.commission(price × qty)`;
         `slippage_cost = abs(cena − open) × qty`.

    Zwraca `FillResult` albo `None`; powód braku realizacji daje `rejection_reason`.
    """
    if order.qty <= 0:
        raise ValueError(f"qty musi być dodatnie (otrzymano {order.qty})")
    open_price = bar.open
    if not _limit_ok(order.side, open_price, order.limit_price):
        return None
    if not _volume_ok(order.qty, bar.volume, costs_cfg.max_volume_participation_pct):
        return None

    raw = apply_slippage(open_price, order.side, costs_cfg.slippage_bps)
    mode = RoundMode.UP if order.side is Side.BUY else RoundMode.DOWN
    price = tick_table.round_price(raw, mode, group=group)
    limit = order.limit_price
    # Przycięcie do limitu po zaokrągleniu (specyfikacja S07 krok 3; D-044).
    if limit is not None and (
        (order.side is Side.BUY and price > limit) or (order.side is Side.SELL and price < limit)
    ):
        price = limit

    value = price * order.qty
    comm = calc_commission(value, costs_cfg)
    slippage_cost = abs(price - open_price) * order.qty
    return FillResult(
        price=price,
        qty=order.qty,
        commission=comm,
        slippage_cost=slippage_cost,
    )
