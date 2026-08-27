import calendar
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple
from decimal import Decimal

from core.exceptions import (
    CommitmentLimitReached,
    CommitmentNotFound,
    FuturePaymentDate,
    InvalidDateRange,
    NoFieldsToUpdate,
    OccurrenceNotFound,
)
from models.db.commitments_db import (
    COUNTED_STATUSES,
    DEFAULT_REMINDER_DAYS,
    MAX_COMMITMENTS_PER_TYPE,
    PURGE_AFTER_DAYS,
    Commitment,
    CommitmentOccurrence,
)
from models.schemas.commitment_schema import (
    MAX_UPCOMING,
    UPCOMING_DAYS,
    CategoryTotal,
    CommitmentCreate,
    CommitmentResponse,
    CommitmentUpdate,
    DashboardSummary,
    OccurrenceResponse,
    OccurrenceUpdate,
)
from repositories.commitment_repository import CommitmentRepository, DueDates
from services.commitments.occurrence_generator import OccurrenceGenerator, today_utc

RESCHEDULING_FIELDS = frozenset({"amount", "frequency", "starts_on", "ends_on", "status"})
CLEARABLE_FIELDS = frozenset(
    {"ends_on", "notes", "trial_ends_on", "cancellation_notice_days"}
)
ACTION_FIELDS = frozenset({"trial_ends_on", "cancellation_notice_days"})
MAX_RANGE_DAYS = 400
MAX_LATE_ROWS = 50


def purge_on(deleted_at: datetime | None) -> date | None:
    if deleted_at is None:
        return None
    return (deleted_at + timedelta(days=PURGE_AFTER_DAYS)).date()
CENTS = Decimal("0.01")


class Settlement(NamedTuple):
    at: datetime | None
    on: date | None


def settle(occurrence, *, status: str, paid_on: date | None, today: date) -> Settlement:
    if status != "paid":
        return Settlement(None, None)
    when = paid_on or today
    if when > today:
        raise FuturePaymentDate()
    return Settlement(occurrence.paid_at or datetime.now(timezone.utc), when)


def money(value: Decimal) -> Decimal:
    return value.quantize(CENTS)


def month_bounds(reference: date) -> tuple[date, date]:
    last_day = calendar.monthrange(reference.year, reference.month)[1]
    return date(reference.year, reference.month, 1), date(
        reference.year, reference.month, last_day
    )


