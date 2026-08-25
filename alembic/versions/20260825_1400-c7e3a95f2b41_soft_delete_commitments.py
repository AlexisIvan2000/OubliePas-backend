"""soft delete on commitments

Revision ID: c7e3a95f2b41
Revises: b6d2f8a41c93
Create Date: 2026-08-25 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7e3a95f2b41'
down_revision: Union[str, Sequence[str], None] = 'b6d2f8a41c93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'commitments',
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        'ix_commitments_deleted_at', 'commitments', ['deleted_at'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_commitments_deleted_at', table_name='commitments')
    op.drop_column('commitments', 'deleted_at')
