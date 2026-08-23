from collections import defaultdict
from datetime import date

from repositories.auth_repository import AuthRepository
from repositories.commitment_repository import CommitmentRepository
from services.commitments.occurrence_generator import today_utc
from services.emailing.email_sender import EmailSender

NOTICE = "notice"
OVERDUE = "overdue"


class ReminderService:
    def __init__(
        self,
        repo: CommitmentRepository,
        auth_repo: AuthRepository,
        sender: EmailSender,
    ):
        self.repo = repo
        self.auth_repo = auth_repo
        self.sender = sender

    def _items(self, entries: list[tuple], reference: date) -> list[dict]:
        return [
            {
                "title": commitment.title,
                "due_date": occurrence.due_date,
                "amount": occurrence.amount,
                "days_left": (occurrence.due_date - reference).days,
            }
            for occurrence, commitment in entries
        ]

    async def _deliver(self, kind: str, user, entries: list[tuple], reference: date) -> None:
        send = (
            self.sender.send_reminder_email
            if kind == NOTICE
            else self.sender.send_overdue_email
        )
        await send(
            user.email,
            first_name=user.first_name,
            items=self._items(entries, reference),
            currency=user.currency,
        )

    async def _dispatch(
        self,
        kind: str,
        entries: list[tuple],
        *,
        reference: date,
        counter: str,
        report: dict,
        emailed: set,
    ) -> None:
        grouped: dict[str, list[tuple]] = defaultdict(list)
        for occurrence, commitment in entries:
            grouped[str(occurrence.user_id)].append((occurrence, commitment))

        for user_id, rows in grouped.items():
            user = await self.auth_repo.get_user_by_id(user_id)
            if user is None or not user.is_active or not user.is_verified:
                report["skipped"] += len(rows)
                continue

            try:
                await self._deliver(kind, user, rows, reference)
            except Exception:
                report["failed"] += len(rows)
                continue

            await self.repo.mark_reminders_sent(
                [occurrence.id for occurrence, _ in rows], kind=kind
            )
            await self.repo.session.commit()
            emailed.add(user_id)
            report[counter] += len(rows)

    async def send_due(self, *, on_date: date | None = None) -> dict:
        reference = on_date or today_utc()
        report = {"users": 0, "occurrences": 0, "overdue": 0, "skipped": 0, "failed": 0}
        emailed: set[str] = set()

        await self._dispatch(
            NOTICE,
            await self.repo.due_for_reminder(reference),
            reference=reference,
            counter="occurrences",
            report=report,
            emailed=emailed,
        )
        await self._dispatch(
            OVERDUE,
            await self.repo.overdue_for_reminder(reference),
            reference=reference,
            counter="overdue",
            report=report,
            emailed=emailed,
        )

        report["users"] = len(emailed)
        return report
