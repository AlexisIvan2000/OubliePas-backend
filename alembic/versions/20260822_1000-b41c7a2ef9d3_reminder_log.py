"""reminder log

Revision ID: b41c7a2ef9d3
Revises: 754e4e5c567c
Create Date: 2026-08-22 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b41c7a2ef9d3'
down_revision: Union[str, Sequence[str], None] = '754e4e5c567c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'occurrence_reminders',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('occurrence_id', sa.UUID(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('notice', 'overdue', 'action_required')",
            name='reminders_kind_check',
        ),
        sa.ForeignKeyConstraint(
            ['occurrence_id'], ['commitment_occurrences.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('occurrence_id', 'kind', name='uq_reminder_occurrence_kind'),
    )
    op.create_index(
        op.f('ix_occurrence_reminders_occurrence_id'),
        'occurrence_reminders',
        ['occurrence_id'],
        unique=False,
    )

    op.execute(
        """
        INSERT INTO occurrence_reminders (id, occurrence_id, kind, sent_at)
        SELECT gen_random_uuid(), id, 'notice', reminder_sent_at
        FROM commitment_occurrences
        WHERE reminder_sent_at IS NOT NULL
        """
    )

    op.drop_index('ix_occurrences_reminder', table_name='commitment_occurrences')
    op.drop_column('commitment_occurrences', 'reminder_sent_at')
    op.create_index(
        'ix_occurrences_due_status',
        'commitment_occurrences',
        ['due_date', 'status'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        'commitment_occurrences',
        sa.Column('reminder_sent_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE commitment_occurrences AS o
        SET reminder_sent_at = r.sent_at
        FROM occurrence_reminders AS r
        WHERE r.occurrence_id = o.id AND r.kind = 'notice'
        """
    )

    op.drop_index('ix_occurrences_due_status', table_name='commitment_occurrences')
    op.create_index(
        'ix_occurrences_reminder',
        'commitment_occurrences',
        ['due_date'],
        unique=False,
        postgresql_where=sa.text("status = 'pending' AND reminder_sent_at IS NULL"),
    )

    op.drop_index(
        op.f('ix_occurrence_reminders_occurrence_id'), table_name='occurrence_reminders'
    )
    op.drop_table('occurrence_reminders')
