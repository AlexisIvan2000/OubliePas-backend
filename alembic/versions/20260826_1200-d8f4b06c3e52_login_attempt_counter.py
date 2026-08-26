"""per-account login attempt counter

Revision ID: d8f4b06c3e52
Revises: c7e3a95f2b41
Create Date: 2026-08-26 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8f4b06c3e52'
down_revision: Union[str, Sequence[str], None] = 'c7e3a95f2b41'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'users',
        sa.Column(
            'failed_login_count',
            sa.Integer(),
            server_default=sa.text('0'),
            nullable=False,
        ),
    )
    op.add_column(
        'users',
        sa.Column('last_failed_login_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'last_failed_login_at')
    op.drop_column('users', 'failed_login_count')
