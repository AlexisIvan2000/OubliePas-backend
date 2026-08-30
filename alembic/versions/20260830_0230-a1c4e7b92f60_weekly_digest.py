"""weekly digest

Revision ID: a1c4e7b92f60
Revises: 678765e4407c
Create Date: 2026-08-30 02:30

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "a1c4e7b92f60"
down_revision = "678765e4407c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Faux par defaut : le recapitulatif est un envoi de plus, pas un reglage
    # qu'on subit. Vrai aurait ajoute un courriel hebdomadaire a tous les
    # comptes existants sans que personne ne l'ait demande.
    op.add_column(
        "users",
        sa.Column(
            "reminder_weekly_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # Table separee des rappels d'echeance : le sujet est le compte et non
    # l'occurrence, et la clef unique doit porter sur la semaine. Un
    # occurrence_id nullable dans occurrence_reminders aurait desarme sa
    # contrainte, c'est-a-dire la seule chose qui empeche deux envois le meme
    # lundi.
    op.create_table(
        "weekly_digests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column(
            "channel", sa.String(length=10), nullable=False, server_default="email"
        ),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "user_id", "week_start", "channel", name="uq_weekly_digest_user_week_channel"
        ),
        sa.CheckConstraint(
            "channel IN ('email', 'push')", name="weekly_digests_channel_check"
        ),
    )
    op.create_index("ix_weekly_digests_user_id", "weekly_digests", ["user_id"])
    op.create_index("ix_weekly_digests_week_start", "weekly_digests", ["week_start"])


def downgrade() -> None:
    op.drop_index("ix_weekly_digests_week_start", table_name="weekly_digests")
    op.drop_index("ix_weekly_digests_user_id", table_name="weekly_digests")
    op.drop_table("weekly_digests")
    op.drop_column("users", "reminder_weekly_enabled")
