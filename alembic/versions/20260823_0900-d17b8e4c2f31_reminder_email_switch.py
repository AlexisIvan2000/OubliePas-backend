"""reminder email switch on users

Revision ID: d17b8e4c2f31
Revises: c95d3f1a8b27
Create Date: 2026-08-23 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd17b8e4c2f31'
down_revision: Union[str, Sequence[str], None] = 'c95d3f1a8b27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'users',
        sa.Column(
            'reminder_email_enabled',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('true'),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'reminder_email_enabled')
