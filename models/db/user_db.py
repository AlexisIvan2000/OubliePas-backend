import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.db.base import Base
from models.db.commitments_db import DEFAULT_REMINDER_DAYS, MAX_REMINDER_DAYS

LOCALES = ("fr", "en")
DEFAULT_LOCALE = "fr"

MAX_VERIFICATION_ATTEMPTS = 5
VERIFICATION_KINDS = ("verification", "reset", "email_change")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'admin', 'super_admin')", name="users_role_check"),
        CheckConstraint(
            f"default_reminder_days BETWEEN 0 AND {MAX_REMINDER_DAYS}",
            name="users_reminder_days_check",
        ),
        CheckConstraint(
            "locale IN (" + ", ".join(repr(code) for code in LOCALES) + ")",
            name="users_locale_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100), nullable=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_code_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    verification_code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pending_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reset_code_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reset_code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email_change_code_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_change_code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_code_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    code_resend_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_login_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sa.text("0")
    )
    last_failed_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="CAD")
    reminder_email_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sa.text("true")
    )
    # Distinct du courriel : couper les notifications ne doit pas couper les
    # rappels, et le defaut est faux parce qu'un push exige une permission
    # navigateur que personne n'a encore donnee.
    reminder_push_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sa.text("false")
    )
    reminder_notice_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sa.text("true")
    )
    reminder_overdue_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sa.text("true")
    )
    reminder_action_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sa.text("true")
    )
    # Faux par defaut : le recapitulatif est un envoi de plus, pas un reglage
    # qu'on subit. Ceux qui le veulent le demandent.
    reminder_weekly_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sa.text("false")
    )
    default_reminder_days: Mapped[int] = mapped_column(
        Integer,
        default=DEFAULT_REMINDER_DAYS,
        server_default=sa.text(str(DEFAULT_REMINDER_DAYS)),
    )
    locale: Mapped[str] = mapped_column(
        String(5),
        default=DEFAULT_LOCALE,
        server_default=sa.text(f"'{DEFAULT_LOCALE}'"),
    )
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Rôle pour le dashboard admin (user = utilisateur normal, admin/super_admin = accès /v1/admin/*)
    role: Mapped[str] = mapped_column(String(20), server_default="user", default="user", index=True)

    # État du compte : is_active=false empêche le login (équivalent ban)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa.text("true"), index=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Notes internes admin, jamais exposées au user, visibles uniquement dans /v1/admin/users/{id}
    admin_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    push_subscriptions: Mapped[list["PushSubscription"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    device_info: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user: Mapped["User"] = relationship(back_populates="refresh_tokens")

class VerificationAttempt(Base):
    __tablename__ = "verification_attempts"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", name="uq_verification_attempt_user_kind"),
        CheckConstraint(
            "kind IN (" + ", ".join(repr(code) for code in VERIFICATION_KINDS) + ")",
            name="verification_attempts_kind_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    count: Mapped[int] = mapped_column(Integer, default=0, server_default=sa.text("0"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


# Un telephone, une tablette, deux ou trois navigateurs de bureau : les
# appareils reels d'une personne tiennent largement dessous. Sans borne, un
# compte pouvait enregistrer trente adresses par heure sans fin, et le cron
# postait a chacune une fois par jour.
MAX_PUSH_SUBSCRIPTIONS_PER_USER = 10


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # L'adresse d'envoi identifie l'abonnement a elle seule, et le navigateur la
    # reconduit telle quelle : elle sert de cle d'upsert plutot qu'un couple
    # (utilisateur, appareil) qu'aucun des deux cotes ne sait fabriquer.
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(String(255))
    auth: Mapped[str] = mapped_column(String(255))
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user: Mapped["User"] = relationship(back_populates="push_subscriptions")
