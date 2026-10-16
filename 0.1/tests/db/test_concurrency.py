"""Test współbieżności (S01): dwa wątki piszące bez `database is locked`."""

from __future__ import annotations

import datetime as dt
import threading
from decimal import Decimal

from sqlalchemy import func, select

from gpwbot.db.models import CashLedgerEntry, Portfolio
from gpwbot.db.session import session_scope


def test_dwa_watki_pisza_bez_locked(tmp_db):
    """WAL + busy_timeout: równoległe zapisy dwóch wątków kończą się sukcesem."""
    engine, factory = tmp_db  # noqa: F841 — engine zapewnia PRAGMA i pool

    portfolio_id_box: list[int] = []
    with session_scope(factory) as s:
        p = Portfolio(
            name="test",
            strategy="test",
            initial_cash=Decimal("100"),
            created_at=dt.datetime(2026, 10, 15, tzinfo=dt.UTC),
        )
        s.add(p)
        s.flush()
        portfolio_id_box.append(p.id)

    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def worker(n: int) -> None:
        try:
            barrier.wait(timeout=10)
            for i in range(10):
                with session_scope(factory) as s:
                    for j in range(5):
                        ts = dt.datetime(2026, 10, 15, tzinfo=dt.UTC) + dt.timedelta(
                            seconds=n * 10_000 + i * 5 + j
                        )
                        s.add(
                            CashLedgerEntry(
                                portfolio_id=portfolio_id_box[0],
                                ts=ts,
                                type="test",
                                amount=Decimal("1"),
                                ref=f"{n}-{i}-{j}",
                            )
                        )
        except BaseException as exc:  # noqa: BLE001 — zbieramy błędy z wątków
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not any(t.is_alive() for t in threads), "wątek nie zakończył się w czasie"
    assert not errors, f"błędy wątków: {errors!r}"

    with factory() as s:
        count = s.scalar(select(func.count()).select_from(CashLedgerEntry))
    assert count == 100
