"""fuseau horaire de l'utilisateur

Revision ID: 4c86c90096f8
Revises: a1c4e7b92f60
Create Date: 2026-09-01 00:34:56.310960

L'autogeneration proposait aussi de retirer le server_default 'now()' de
push_subscriptions.created_at, push_subscriptions.last_seen_at et
weekly_digests.sent_at : une derive anterieure entre la base et les modeles,
qui declarent ces valeurs cote Python. Elle est laissee telle quelle. Une
migration qui ajoute un fuseau n'a pas a modifier trois colonnes de dates au
passage, et retirer un defaut serveur casserait toute insertion qui s'y fie.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '4c86c90096f8'
down_revision: Union[str, Sequence[str], None] = 'a1c4e7b92f60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # UTC pour l'existant : c'est ce que le service calculait deja pour tout le
    # monde, donc personne ne voit son mois changer a la migration. Les comptes
    # se corrigent a leur prochaine connexion, quand le navigateur dit le sien.
    op.add_column(
        'users',
        sa.Column(
            'timezone',
            sa.String(length=64),
            server_default=sa.text("'UTC'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'timezone')
