"""locale on users

Revision ID: f39da6b7c518
Revises: e28c9f5d3a42
Create Date: 2026-08-23 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f39da6b7c518'
down_revision: Union[str, Sequence[str], None] = 'e28c9f5d3a42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'users',
        sa.Column('locale', sa.String(length=5), nullable=False, server_default=sa.text("'fr'")),
    )
    op.create_check_constraint("users_locale_check", 'users', "locale IN ('fr', 'en')")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('users_locale_check', 'users', type_='check')
    op.drop_column('users', 'locale')
