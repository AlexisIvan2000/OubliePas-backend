from collections import defaultdict
from datetime import date

from repositories.auth_repository import AuthRepository
from repositories.commitment_repository import CommitmentRepository
from services.commitments.occurrence_generator import today_utc
from services.emailing.email_sender import EmailSender


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

    async def _deliver(self, user, entries: list[tuple], reference: date) -> bool:
        items = [
            {
                "title": commitment.title,
                "due_date": occurrence.due_date,
                "amount": occurrence.amount,
                "days_left": (occurrence.due_date - reference).days,
            }
            for occurrence, commitment in entries
        ]
        await self.sender.send_reminder_email(
            user.email,
            first_name=user.first_name,
            items=items,
            currency=user.currency,
        )
        return True

    async def send_due(self, *, on_date: date | None = None) -> dict:
        reference = on_date or today_utc()
        due = await self.repo.due_for_reminder(reference)

        grouped: dict[str, list[tuple]] = defaultdict(list)
        for occurrence, commitment in due:
            grouped[str(occurrence.user_id)].append((occurrence, commitment))

        report = {"users": 0, "occurrences": 0, "skipped": 0, "failed": 0}

        for user_id, entries in grouped.items():
            user = await self.auth_repo.get_user_by_id(user_id)
            if user is None or not user.is_active or not user.is_verified:
                report["skipped"] += len(entries)
                continue

            try:
                await self._deliver(user, entries, reference)
            except Exception:
                report["failed"] += len(entries)
                continue

            await self.repo.mark_reminders_sent([occurrence.id for occurrence, _ in entries])
            await self.repo.session.commit()
            report["users"] += 1
            report["occurrences"] += len(entries)

        return report
