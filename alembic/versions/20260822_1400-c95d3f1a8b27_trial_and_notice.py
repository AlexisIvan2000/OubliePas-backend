"""trial and cancellation notice

Revision ID: c95d3f1a8b27
Revises: b41c7a2ef9d3
Create Date: 2026-08-22 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c95d3f1a8b27'
down_revision: Union[str, Sequence[str], None] = 'b41c7a2ef9d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('commitments', sa.Column('trial_ends_on', sa.Date(), nullable=True))
    op.add_column(
        'commitments', sa.Column('cancellation_notice_days', sa.Integer(), nullable=True)
    )
    op.create_check_constraint(
        'commitments_trial_check',
        'commitments',
        'trial_ends_on IS NULL OR trial_ends_on <= starts_on',
    )
    op.create_check_constraint(
        'commitments_notice_check',
        'commitments',
        'cancellation_notice_days IS NULL OR cancellation_notice_days BETWEEN 1 AND 60',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('commitments_notice_check', 'commitments', type_='check')
    op.drop_constraint('commitments_trial_check', 'commitments', type_='check')
    op.drop_column('commitments', 'cancellation_notice_days')
    op.drop_column('commitments', 'trial_ends_on')
