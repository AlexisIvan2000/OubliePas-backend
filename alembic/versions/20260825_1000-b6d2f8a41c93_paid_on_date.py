"""real payment date on occurrences

Revision ID: b6d2f8a41c93
Revises: a4c7e1b92d05
Create Date: 2026-08-25 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6d2f8a41c93'
down_revision: Union[str, Sequence[str], None] = 'a4c7e1b92d05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'commitment_occurrences',
        sa.Column('paid_on', sa.Date(), nullable=True),
    )
    op.execute(
        "update commitment_occurrences "
        "set paid_on = (paid_at at time zone 'UTC')::date "
        "where status = 'paid' and paid_at is not null"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('commitment_occurrences', 'paid_on')