class CommitmentService:
    def __init__(self, repo: CommitmentRepository, generator: OccurrenceGenerator):
        self.repo = repo
        self.generator = generator

    def _occurrence_response(
        self, occurrence: CommitmentOccurrence, today: date
    ) -> OccurrenceResponse:
        commitment = occurrence.commitment
        return OccurrenceResponse(
            id=occurrence.id,
            commitment_id=occurrence.commitment_id,
            title=commitment.title,
            type=commitment.type,
            category=commitment.category,
            due_date=occurrence.due_date,
            amount=occurrence.amount,
            status=occurrence.status,
            paid_at=occurrence.paid_at,
            paid_on=occurrence.paid_on,
            is_late=occurrence.status == "pending" and occurrence.due_date < today,
        )

    def _commitment_response(
        self, commitment: Commitment, due: DueDates | None
    ) -> CommitmentResponse:
        return CommitmentResponse.model_validate(commitment).model_copy(
            update={
                "next_due_date": due.next_due if due else None,
                "late_due_date": due.late if due else None,
                "purge_on": purge_on(commitment.deleted_at),
            }
        )

    async def _owned(self, user_id: str, commitment_id) -> Commitment:
        commitment = await self.repo.get_by_id(commitment_id, user_id)
        if commitment is None:
            raise CommitmentNotFound()
        return commitment

    async def create(
        self,
        user_id: str,
        payload: CommitmentCreate,
        *,
        default_reminder_days: int = DEFAULT_REMINDER_DAYS,
    ) -> CommitmentResponse:
        await self._guard_limit(user_id, payload.type)
        today = today_utc()
        fields = payload.model_dump()
        if fields["reminder_days_before"] is None:
            fields["reminder_days_before"] = default_reminder_days
        commitment = await self.repo.create({"user_id": user_id, **fields})
        await self.generator.sync(commitment, today=today)
        due = await self.repo.due_dates(user_id, today)
        return self._commitment_response(commitment, due.get(commitment.id))

    async def _guard_limit(self, user_id: str, commitment_type: str) -> None:
        tracked = await self.repo.count_commitments(
            user_id, statuses=COUNTED_STATUSES, commitment_type=commitment_type
        )
        if tracked >= MAX_COMMITMENTS_PER_TYPE:
            raise CommitmentLimitReached(commitment_type, MAX_COMMITMENTS_PER_TYPE)

    async def _guard_entry(self, user_id: str, commitment, changes: dict) -> None:
        # Desarchiver et changer de type font entrer une ligne dans la population
        # comptee sans passer par la creation : sans cette garde, le plafond ne
        # tiendrait que le chemin normal.
        target_type = changes.get("type", commitment.type)
        target_status = changes.get("status", commitment.status)
        if target_status not in COUNTED_STATUSES:
            return
        if commitment.status in COUNTED_STATUSES and commitment.type == target_type:
            return
        await self._guard_limit(user_id, target_type)

    async def list_commitments(
        self,
        user_id: str,
        *,
        commitment_type: str | None = None,
        status: str | None = None,
    ) -> list[CommitmentResponse]:
        commitments = await self.repo.list_for_user(
            user_id, commitment_type=commitment_type, status=status
        )
        due = await self.repo.due_dates(user_id, today_utc())
        return [
            self._commitment_response(commitment, due.get(commitment.id))
            for commitment in commitments
        ]

    async def get(self, user_id: str, commitment_id) -> CommitmentResponse:
        commitment = await self._owned(user_id, commitment_id)
        due = await self.repo.due_dates(user_id, today_utc())
        return self._commitment_response(commitment, due.get(commitment.id))

    async def update(
        self, user_id: str, commitment_id, payload: CommitmentUpdate
    ) -> CommitmentResponse:
        changes = {
            field: value
            for field, value in payload.model_dump(exclude_unset=True).items()
            if value is not None or field in CLEARABLE_FIELDS
        }
        if not changes:
            raise NoFieldsToUpdate()

        commitment = await self._owned(user_id, commitment_id)

        starts_on = changes.get("starts_on", commitment.starts_on)
        ends_on = changes.get("ends_on", commitment.ends_on)
        if ends_on is not None and ends_on < starts_on:
            raise InvalidDateRange()

        trial_ends_on = changes.get("trial_ends_on", commitment.trial_ends_on)
        if trial_ends_on is not None and trial_ends_on > starts_on:
            raise InvalidDateRange("The trial must end on or before the first due date")

        await self._guard_entry(user_id, commitment, changes)

        today = today_utc()
        updated = await self.repo.update(commitment_id, user_id, changes)
        if RESCHEDULING_FIELDS & changes.keys():
            await self.generator.resync(updated, today=today)
        elif ACTION_FIELDS & changes.keys():
            await self.repo.clear_reminders(commitment_id, kind="action_required")

        due = await self.repo.due_dates(user_id, today)
        return self._commitment_response(updated, due.get(updated.id))

    async def delete(self, user_id: str, commitment_id) -> dict:
        await self._owned(user_id, commitment_id)
        removed = await self.repo.delete(commitment_id, user_id)
        return {"deleted": len(removed), "ids": [str(one) for one in removed]}

    async def delete_all(
        self,
        user_id: str,
        *,
        commitment_type: str | None = None,
        status: str | None = None,
    ) -> dict:
        removed = await self.repo.delete_all(
            user_id, commitment_type=commitment_type, status=status
        )
        return {"deleted": len(removed), "ids": [str(one) for one in removed]}

    async def restore(self, user_id: str, ids: list) -> dict:
        return {"restored": await self.repo.restore(user_id, ids)}

    async def list_deleted(
        self, user_id: str, *, commitment_type: str | None = None
    ) -> list[CommitmentResponse]:
        commitments = await self.repo.list_deleted(user_id, commitment_type=commitment_type)
        return [self._commitment_response(commitment, None) for commitment in commitments]

    async def purge_now(self, user_id: str, *, commitment_type: str | None = None) -> dict:
        return {"deleted": await self.repo.purge_now(user_id, commitment_type=commitment_type)}

    async def list_occurrences(
        self,
        user_id: str,
        *,
        start: date,
        end: date,
        status: str | None = None,
    ) -> list[OccurrenceResponse]:
        if end < start:
            raise InvalidDateRange()
        if (end - start).days > MAX_RANGE_DAYS:
            raise InvalidDateRange(
                f"The range must not exceed {MAX_RANGE_DAYS} days"
            )

        today = today_utc()
        occurrences = await self.repo.list_occurrences(
            user_id, start=start, end=end, status=status
        )
        return [self._occurrence_response(occurrence, today) for occurrence in occurrences]

    async def list_late(self, user_id: str) -> list[OccurrenceResponse]:
        today = today_utc()
        occurrences = await self.repo.list_late(user_id, today, limit=MAX_LATE_ROWS)
        return [self._occurrence_response(occurrence, today) for occurrence in occurrences]

    async def update_occurrence(
        self, user_id: str, occurrence_id, payload: OccurrenceUpdate
    ) -> OccurrenceResponse:
        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            raise NoFieldsToUpdate()

        occurrence = await self.repo.get_occurrence(occurrence_id, user_id)
        if occurrence is None:
            raise OccurrenceNotFound()

        today = today_utc()
        status = changes.get("status", occurrence.status)
        settlement = settle(
            occurrence,
            status=status,
            paid_on=changes.get("paid_on", occurrence.paid_on),
            today=today,
        )
        updated = await self.repo.set_occurrence_status(
            occurrence_id,
            user_id,
            status=status,
            amount=changes.get("amount"),
            paid_at=settlement.at,
            paid_on=settlement.on,
        )
        return self._occurrence_response(updated, today)

    async def summary(self, user_id: str, currency: str) -> DashboardSummary:
        today = today_utc()
        start, end = month_bounds(today)

        by_type = await self.repo.totals_by_type(user_id, start=start, end=end)
        by_status = await self.repo.totals_by_status(user_id, start=start, end=end)
        by_category = await self.repo.totals_by_category(user_id, start=start, end=end)
        subscriptions = by_type.get("subscription", Decimal("0"))
        invoices = by_type.get("invoice", Decimal("0"))

        horizon = today + timedelta(days=UPCOMING_DAYS)
        upcoming = await self.repo.list_occurrences(
            user_id,
            start=today,
            end=horizon,
            status="pending",
            limit=MAX_UPCOMING,
        )
        upcoming_total = await self.repo.count_occurrences(
            user_id, start=today, end=horizon, status="pending"
        )

        return DashboardSummary(
            currency=currency,
            month=f"{today.year:04d}-{today.month:02d}",
            month_total=money(subscriptions + invoices),
            subscriptions_total=money(subscriptions),
            invoices_total=money(invoices),
            paid_total=money(by_status.get("paid", Decimal("0"))),
            pending_total=money(by_status.get("pending", Decimal("0"))),
            late_count=await self.repo.count_late(user_id, today),
            active_count=await self.repo.count_commitments(user_id, statuses=("active",)),
            upcoming_days=UPCOMING_DAYS,
            upcoming_total=upcoming_total,
            upcoming=[self._occurrence_response(row, today) for row in upcoming],
            by_category=[
                CategoryTotal(category=category, total=money(total), count=count)
                for category, total, count in by_category
            ],
        )
