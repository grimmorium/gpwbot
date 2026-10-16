"""Środowisko migracji Alembic (S01).

`target_metadata` = `Base.metadata` z modeli; SQLite wymaga trybu batch
dla przyszłych zmian kolumn. URL bazy podaje wywołujący (`gpwbot.db.migrate`).
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from gpwbot.db.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Tryb offline: SQL do stdout (bez połączenia)."""
    url = config.get_main_option("sqlalchemy.url") or "sqlite://"
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Tryb online: migracje na połączeniu do bazy."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
