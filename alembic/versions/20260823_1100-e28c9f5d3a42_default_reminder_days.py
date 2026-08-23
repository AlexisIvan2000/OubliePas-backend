"""default reminder days on users

Revision ID: e28c9f5d3a42
Revises: d17b8e4c2f31
Create Date: 2026-08-23 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e28c9f5d3a42'
down_revision: Union[str, Sequence[str], None] = 'd17b8e4c2f31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'users',
        sa.Column(
            'default_reminder_days',
            sa.Integer(),
            nullable=False,
            server_default=sa.text('3'),
        ),
    )
    op.create_check_constraint(
        'users_reminder_days_check',
        'users',
        'default_reminder_days BETWEEN 0 AND 30',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('users_reminder_days_check', 'users', type_='check')
    op.drop_column('users', 'default_reminder_days')
