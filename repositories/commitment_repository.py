import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models.db.commitments_db import (
    DEFAULT_REMINDER_CHANNEL,
    MAX_CANCELLATION_NOTICE_DAYS,
    MAX_REMINDER_DAYS,
    OVERDUE_REMINDER_DAYS,
    OVERDUE_REMINDER_WINDOW_DAYS,
    Commitment,
    CommitmentOccurrence,
    OccurrenceReminder,
)
from models.db.user_db import User
from services.notifications.reminder_window import (
    ACTION,
    NOTICE,
    OVERDUE,
    TIMEZONE_SPREAD_DAYS,
    query_bounds,
)


class DueDates(NamedTuple):
    next_due: date | None
    late: date | None


class CommitmentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> Commitment:
        commitment = Commitment(**data)
        self.session.add(commitment)
        await self.session.flush()
        return commitment

    # Un engagement supprime reste en base 30 jours pour rendre l'annulation
    # possible. Toutes les lectures passent par ces deux helpers : oublier le
    # garde-fou sur une requete ferait reapparaitre ses montants dans les totaux.
    def _live(self, user_id: str):
        return select(Commitment).where(
            Commitment.user_id == user_id,
            Commitment.deleted_at.is_(None),
        )

    def _live_occurrences(self, user_id: str, *, columns=None):
        base = select(CommitmentOccurrence) if columns is None else select(*columns)
        return base.select_from(CommitmentOccurrence).join(
            Commitment, Commitment.id == CommitmentOccurrence.commitment_id
        ).where(
            CommitmentOccurrence.user_id == user_id,
            Commitment.deleted_at.is_(None),
        )

    async def get_by_id(self, commitment_id: str, user_id: str) -> Commitment | None:
        result = await self.session.execute(
            self._live(user_id).where(Commitment.id == commitment_id)
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: str,
        *,
        commitment_type: str | None = None,
        status: str | None = None,
    ) -> list[Commitment]:
        query = self._live(user_id)
        if commitment_type is not None:
            query = query.where(Commitment.type == commitment_type)
        if status is not None:
            query = query.where(Commitment.status == status)
        result = await self.session.execute(query.order_by(Commitment.title))
        return list(result.scalars().all())

    async def list_deleted(
        self, user_id: str, *, commitment_type: str | None = None
    ) -> list[Commitment]:
        query = select(Commitment).where(
            Commitment.user_id == user_id,
            Commitment.deleted_at.is_not(None),
        )
        if commitment_type is not None:
            query = query.where(Commitment.type == commitment_type)
        result = await self.session.execute(query.order_by(Commitment.deleted_at.desc()))
        return list(result.scalars().all())

    async def purge_now(self, user_id: str, *, commitment_type: str | None = None) -> int:
        query = delete(Commitment).where(
            Commitment.user_id == user_id,
            Commitment.deleted_at.is_not(None),
        )
        if commitment_type is not None:
            query = query.where(Commitment.type == commitment_type)
        result = await self.session.execute(query)
        await self.session.flush()
        return result.rowcount

    async def active_with_timezone(self) -> list[tuple[Commitment, str]]:
        # Le fuseau voyage avec la ligne : le plancher de generation est celui
        # du proprietaire, et le cron parcourt les engagements de tout le monde
        # en un seul passage. Une seconde requete par utilisateur ferait N+1.
        result = await self.session.execute(
            select(Commitment, User.timezone)
            .join(User, User.id == Commitment.user_id)
            .where(Commitment.status == "active", Commitment.deleted_at.is_(None))
            .order_by(Commitment.created_at)
        )
        return [(commitment, zone) for commitment, zone in result.all()]

    async def update(self, commitment_id: str, user_id: str, data: dict) -> Commitment | None:
        commitment = await self.get_by_id(commitment_id, user_id)
        if commitment is None:
            return None
        for field, value in data.items():
            setattr(commitment, field, value)
        await self.session.flush()
        return commitment

    async def delete(self, commitment_id: str, user_id: str) -> list[uuid.UUID]:
        return await self._soft_delete(
            self._live(user_id).where(Commitment.id == commitment_id)
        )

    async def delete_all(
        self,
        user_id: str,
        *,
        commitment_type: str | None = None,
        status: str | None = None,
    ) -> list[uuid.UUID]:
        query = self._live(user_id)
        if commitment_type is not None:
            query = query.where(Commitment.type == commitment_type)
        if status is not None:
            query = query.where(Commitment.status == status)
        return await self._soft_delete(query)

    async def delete_many(self, user_id: str, ids: list) -> list[uuid.UUID]:
        if not ids:
            return []
        return await self._soft_delete(self._live(user_id).where(Commitment.id.in_(ids)))

    async def _soft_delete(self, query) -> list[uuid.UUID]:
        rows = (await self.session.execute(query)).scalars().all()
        stamp = datetime.now(timezone.utc)
        for commitment in rows:
            commitment.deleted_at = stamp
        await self.session.flush()
        return [commitment.id for commitment in rows]

    async def restore(self, user_id: str, ids: list) -> int:
        if not ids:
            return 0
        result = await self.session.execute(
            update(Commitment)
            .where(
                Commitment.user_id == user_id,
                Commitment.id.in_(ids),
                Commitment.deleted_at.is_not(None),
            )
            .values(deleted_at=None)
        )
        await self.session.flush()
        return result.rowcount

    async def purge_deleted(self, before: datetime) -> int:
        result = await self.session.execute(
            delete(Commitment).where(
                Commitment.deleted_at.is_not(None),
                Commitment.deleted_at < before,
            )
        )
        await self.session.flush()
        return result.rowcount

    async def add_occurrences(self, rows: list[dict]) -> int:
        if not rows:
            return 0

        now = datetime.now(timezone.utc)
        payload = [
            {
                "id": uuid.uuid4(),
                "status": "pending",
                "created_at": now,
                **row,
            }
            for row in rows
        ]
        statement = (
            pg_insert(CommitmentOccurrence)
            .values(payload)
            .on_conflict_do_nothing(constraint="uq_occurrence_commitment_due")
        )
        result = await self.session.execute(statement)
        await self.session.flush()
        return result.rowcount

    async def get_occurrence(
        self, occurrence_id: str, user_id: str
    ) -> CommitmentOccurrence | None:
        result = await self.session.execute(
            self._live_occurrences(user_id)
            .options(selectinload(CommitmentOccurrence.commitment))
            .where(CommitmentOccurrence.id == occurrence_id)
        )
        return result.scalar_one_or_none()

    async def list_occurrences(
        self,
        user_id: str,
        *,
        start: date,
        end: date,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[CommitmentOccurrence]:
        query = (
            self._live_occurrences(user_id)
            .options(selectinload(CommitmentOccurrence.commitment))
            .where(
                CommitmentOccurrence.due_date >= start,
                CommitmentOccurrence.due_date <= end,
            )
        )
        if status is not None:
            query = query.where(CommitmentOccurrence.status == status)
        query = query.order_by(CommitmentOccurrence.due_date)
        if limit is not None:
            query = query.limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def due_dates(self, user_id: str, floor: date) -> dict[uuid.UUID, DueDates]:
        result = await self.session.execute(
            self._live_occurrences(
                user_id,
                columns=(
                    CommitmentOccurrence.commitment_id,
                    func.min(
                        case(
                            (
                                CommitmentOccurrence.due_date >= floor,
                                CommitmentOccurrence.due_date,
                            )
                        )
                    ),
                    func.min(
                        case(
                            (
                                CommitmentOccurrence.due_date < floor,
                                CommitmentOccurrence.due_date,
                            )
                        )
                    ),
                ),
            )
            .where(CommitmentOccurrence.status == "pending")
            .group_by(CommitmentOccurrence.commitment_id)
        )
        return {
            commitment_id: DueDates(next_due=next_due, late=late)
            for commitment_id, next_due, late in result.all()
        }

    async def totals_by_type(self, user_id: str, *, start: date, end: date) -> dict[str, Decimal]:
        result = await self.session.execute(
            self._live_occurrences(
                user_id,
                columns=(
                    Commitment.type,
                    func.coalesce(func.sum(CommitmentOccurrence.amount), 0),
                ),
            )
            .where(
                CommitmentOccurrence.due_date >= start,
                CommitmentOccurrence.due_date <= end,
                CommitmentOccurrence.status != "skipped",
            )
            .group_by(Commitment.type)
        )
        return {row_type: Decimal(total) for row_type, total in result.all()}

    async def totals_by_category(
        self, user_id: str, *, start: date, end: date
    ) -> list[tuple[str, Decimal, int]]:
        total = func.coalesce(func.sum(CommitmentOccurrence.amount), 0)
        result = await self.session.execute(
            self._live_occurrences(
                user_id, columns=(Commitment.category, total, func.count())
            )
            .where(
                CommitmentOccurrence.due_date >= start,
                CommitmentOccurrence.due_date <= end,
                CommitmentOccurrence.status != "skipped",
            )
            .group_by(Commitment.category)
            .order_by(total.desc(), Commitment.category)
        )
        return [(category, Decimal(amount), count) for category, amount, count in result.all()]

    async def totals_by_status(
        self, user_id: str, *, start: date, end: date
    ) -> dict[str, Decimal]:
        result = await self.session.execute(
            self._live_occurrences(
                user_id,
                columns=(
                    CommitmentOccurrence.status,
                    func.coalesce(func.sum(CommitmentOccurrence.amount), 0),
                ),
            )
            .where(
                CommitmentOccurrence.due_date >= start,
                CommitmentOccurrence.due_date <= end,
            )
            .group_by(CommitmentOccurrence.status)
        )
        return {status: Decimal(total) for status, total in result.all()}

    async def list_late(
        self, user_id: str, on_date: date, *, limit: int | None = None
    ) -> list[CommitmentOccurrence]:
        query = (
            self._live_occurrences(user_id)
            .options(selectinload(CommitmentOccurrence.commitment))
            .where(
                CommitmentOccurrence.status == "pending",
                CommitmentOccurrence.due_date < on_date,
            )
            .order_by(CommitmentOccurrence.due_date)
        )
        if limit is not None:
            query = query.limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def count_late(self, user_id: str, on_date: date) -> int:
        result = await self.session.execute(
            self._live_occurrences(user_id, columns=(func.count(),)).where(
                CommitmentOccurrence.status == "pending",
                CommitmentOccurrence.due_date < on_date,
            )
        )
        return result.scalar_one()

    async def count_occurrences(
        self,
        user_id: str,
        *,
        start: date,
        end: date,
        status: str | None = None,
    ) -> int:
        query = self._live_occurrences(user_id, columns=(func.count(),)).where(
            CommitmentOccurrence.due_date >= start,
            CommitmentOccurrence.due_date <= end,
        )
        if status is not None:
            query = query.where(CommitmentOccurrence.status == status)
        result = await self.session.execute(query)
        return result.scalar_one()

    async def count_commitments(
        self,
        user_id: str,
        *,
        statuses: tuple[str, ...] | None = None,
        commitment_type: str | None = None,
    ) -> int:
        query = select(func.count()).select_from(Commitment).where(
            Commitment.user_id == user_id,
            Commitment.deleted_at.is_(None),
        )
        if statuses is not None:
            query = query.where(Commitment.status.in_(statuses))
        if commitment_type is not None:
            query = query.where(Commitment.type == commitment_type)
        result = await self.session.execute(query)
        return result.scalar_one()

    async def delete_pending_occurrences_from(self, commitment_id: str, floor: date) -> int:
        result = await self.session.execute(
            delete(CommitmentOccurrence).where(
                CommitmentOccurrence.commitment_id == commitment_id,
                CommitmentOccurrence.status == "pending",
                CommitmentOccurrence.due_date >= floor,
            )
        )
        await self.session.flush()
        return result.rowcount

    async def set_occurrence_status(
        self,
        occurrence_id: str,
        user_id: str,
        *,
        status: str,
        amount: Decimal | None = None,
        paid_at: datetime | None = None,
        paid_on: date | None = None,
    ) -> CommitmentOccurrence | None:
        occurrence = await self.get_occurrence(occurrence_id, user_id)
        if occurrence is None:
            return None

        occurrence.status = status
        if amount is not None:
            occurrence.amount = amount
        occurrence.paid_at = paid_at
        occurrence.paid_on = paid_on
        await self.session.flush()
        return occurrence

    async def due_between(
        self, start: date, end: date
    ) -> list[tuple[CommitmentOccurrence, Commitment]]:
        # Le recapitulatif ne filtre pas sur is_reminder_enabled, contrairement
        # aux rappels : c'est une image de la semaine, et en retirer une ligne
        # fausserait le total annonce juste en dessous.
        result = await self.session.execute(
            select(CommitmentOccurrence, Commitment)
            .join(Commitment, Commitment.id == CommitmentOccurrence.commitment_id)
            .where(
                CommitmentOccurrence.status == "pending",
                CommitmentOccurrence.due_date >= start,
                CommitmentOccurrence.due_date <= end,
                Commitment.status == "active",
                Commitment.deleted_at.is_(None),
            )
            .order_by(CommitmentOccurrence.due_date)
        )
        return list(result.all())

    def _unreminded(self, kind: str, *, earliest: date, latest: date, channel: str):
        sent = select(OccurrenceReminder.id).where(
            OccurrenceReminder.occurrence_id == CommitmentOccurrence.id,
            OccurrenceReminder.kind == kind,
            OccurrenceReminder.channel == channel,
        )
        return (
            select(CommitmentOccurrence, Commitment)
            .join(Commitment, Commitment.id == CommitmentOccurrence.commitment_id)
            .where(
                CommitmentOccurrence.status == "pending",
                CommitmentOccurrence.due_date >= earliest,
                CommitmentOccurrence.due_date <= latest,
                Commitment.status == "active",
                Commitment.deleted_at.is_(None),
                Commitment.is_reminder_enabled.is_(True),
                ~sent.exists(),
            )
            .order_by(CommitmentOccurrence.due_date)
        )

    def _notice_window(self, on_date: date, channel: str = DEFAULT_REMINDER_CHANNEL):
        # La borne large sert l'index sur (due_date, status) ; la seconde applique
        # le delai propre a chaque engagement. Sans elle, 87 % des lignes remontees
        # etaient hydratees en objets puis jetees.
        earliest, latest = query_bounds(NOTICE, on_date)
        return self._unreminded("notice", earliest=earliest, latest=latest, channel=channel)

    async def due_for_reminder(
        self, on_date: date, *, channel: str = DEFAULT_REMINDER_CHANNEL
    ) -> list[tuple[CommitmentOccurrence, Commitment]]:
        # Le delai de l'engagement, elargi lui aussi : sinon un compte a l'est
        # du serveur serait ecarte avant que sa propre date ait pu decider, et
        # is_due n'aurait plus rien a trancher.
        result = await self.session.execute(
            self._notice_window(on_date, channel).where(
                CommitmentOccurrence.due_date
                <= on_date
                + timedelta(days=TIMEZONE_SPREAD_DAYS)
                + Commitment.reminder_days_before
            )
        )
        return list(result.all())

    async def overdue_for_reminder(
        self, on_date: date, *, channel: str = DEFAULT_REMINDER_CHANNEL
    ) -> list[tuple[CommitmentOccurrence, Commitment]]:
        earliest, latest = query_bounds(OVERDUE, on_date)
        result = await self.session.execute(
            self._unreminded("overdue", earliest=earliest, latest=latest, channel=channel)
        )
        return [(occurrence, commitment) for occurrence, commitment in result.all()]

    async def action_candidates(
        self, on_date: date, *, channel: str = DEFAULT_REMINDER_CHANNEL
    ) -> list[tuple[CommitmentOccurrence, Commitment]]:
        result = await self.session.execute(
            self._unreminded(
                "action_required",
                earliest=query_bounds(ACTION, on_date)[0],
                latest=query_bounds(ACTION, on_date)[1],
                channel=channel,
            ).where(
                or_(
                    Commitment.trial_ends_on.is_not(None),
                    Commitment.cancellation_notice_days.is_not(None),
                )
            )
        )
        return [(occurrence, commitment) for occurrence, commitment in result.all()]

    async def purge_reminders(self, before: date) -> int:
        stale = select(CommitmentOccurrence.id).where(
            CommitmentOccurrence.due_date < before
        )
        result = await self.session.execute(
            delete(OccurrenceReminder).where(
                OccurrenceReminder.occurrence_id.in_(stale)
            )
        )
        await self.session.flush()
        return result.rowcount

    # L'effacement porte sur la famille entiere, tous canaux confondus : rouvrir
    # une echeance doit rendre le rappel possible partout, pas sur un seul canal.
    async def clear_reminders(self, commitment_id: str, *, kind: str) -> int:
        pending = select(CommitmentOccurrence.id).where(
            CommitmentOccurrence.commitment_id == commitment_id,
            CommitmentOccurrence.status == "pending",
        )
        result = await self.session.execute(
            delete(OccurrenceReminder).where(
                OccurrenceReminder.kind == kind,
                OccurrenceReminder.occurrence_id.in_(pending),
            )
        )
        await self.session.flush()
        return result.rowcount

    async def mark_reminders_sent(
        self,
        occurrence_ids: list[uuid.UUID],
        *,
        kind: str,
        channel: str = DEFAULT_REMINDER_CHANNEL,
        sent_at: datetime | None = None,
    ) -> int:
        if not occurrence_ids:
            return 0

        stamp = sent_at or datetime.now(timezone.utc)
        statement = (
            pg_insert(OccurrenceReminder)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "occurrence_id": occurrence_id,
                        "kind": kind,
                        "channel": channel,
                        "sent_at": stamp,
                    }
                    for occurrence_id in occurrence_ids
                ]
            )
            .on_conflict_do_nothing(constraint="uq_reminder_occurrence_kind_channel")
        )
        result = await self.session.execute(statement)
        await self.session.flush()
        return result.rowcount
