"""s06 ledger fifo

Revision ID: a3f5c8e2b9d4
Revises: 33eb8be5bb07
Create Date: 2026-10-21 09:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import gpwbot.db.types  # noqa: F401 — autogenerate nie emituje importów typów własnych (D-010)


# revision identifiers, used by Alembic.
revision: str = 'a3f5c8e2b9d4'
down_revision: Union[str, None] = '33eb8be5bb07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """upgrade."""
    op.create_table('position_meta',
    sa.Column('portfolio_id', sa.Integer(), nullable=False),
    sa.Column('instrument_id', sa.Integer(), nullable=False),
    sa.Column('stop_price', gpwbot.db.types.DecimalStr(), nullable=True),
    sa.Column('entry_atr', gpwbot.db.types.DecimalStr(), nullable=True),
    sa.Column('opened_session', sa.Date(), nullable=True),
    sa.Column('last_sell_session', sa.Date(), nullable=True),
    sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id'], ),
    sa.ForeignKeyConstraint(['portfolio_id'], ['portfolios.id'], ),
    sa.PrimaryKeyConstraint('portfolio_id', 'instrument_id')
    )
    op.create_table('corporate_actions',
    sa.Column('instrument_id', sa.Integer(), nullable=False),
    sa.Column('type', sa.String(length=16), nullable=False),
    sa.Column('ex_date', sa.Date(), nullable=False),
    sa.Column('pay_date', sa.Date(), nullable=True),
    sa.Column('value', gpwbot.db.types.DecimalStr(), nullable=False),
    sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id'], ),
    sa.PrimaryKeyConstraint('instrument_id', 'type', 'ex_date')
    )
    with op.batch_alter_table('fills', schema=None) as batch_op:
        batch_op.add_column(sa.Column('realized_pnl', gpwbot.db.types.DecimalStr(), nullable=True))

    with op.batch_alter_table('cash_ledger', schema=None) as batch_op:
        batch_op.add_column(sa.Column('available_from', sa.Date(), nullable=True))

    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('reserved_amount', gpwbot.db.types.DecimalStr(), nullable=True))


def downgrade() -> None:
    """Cofa rewizję."""
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_column('reserved_amount')

    with op.batch_alter_table('cash_ledger', schema=None) as batch_op:
        batch_op.drop_column('available_from')

    with op.batch_alter_table('fills', schema=None) as batch_op:
        batch_op.drop_column('realized_pnl')

    op.drop_table('corporate_actions')
    op.drop_table('position_meta')
