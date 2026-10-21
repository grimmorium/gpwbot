"""Modele SQLAlchemy 2.0 (S01): pełny schemat z Części I sekcji 6 + `portfolios`.

Konwencje: pieniądze/ceny przez `DecimalStr`, czas przez `UtcDateTime` (A3).
Późniejsze sesje dodają tabele wyłącznie kolejnymi rewizjami Alembic.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from gpwbot.db.types import DecimalStr, UtcDateTime


class Base(DeclarativeBase):
    """Baza deklaratywna; `Base.metadata` jest źródłem prawdy dla migracji."""


class Portfolio(Base):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    strategy: Mapped[str] = mapped_column(String(64))
    initial_cash: Mapped[Decimal] = mapped_column(DecimalStr)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), unique=True)
    isin: Mapped[str | None] = mapped_column(String(12))
    name: Mapped[str | None] = mapped_column(String(255))
    segment: Mapped[str | None] = mapped_column(String(32))
    sector: Mapped[str | None] = mapped_column(String(64))
    index_membership: Mapped[list[str] | None] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    aliases_json: Mapped[list[str] | None] = mapped_column(JSON)


class PricesDaily(Base):
    __tablename__ = "prices_daily"
    __table_args__ = (Index("ix_prices_daily_instrument_date", "instrument_id", "date"),)

    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    open: Mapped[Decimal] = mapped_column(DecimalStr)  # noqa: A003 — nazwa kolumny wg specyfikacji
    high: Mapped[Decimal] = mapped_column(DecimalStr)
    low: Mapped[Decimal] = mapped_column(DecimalStr)
    close: Mapped[Decimal] = mapped_column(DecimalStr)
    adj_close: Mapped[Decimal | None] = mapped_column(DecimalStr)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    turnover_pln: Mapped[Decimal | None] = mapped_column(DecimalStr)
    ingested_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)
    quality_flag: Mapped[str | None] = mapped_column(String(32))
    adjusted: Mapped[bool] = mapped_column(Boolean, default=False)


class NewsItem(Base):
    __tablename__ = "news_items"
    __table_args__ = (Index("ix_news_items_published_at", "published_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(Text)
    url_hash: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str | None] = mapped_column(String(1000))
    body: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    ingested_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)
    dedupe_group: Mapped[int | None] = mapped_column(Integer)


class Filing(Base):
    __tablename__ = "filings"

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    type: Mapped[str] = mapped_column(String(32))  # ESPI / EBI / periodic
    number: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(1000))
    body: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)
    ingested_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)


class TextExtraction(Base):
    __tablename__ = "text_extractions"
    __table_args__ = (
        UniqueConstraint(
            "item_type",
            "item_id",
            "instrument_id",
            "prompt_hash",
            "model",
            name="uq_text_extractions_identity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    item_type: Mapped[str] = mapped_column(String(32))  # news / filing
    item_id: Mapped[int] = mapped_column(Integer)
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"))
    event_type: Mapped[str] = mapped_column(String(32))
    sentiment: Mapped[float] = mapped_column(Float)  # -1..1
    materiality: Mapped[float] = mapped_column(Float)  # 0..1
    is_new_information: Mapped[bool] = mapped_column(Boolean, default=False)
    summary_pl: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(128))
    prompt_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)


class FeatureSnapshot(Base):
    __tablename__ = "feature_snapshots"

    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    as_of: Mapped[dt.datetime] = mapped_column(UtcDateTime, primary_key=True)
    features_json: Mapped[dict[str, object]] = mapped_column(JSON)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (UniqueConstraint("name", "as_of", name="uq_runs_name_as_of"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    as_of: Mapped[dt.datetime] = mapped_column(UtcDateTime)
    started_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    finished_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    config_hash: Mapped[str] = mapped_column(String(64))
    model_info: Mapped[dict[str, object] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="running")


class Decision(Base):
    __tablename__ = "decisions"
    __table_args__ = (Index("ix_decisions_run_id", "run_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    action: Mapped[str] = mapped_column(String(16))  # BUY / SELL / HOLD
    confidence: Mapped[float] = mapped_column(Float)
    horizon_days: Mapped[int] = mapped_column(Integer)
    thesis_pl: Mapped[str] = mapped_column(Text)
    evidence_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    risks_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    invalidation_pl: Mapped[str] = mapped_column(Text)
    raw_llm_json: Mapped[dict[str, object] | None] = mapped_column(JSON)
    accepted_by_risk: Mapped[bool | None] = mapped_column(Boolean)
    risk_notes: Mapped[list[str] | None] = mapped_column(JSON)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_portfolio_status", "portfolio_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    side: Mapped[str] = mapped_column(String(8))  # BUY / SELL
    qty: Mapped[int] = mapped_column(Integer)
    order_type: Mapped[str] = mapped_column(String(16), default="limit")
    limit_price: Mapped[Decimal | None] = mapped_column(DecimalStr)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)
    valid_from_session: Mapped[dt.date] = mapped_column(Date)
    # S06: aktywna rezerwacja gotówki (reserve/release); NULL = brak rezerwacji.
    reserved_amount: Mapped[Decimal | None] = mapped_column(DecimalStr)


class Fill(Base):
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    session_date: Mapped[dt.date] = mapped_column(Date)
    price: Mapped[Decimal] = mapped_column(DecimalStr)
    qty: Mapped[int] = mapped_column(Integer)
    commission: Mapped[Decimal] = mapped_column(DecimalStr, default=Decimal("0"))
    slippage_cost: Mapped[Decimal] = mapped_column(DecimalStr, default=Decimal("0"))
    # S06: wynik podatkowy sprzedaży (przychód − koszt partii FIFO − prowizja); NULL dla BUY.
    realized_pnl: Mapped[Decimal | None] = mapped_column(DecimalStr)


class TaxLot(Base):
    __tablename__ = "tax_lots"

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    qty_remaining: Mapped[int] = mapped_column(Integer)
    buy_price: Mapped[Decimal] = mapped_column(DecimalStr)
    buy_date: Mapped[dt.date] = mapped_column(Date)
    fill_id: Mapped[int | None] = mapped_column(ForeignKey("fills.id"), index=True)


class CashLedgerEntry(Base):
    __tablename__ = "cash_ledger"

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"))
    ts: Mapped[dt.datetime] = mapped_column(UtcDateTime)
    type: Mapped[str] = mapped_column(
        String(32)
    )  # DEPOSIT/BUY/SELL/COMMISSION/DIVIDEND/ADJUSTMENT (S06)
    amount: Mapped[Decimal] = mapped_column(DecimalStr)
    ref: Mapped[str | None] = mapped_column(String(256))
    # S06: od kiedy wpływ jest dostępny (rozliczenie T+2, data wypłaty dywidendy);
    # NULL = natychmiast. `ts` to czas zapisu audytowego, nie data biznesowa.
    available_from: Mapped[dt.date | None] = mapped_column(Date)


class PositionMeta(Base):
    """S06: metadane pozycji per (portfolio, instrument); stop/ATR ustawia S08."""

    __tablename__ = "position_meta"

    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    stop_price: Mapped[Decimal | None] = mapped_column(DecimalStr)
    entry_atr: Mapped[Decimal | None] = mapped_column(DecimalStr)
    opened_session: Mapped[dt.date | None] = mapped_column(Date)
    last_sell_session: Mapped[dt.date | None] = mapped_column(Date)


class CorporateAction(Base):
    """S06: zdarzenia korporacyjne (DIVIDEND — wartość brutto na akcję; SPLIT — współczynnik)."""

    __tablename__ = "corporate_actions"

    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    type: Mapped[str] = mapped_column(String(16), primary_key=True)  # DIVIDEND / SPLIT
    ex_date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    pay_date: Mapped[dt.date | None] = mapped_column(Date)
    value: Mapped[Decimal] = mapped_column(DecimalStr)


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    equity: Mapped[Decimal] = mapped_column(DecimalStr)
    cash: Mapped[Decimal] = mapped_column(DecimalStr)
    positions_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)


class LlmCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(64))  # decision / extraction / linker
    prompt: Mapped[str] = mapped_column(Text)
    response: Mapped[str | None] = mapped_column(Text)
    thinking: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    prompt_hash: Mapped[str] = mapped_column(String(64))
