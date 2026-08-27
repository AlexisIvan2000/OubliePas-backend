"""verification attempts per flow

Revision ID: e9a5c1d74b83
Revises: d8f4b06c3e52
Create Date: 2026-08-27 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e9a5c1d74b83'
down_revision: Union[str, Sequence[str], None] = 'd8f4b06c3e52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'verification_attempts',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'kind', name='uq_verification_attempt_user_kind'),
        sa.CheckConstraint(
            "kind IN ('verification', 'reset', 'email_change')",
            name='verification_attempts_kind_check',
        ),
    )
    op.create_index(
        op.f('ix_verification_attempts_user_id'),
        'verification_attempts',
        ['user_id'],
    )

    # Le compteur unique ne se repartit pas : rien ne dit de quel flux venaient
    # ses essais. On repart de zero plutot que d'inventer une origine, ce qui
    # ouvre au plus cinq essais a des comptes qui en avaient consomme.
    op.drop_column('users', 'verification_attempts')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        'users',
        sa.Column(
            'verification_attempts',
            sa.Integer(),
            server_default=sa.text('0'),
            nullable=False,
        ),
    )
    # Le retour prend le plus grand des trois compteurs : c'est le seul choix
    # qui ne relache aucune garde en revenant en arriere.
    op.execute(
        """
        UPDATE users
        SET verification_attempts = source.count
        FROM (
            SELECT user_id, MAX(count) AS count
            FROM verification_attempts
            GROUP BY user_id
        ) AS source
        WHERE users.id = source.user_id
        """
    )
    op.drop_index(op.f('ix_verification_attempts_user_id'), table_name='verification_attempts')
    op.drop_table('verification_attempts')
