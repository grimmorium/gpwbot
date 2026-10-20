"""Testy S04: `sync_instruments`, `get_universe` (filtry, granice, brak look-ahead).

Zakres z docs/sessions/S04.md: sync idempotentny + usunięcie z seedu = active=false;
każdy powód wykluczenia osobno (sztuczne ceny); granice progów (obrót == próg,
dokładnie 250 sesji historii); brak look-ahead; wynik deterministyczny po tickerze.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from gpwbot.calendar import GpwCalendar
from gpwbot.clock import FakeClock
from gpwbot.config import AppConfig, load_config
from gpwbot.context import AppContext
from gpwbot.db.models import Base, Instrument
from gpwbot.db.repo import (
    InstrumentUpsert,
    PriceBar,
    get_instrument_by_ticker,
    upsert_instruments,
    upsert_prices,
)
from gpwbot.db.session import create_engine_for, make_session_factory, session_scope
from gpwbot.errors import ConfigError
from gpwbot.universe import (
    ExclusionReason,
    UniverseResult,
    get_universe,
    load_seed,
    sync_instruments,
)

WARSAW = ZoneInfo("Europe/Warsaw")
AS_OF_DATE = dt.date(2026, 10, 15)  # czwartek — dzień sesji GPW
AS_OF = dt.datetime(2026, 10, 15, 23, 59, 59, tzinfo=WARSAW)

# Bar testowy: (data, close, volume, turnover[, quality_flag]).
BarSpec = (
    tuple[dt.date, Decimal, int | None, Decimal | None]
    | tuple[dt.date, Decimal, int | None, Decimal | None, str]
)


def write_config(
    tmp_path: Path,
    *,
    min_history_sessions: int = 3,
    min_avg_turnover_pln_20d: Decimal | str = "100",
    min_price_pln: Decimal | str = "1",
) -> AppConfig:
    text = (
        "paths:\n"
        f"  db_path: {(tmp_path / 'test.db').as_posix()}\n"
        f"  data_dir: {tmp_path.as_posix()}\n"
        f"  raw_dir: {(tmp_path / 'raw').as_posix()}\n"
        f"  reports_dir: {(tmp_path / 'reports').as_posix()}\n"
        "universe:\n"
        "  indices: [wig20]\n"
        f"  min_avg_turnover_pln_20d: {min_avg_turnover_pln_20d}\n"
        f"  min_history_sessions: {min_history_sessions}\n"
        f"  min_price_pln: {min_price_pln}\n"
    )
    config_file = tmp_path / "config.yaml"
    config_file.parent.mkdir(parents=True, exist_ok=True)  # testy podkatalogowe (sh/h250)
    config_file.write_text(text, encoding="utf-8")
    return load_config(config_file)


@pytest.fixture
def ctx(tmp_path: Path, fake_clock: FakeClock) -> AppContext:
    """Kontekst z bazą (schemat nałożony), deterministycznym zegarem i configiem testowym."""
    cfg = write_config(tmp_path)
    engine = create_engine_for(cfg)
    Base.metadata.create_all(engine)
    factory: sessionmaker[Session] = make_session_factory(engine)
    yield AppContext(config=cfg, clock=fake_clock, session_factory=factory)
    engine.dispose()


@pytest.fixture
def calendar(ctx: AppContext) -> GpwCalendar:
    cal = GpwCalendar(ctx.config)
    assert cal.is_session(AS_OF_DATE), "test zakłada sesję GPW 2026-10-15"
    return cal


def session_dates_ending_at(calendar: GpwCalendar, end_date: dt.date, n: int) -> list[dt.date]:
    """`n` kolejnych dni sesji kończących się na `end_date` (rosnąco)."""
    dates: list[dt.date] = []
    d = end_date
    for _ in range(n):
        dates.append(d)
        d = calendar.previous_session(d)
    return list(reversed(dates))


def add_instrument(
    ctx: AppContext, ticker: str, *, active: bool = True, indices=("wig20",)
) -> None:
    with session_scope(ctx.session_factory) as s:
        upsert_instruments(
            s, [InstrumentUpsert(ticker=ticker, index_membership=list(indices), active=active)]
        )


def add_bars(ctx: AppContext, ticker: str, bars: list[BarSpec]) -> None:
    """Sztuczne ceny; open=high=low=close (brak problemów jakościowych OHLC)."""
    with session_scope(ctx.session_factory) as s:
        inst = get_instrument_by_ticker(s, ticker)
        assert inst is not None
        repo_bars = [
            PriceBar(
                date=b[0],
                open=b[1],
                high=b[1],
                low=b[1],
                close=b[1],
                volume=b[2],
                turnover_pln=b[3],
                quality_flag=b[4] if len(b) == 5 else None,
            )
            for b in bars
        ]
        upsert_prices(s, inst.id, repo_bars, "test", now=ctx.clock.now())


def good_bars(
    calendar: GpwCalendar,
    n: int = 5,
    *,
    close: Decimal = Decimal("10"),
    volume: int = 1000,
    turnover: Decimal | None = Decimal("1000"),
) -> list[BarSpec]:
    """`n` dobrych barów kończących się na AS_OF_DATE (przechodzą wszystkie filtry)."""
    to = turnover if turnover is not None else close * volume
    return [(d, close, volume, to) for d in session_dates_ending_at(calendar, AS_OF_DATE, n)]


def write_seed(tmp_path: Path, tickers: list[str]) -> Path:
    lines = [f"- ticker: {t}\n  indices: [wig20]\n" for t in tickers]
    seed_file = tmp_path / "seed.yaml"
    seed_file.write_text("".join(lines), encoding="utf-8")
    return seed_file


class TestSyncInstruments:
    def test_idempotentny(self, ctx: AppContext, tmp_path: Path) -> None:
        seed_file = write_seed(tmp_path, ["AAA", "BBB"])

        report1 = sync_instruments(ctx, seed_file)
        report2 = sync_instruments(ctx, seed_file)

        assert (report1.synced, report1.deactivated) == (2, 0)
        assert (report2.synced, report2.deactivated) == (2, 0)
        with ctx.session_factory() as s:
            tickers = {i.ticker for i in s.scalars(select(Instrument)).all()}
        assert tickers == {"AAA", "BBB"}

    def test_usuniecie_z_seedu_daje_active_false(self, ctx: AppContext, tmp_path: Path) -> None:
        seed_file = write_seed(tmp_path, ["AAA", "BBB"])
        sync_instruments(ctx, seed_file)

        write_seed(tmp_path, ["AAA"])  # nadpisanie tego samego pliku
        report = sync_instruments(ctx, seed_file)

        assert report.deactivated == 1
        with ctx.session_factory() as s:
            aaa = get_instrument_by_ticker(s, "AAA")
            bbb = get_instrument_by_ticker(s, "BBB")
        assert aaa is not None and aaa.active is True
        # Wiersz zostaje (nie jest kasowany), ale instrument wyłącza się z universe.
        assert bbb is not None and bbb.active is False

    def test_brak_pliku_seedu_rzuca_config_error(self, ctx: AppContext) -> None:
        with pytest.raises(ConfigError):
            sync_instruments(ctx, Path("nie_ma_takiego_seedu.yaml"))


class TestLoadSeed:
    def test_zduplikowany_ticker_jest_błędem(self, tmp_path: Path) -> None:
        seed_file = write_seed(tmp_path, ["AAA", "aaa"])  # normalizacja do uppercase
        with pytest.raises(ConfigError):
            load_seed(seed_file)

    def test_nieznany_klucz_jest_błędem(self, tmp_path: Path) -> None:
        seed_file = tmp_path / "seed.yaml"
        seed_file.write_text("- ticker: AAA\n  indeksy: [wig20]\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_seed(seed_file)

    def test_tickery_i_indeksy_normalizowane(self, tmp_path: Path) -> None:
        seed_file = tmp_path / "seed.yaml"
        seed_file.write_text(
            "- ticker:  aaa \n  indices: [WIG20]\n  aliases: [Aaa]\n", encoding="utf-8"
        )
        (seed,) = load_seed(seed_file)
        assert seed.ticker == "AAA"
        assert seed.indices == ("wig20",)


class TestGetUniverse:
    def test_instrument_speelniajacy_warunki_w_members(
        self, ctx: AppContext, calendar: GpwCalendar
    ) -> None:
        add_instrument(ctx, "AAA")
        add_bars(ctx, "AAA", good_bars(calendar))

        result = get_universe(ctx, AS_OF)

        assert isinstance(result, UniverseResult)
        assert result.members == ("AAA",)
        assert result.excluded == {}

    def test_inactive(self, ctx: AppContext, calendar: GpwCalendar) -> None:
        add_instrument(ctx, "AAA", active=False)
        add_bars(ctx, "AAA", good_bars(calendar))  # nawet idealne dane nie ratują

        result = get_universe(ctx, AS_OF)

        assert result.members == ()
        assert result.excluded == {"AAA": ExclusionReason.INACTIVE}

    def test_stale(self, ctx: AppContext, calendar: GpwCalendar) -> None:
        add_instrument(ctx, "AAA")
        # Ostatni bar dzień sesji wcześniej — dane starsze niż 1 sesja.
        bars = good_bars(calendar, n=5)[:-1]
        add_bars(ctx, "AAA", bars)

        result = get_universe(ctx, AS_OF)

        assert result.excluded == {"AAA": ExclusionReason.STALE}

    def test_suspended(self, ctx: AppContext, calendar: GpwCalendar) -> None:
        add_instrument(ctx, "AAA")
        bars = good_bars(calendar, n=5)
        last_date, close, _, _ = bars[-1]
        bars[-1] = (last_date, close, 0, Decimal("0"))  # świeca bez obrotów
        add_bars(ctx, "AAA", bars)

        result = get_universe(ctx, AS_OF)

        assert result.excluded == {"AAA": ExclusionReason.SUSPENDED}

    def test_short_history(
        self, tmp_path: Path, fake_clock: FakeClock, calendar: GpwCalendar
    ) -> None:
        # Osobny kontekst z pełnym progiem 250 sesji.
        cfg = write_config(tmp_path / "sh", min_history_sessions=250)
        engine = create_engine_for(cfg)
        Base.metadata.create_all(engine)
        factory = make_session_factory(engine)
        ctx_sh = AppContext(config=cfg, clock=fake_clock, session_factory=factory)
        try:
            add_instrument(ctx_sh, "AAA")
            add_bars(ctx_sh, "AAA", good_bars(calendar, n=249))

            result = get_universe(ctx_sh, AS_OF)
        finally:
            engine.dispose()

        assert result.excluded == {"AAA": ExclusionReason.SHORT_HISTORY}

    def test_low_turnover(self, ctx: AppContext, calendar: GpwCalendar) -> None:
        add_instrument(ctx, "AAA")
        bars = good_bars(calendar, n=5, turnover=Decimal("50"))  # < progu 100
        add_bars(ctx, "AAA", bars)

        result = get_universe(ctx, AS_OF)

        assert result.excluded == {"AAA": ExclusionReason.LOW_TURNOVER}

    def test_low_price(self, ctx: AppContext, calendar: GpwCalendar) -> None:
        add_instrument(ctx, "AAA")
        bars = good_bars(calendar, n=5, close=Decimal("0.5"), turnover=Decimal("1000"))
        add_bars(ctx, "AAA", bars)

        result = get_universe(ctx, AS_OF)

        assert result.excluded == {"AAA": ExclusionReason.LOW_PRICE}

    def test_needs_review(self, ctx: AppContext, calendar: GpwCalendar) -> None:
        add_instrument(ctx, "AAA")
        bars = good_bars(calendar, n=5)
        d, close, volume, turnover = bars[2]
        bars[2] = (d, close, volume, turnover, "needs_review")  # flaga na środkowej świecy
        add_bars(ctx, "AAA", bars)

        result = get_universe(ctx, AS_OF)

        assert result.excluded == {"AAA": ExclusionReason.NEEDS_REVIEW}


class TestGraniceProgów:
    def test_obrót_dokładnie_równy_progowi_przechodzi(
        self, ctx: AppContext, calendar: GpwCalendar
    ) -> None:
        add_instrument(ctx, "AAA")
        bars = good_bars(calendar, n=20, turnover=Decimal("100"))  # == próg
        add_bars(ctx, "AAA", bars)

        result = get_universe(ctx, AS_OF)

        assert result.members == ("AAA",)

    def test_cena_dokładnie_równa_min_price_przechodzi(
        self, ctx: AppContext, calendar: GpwCalendar
    ) -> None:
        add_instrument(ctx, "AAA")
        bars = good_bars(calendar, n=5, close=Decimal("1"), turnover=Decimal("1000"))
        add_bars(ctx, "AAA", bars)

        result = get_universe(ctx, AS_OF)

        assert result.members == ("AAA",)

    def test_historia_dokładnie_250_sesji_przechodzi(
        self, tmp_path: Path, fake_clock: FakeClock, calendar: GpwCalendar
    ) -> None:
        cfg = write_config(tmp_path / "h250", min_history_sessions=250)
        engine = create_engine_for(cfg)
        Base.metadata.create_all(engine)
        factory = make_session_factory(engine)
        ctx_h = AppContext(config=cfg, clock=fake_clock, session_factory=factory)
        try:
            add_instrument(ctx_h, "AAA")
            add_bars(ctx_h, "AAA", good_bars(calendar, n=250))

            result = get_universe(ctx_h, AS_OF)
        finally:
            engine.dispose()

        assert result.members == ("AAA",)


class TestBrakLookAhead:
    def test_wiersz_po_as_of_nie_zmienia_wyniku(
        self, ctx: AppContext, calendar: GpwCalendar
    ) -> None:
        add_instrument(ctx, "AAA")
        add_bars(ctx, "AAA", good_bars(calendar))
        before = get_universe(ctx, AS_OF)

        # Bar z datą > as_of (następna sesja), który sam w sobie wykluczyłby instrument.
        next_date = calendar.add_sessions(AS_OF_DATE, 1)
        add_bars(ctx, "AAA", [(next_date, Decimal("10"), 0, Decimal("0"))])

        after = get_universe(ctx, AS_OF)

        assert before == after
        assert after.members == ("AAA",)


class TestDeterminizm:
    def test_kolejność_po_tickerze(self, ctx: AppContext, calendar: GpwCalendar) -> None:
        for ticker in ("ZZZ", "MMM", "AAA"):  # celowo niealfabetyczna kolejność zapisu
            add_instrument(ctx, ticker)
            add_bars(ctx, ticker, good_bars(calendar))

        result = get_universe(ctx, AS_OF)

        assert result.members == ("AAA", "MMM", "ZZZ")


class TestWalidacjaWejścia:
    def test_naive_as_of_rzuca_value_error(self, ctx: AppContext) -> None:
        with pytest.raises(ValueError):
            get_universe(ctx, dt.datetime(2026, 10, 15, 23, 59))
