import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.db.base import Base

COMMITMENT_TYPES = ("subscription", "invoice")
COMMITMENT_FREQUENCIES = ("weekly", "monthly", "quarterly", "yearly", "oneoff")
COMMITMENT_STATUSES = ("active", "paused", "archived")
OCCURRENCE_STATUSES = ("pending", "paid", "skipped")
REMINDER_KINDS = ("notice", "overdue", "action_required")

DEFAULT_CATEGORY = "other"
DEFAULT_REMINDER_DAYS = 3
MAX_REMINDER_DAYS = 30
OVERDUE_REMINDER_DAYS = 3
OVERDUE_REMINDER_WINDOW_DAYS = 30
MIN_TRIAL_NOTICE_DAYS = 3
PURGE_AFTER_DAYS = 30
MAX_CANCELLATION_NOTICE_DAYS = 60


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class Commitment(Base):
    __tablename__ = "commitments"
    __table_args__ = (
        CheckConstraint(_in_clause("type", COMMITMENT_TYPES), name="commitments_type_check"),
        CheckConstraint(
            _in_clause("frequency", COMMITMENT_FREQUENCIES), name="commitments_frequency_check"
        ),
        CheckConstraint(_in_clause("status", COMMITMENT_STATUSES), name="commitments_status_check"),
        CheckConstraint("amount > 0", name="commitments_amount_check"),
        CheckConstraint(
            f"reminder_days_before BETWEEN 0 AND {MAX_REMINDER_DAYS}",
            name="commitments_reminder_days_check",
        ),
        CheckConstraint("ends_on IS NULL OR ends_on >= starts_on", name="commitments_dates_check"),
        CheckConstraint(
            "trial_ends_on IS NULL OR trial_ends_on <= starts_on",
            name="commitments_trial_check",
        ),
        CheckConstraint(
            "cancellation_notice_days IS NULL OR cancellation_notice_days BETWEEN 1 AND "
            f"{MAX_CANCELLATION_NOTICE_DAYS}",
            name="commitments_notice_check",
        ),
        Index("ix_commitments_user_status", "user_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(20), index=True)
    category: Mapped[str] = mapped_column(String(50), default=DEFAULT_CATEGORY)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    frequency: Mapped[str] = mapped_column(String(20), default="monthly")
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    trial_ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    cancellation_notice_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reminder_days_before: Mapped[int] = mapped_column(Integer, default=DEFAULT_REMINDER_DAYS)
    is_reminder_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    occurrences: Mapped[list["CommitmentOccurrence"]] = relationship(
        back_populates="commitment", cascade="all, delete-orphan"
    )


class CommitmentOccurrence(Base):
    __tablename__ = "commitment_occurrences"
    __table_args__ = (
        UniqueConstraint("commitment_id", "due_date", name="uq_occurrence_commitment_due"),
        CheckConstraint(_in_clause("status", OCCURRENCE_STATUSES), name="occurrences_status_check"),
        CheckConstraint("amount > 0", name="occurrences_amount_check"),
        Index("ix_occurrences_due_status", "due_date", "status"),
        Index("ix_occurrences_user_due", "user_id", "due_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    commitment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("commitments.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    due_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    commitment: Mapped["Commitment"] = relationship(back_populates="occurrences")
    reminders: Mapped[list["OccurrenceReminder"]] = relationship(
        back_populates="occurrence", cascade="all, delete-orphan"
    )


class OccurrenceReminder(Base):
    __tablename__ = "occurrence_reminders"
    __table_args__ = (
        UniqueConstraint("occurrence_id", "kind", name="uq_reminder_occurrence_kind"),
        CheckConstraint(_in_clause("kind", REMINDER_KINDS), name="reminders_kind_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    occurrence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("commitment_occurrences.id", ondelete="CASCADE"),
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(20))
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    occurrence: Mapped["CommitmentOccurrence"] = relationship(back_populates="reminders")
