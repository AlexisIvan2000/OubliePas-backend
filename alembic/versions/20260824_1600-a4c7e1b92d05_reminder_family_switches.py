"""reminder family switches on users

Revision ID: a4c7e1b92d05
Revises: f39da6b7c518
Create Date: 2026-08-24 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4c7e1b92d05'
down_revision: Union[str, Sequence[str], None] = 'f39da6b7c518'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (
    'reminder_notice_enabled',
    'reminder_overdue_enabled',
    'reminder_action_enabled',
)


def upgrade() -> None:
    """Upgrade schema."""
    for name in COLUMNS:
        op.add_column(
            'users',
            sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.text('true')),
        )


def downgrade() -> None:
    """Downgrade schema."""
    for name in reversed(COLUMNS):
        op.drop_column('users', name)
