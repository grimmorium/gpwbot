"""Testy migracji Alembic (S01): upgrade head, compare_metadata, downgrade base."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.autogenerate.api import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from gpwbot.db import migrate
from gpwbot.db.models import Base

EXPECTED_TABLES = {
    "instruments",
    "prices_daily",
    "news_items",
    "filings",
    "text_extractions",
    "feature_snapshots",
    "runs",
    "decisions",
    "orders",
    "fills",
    "tax_lots",
    "cash_ledger",
    "portfolio_snapshots",
    "llm_calls",
    "portfolios",
    # S06: ledger, pozycje, partie FIFO
    "position_meta",
    "corporate_actions",
}


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    return tmp_path / "mig.db"


def test_upgrade_head_tworzy_wszystkie_tabele(db_file):
    """`upgrade head` na pustym pliku tworzy pełny schemat."""
    migrate.upgrade_to_head(db_file)
    engine = create_engine("sqlite:///" + db_file.as_posix())
    try:
        tables = set(inspect(engine).get_table_names())
        assert tables.issuperset(EXPECTED_TABLES)
    finally:
        engine.dispose()


def test_compare_metadata_puste_po_upgrade(db_file):
    """Schemat po migracjach jest identyczny z `Base.metadata`."""
    migrate.upgrade_to_head(db_file)
    engine = create_engine("sqlite:///" + db_file.as_posix())
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            assert not compare_metadata(ctx, Base.metadata)
    finally:
        engine.dispose()


def test_downgrade_base_usuwa_tabele(db_file):
    migrate.upgrade_to_head(db_file)
    migrate.downgrade_to_base(db_file)
    engine = create_engine("sqlite:///" + db_file.as_posix())
    try:
        tables = set(inspect(engine).get_table_names())
        assert not (EXPECTED_TABLES & tables)
    finally:
        engine.dispose()


def test_upgrade_head_idempotentne(db_file):
    """Drugi `upgrade head` jest no-op i nie rzuca."""
    migrate.upgrade_to_head(db_file)
    migrate.upgrade_to_head(db_file)
