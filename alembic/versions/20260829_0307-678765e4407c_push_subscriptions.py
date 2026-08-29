"""push subscriptions

Revision ID: 678765e4407c
Revises: fc16c2a40e73
Create Date: 2026-08-29 03:07

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "678765e4407c"
down_revision = "fc16c2a40e73"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Faux par defaut : un push exige une permission navigateur que personne
    # n'a encore donnee. Vrai aurait promis des notifications qui n'arrivent
    # pas, et coupe le seul canal fiable de personne.
    op.add_column(
        "users",
        sa.Column(
            "reminder_push_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    op.create_table(
        "push_subscriptions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.String(length=255), nullable=False),
        sa.Column("auth", sa.String(length=255), nullable=False),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # L'unicite porte l'upsert : le navigateur reconduit la meme adresse a
        # chaque reabonnement, et un appareil peut changer de compte.
        sa.UniqueConstraint("endpoint", name="uq_push_subscription_endpoint"),
    )
    op.create_index("ix_push_subscriptions_user_id", "push_subscriptions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_push_subscriptions_user_id", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
    op.drop_column("users", "reminder_push_enabled")
