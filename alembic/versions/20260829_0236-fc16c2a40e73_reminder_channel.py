"""reminder channel

Revision ID: fc16c2a40e73
Revises: e9a5c1d74b83
Create Date: 2026-08-29 02:36

"""
from alembic import op
import sqlalchemy as sa

revision = "fc16c2a40e73"
down_revision = "e9a5c1d74b83"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default plutot qu'un remplissage en deux temps : la table est
    # petite et Postgres pose une valeur par defaut sans reecrire les lignes.
    op.add_column(
        "occurrence_reminders",
        sa.Column("channel", sa.String(length=10), nullable=False, server_default="email"),
    )
    op.create_check_constraint(
        "reminders_channel_check", "occurrence_reminders", "channel IN ('email', 'push')"
    )
    # L'ancienne contrainte tombe avant la nouvelle : la garder ferait echouer
    # le second canal du meme rappel, ce que toute la passe cherche a permettre.
    op.drop_constraint("uq_reminder_occurrence_kind", "occurrence_reminders", type_="unique")
    op.create_unique_constraint(
        "uq_reminder_occurrence_kind_channel",
        "occurrence_reminders",
        ["occurrence_id", "kind", "channel"],
    )


def downgrade() -> None:
    # Revenir en arriere impose de choisir : deux lignes du meme rappel ne
    # peuvent plus coexister. Les lignes push partent, le courriel fait foi.
    op.execute("DELETE FROM occurrence_reminders WHERE channel <> 'email'")
    op.drop_constraint(
        "uq_reminder_occurrence_kind_channel", "occurrence_reminders", type_="unique"
    )
    op.create_unique_constraint(
        "uq_reminder_occurrence_kind", "occurrence_reminders", ["occurrence_id", "kind"]
    )
    op.drop_constraint("reminders_channel_check", "occurrence_reminders", type_="check")
    op.drop_column("occurrence_reminders", "channel")
