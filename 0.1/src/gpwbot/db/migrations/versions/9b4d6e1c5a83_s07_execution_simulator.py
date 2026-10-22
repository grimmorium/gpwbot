"""s07 execution simulator

Revision ID: 9b4d6e1c5a83
Revises: a3f5c8e2b9d4
Create Date: 2026-10-22 09:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9b4d6e1c5a83'
down_revision: Union[str, None] = 'a3f5c8e2b9d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """upgrade."""
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('processed_session', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('reason', sa.Text(), nullable=True))


def downgrade() -> None:
    """Cofa rewizję."""
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_column('reason')
        batch_op.drop_column('processed_session')
