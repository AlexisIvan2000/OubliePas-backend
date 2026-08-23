from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from models.db.commitments_db import (
    DEFAULT_CATEGORY,
    DEFAULT_REMINDER_DAYS,
    MAX_CANCELLATION_NOTICE_DAYS,
    MAX_REMINDER_DAYS,
)

CommitmentType = Literal["subscription", "invoice"]
CommitmentFrequency = Literal["weekly", "monthly", "quarterly", "yearly", "oneoff"]
CommitmentStatus = Literal["active", "paused", "archived"]
OccurrenceStatus = Literal["pending", "paid", "skipped"]

MAX_AMOUNT = Decimal("99999999.99")
MAX_NOTES_LENGTH = 1000
UPCOMING_DAYS = 14
MAX_UPCOMING = 8


def _clean(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Value must not be blank")
    return cleaned


class CommitmentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    type: CommitmentType
    category: str = Field(default=DEFAULT_CATEGORY, min_length=1, max_length=50)
    amount: Decimal = Field(gt=0, le=MAX_AMOUNT, max_digits=10, decimal_places=2)
    frequency: CommitmentFrequency = "monthly"
    starts_on: date
    ends_on: date | None = None
    trial_ends_on: date | None = None
    cancellation_notice_days: int | None = Field(
        default=None, ge=1, le=MAX_CANCELLATION_NOTICE_DAYS
    )
    reminder_days_before: int | None = Field(default=None, ge=0, le=MAX_REMINDER_DAYS)
    is_reminder_enabled: bool = True
    notes: str | None = Field(default=None, max_length=MAX_NOTES_LENGTH)

    @field_validator("title", "category")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return _clean(value)

    @model_validator(mode="after")
    def check_term(self) -> "CommitmentCreate":
        if self.ends_on is not None and self.ends_on < self.starts_on:
            raise ValueError("ends_on must be on or after starts_on")
        if self.trial_ends_on is not None and self.trial_ends_on > self.starts_on:
            raise ValueError("trial_ends_on must be on or before starts_on")
        return self


class CommitmentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    type: CommitmentType | None = None
    category: str | None = Field(default=None, min_length=1, max_length=50)
    amount: Decimal | None = Field(
        default=None, gt=0, le=MAX_AMOUNT, max_digits=10, decimal_places=2
    )
    frequency: CommitmentFrequency | None = None
    starts_on: date | None = None
    ends_on: date | None = None
    trial_ends_on: date | None = None
    cancellation_notice_days: int | None = Field(
        default=None, ge=1, le=MAX_CANCELLATION_NOTICE_DAYS
    )
    reminder_days_before: int | None = Field(default=None, ge=0, le=MAX_REMINDER_DAYS)
    is_reminder_enabled: bool | None = None
    status: CommitmentStatus | None = None
    notes: str | None = Field(default=None, max_length=MAX_NOTES_LENGTH)

    @field_validator("title", "category")
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        return None if value is None else _clean(value)

    @model_validator(mode="after")
    def check_term(self) -> "CommitmentUpdate":
        if (
            self.ends_on is not None
            and self.starts_on is not None
            and self.ends_on < self.starts_on
        ):
            raise ValueError("ends_on must be on or after starts_on")
        if (
            self.trial_ends_on is not None
            and self.starts_on is not None
            and self.trial_ends_on > self.starts_on
        ):
            raise ValueError("trial_ends_on must be on or before starts_on")
        return self


class CommitmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    type: CommitmentType
    category: str
    amount: Decimal
    frequency: CommitmentFrequency
    starts_on: date
    ends_on: date | None
    trial_ends_on: date | None
    cancellation_notice_days: int | None
    reminder_days_before: int
    is_reminder_enabled: bool
    status: CommitmentStatus
    notes: str | None
    next_due_date: date | None = None
    created_at: datetime


class OccurrenceUpdate(BaseModel):
    status: OccurrenceStatus | None = None
    amount: Decimal | None = Field(
        default=None, gt=0, le=MAX_AMOUNT, max_digits=10, decimal_places=2
    )


class OccurrenceResponse(BaseModel):
    id: UUID
    commitment_id: UUID
    title: str
    type: CommitmentType
    category: str
    due_date: date
    amount: Decimal
    status: OccurrenceStatus
    paid_at: datetime | None
    is_late: bool


class CategoryTotal(BaseModel):
    category: str
    total: Decimal
    count: int


class DashboardSummary(BaseModel):
    currency: str
    month: str
    month_total: Decimal
    subscriptions_total: Decimal
    invoices_total: Decimal
    paid_total: Decimal
    pending_total: Decimal
    late_count: int
    active_count: int
    upcoming_days: int
    upcoming_total: int
    upcoming: list[OccurrenceResponse]
    by_category: list[CategoryTotal]
