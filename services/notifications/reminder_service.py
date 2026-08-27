import logging
from collections import defaultdict
from datetime import date

from repositories.auth_repository import AuthRepository
from repositories.commitment_repository import CommitmentRepository
from services.commitments.action_window import TRIAL, action_window
from services.commitments.occurrence_generator import today_utc
from services.emailing.email_sender import EmailSender

logger = logging.getLogger(__name__)

NOTICE = "notice"
OVERDUE = "overdue"
ACTION = "action_required"

FAMILY_SWITCH = {
    NOTICE: "reminder_notice_enabled",
    OVERDUE: "reminder_overdue_enabled",
    ACTION: "reminder_action_enabled",
}


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

    def _items(self, kind: str, entries: list[tuple], reference: date) -> list[dict]:
        rows = []
        for entry in entries:
            occurrence, commitment = entry[0], entry[1]
            item = {
                "title": commitment.title,
                "due_date": occurrence.due_date,
                "amount": occurrence.amount,
                "days_left": (occurrence.due_date - reference).days,
            }
            if kind == ACTION:
                window = entry[2]
                item["deadline"] = window.deadline
                item["reason"] = window.reason
                item["days_left"] = window.days_left(reference)
            rows.append(item)
        return rows

    async def _deliver(self, kind: str, user, entries: list[tuple], reference: date) -> None:
        senders = {
            NOTICE: self.sender.send_reminder_email,
            OVERDUE: self.sender.send_overdue_email,
            ACTION: self.sender.send_action_email,
        }
        await senders[kind](
            user.email,
            first_name=user.first_name,
            items=self._items(kind, entries, reference),
            currency=user.currency,
            locale=user.locale,
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
        for entry in entries:
            grouped[str(entry[0].user_id)].append(entry)

        for user_id, rows in grouped.items():
            user = await self.auth_repo.get_user_by_id(user_id)
            if (
                user is None
                or not user.is_active
                or not user.is_verified
                or not user.reminder_email_enabled
                or not getattr(user, FAMILY_SWITCH[kind])
            ):
                report["skipped"] += len(rows)
                continue

            try:
                await self._deliver(kind, user, rows, reference)
            except Exception:
                logger.exception(
                    "reminder '%s' failed for user %s (%d occurrence(s) left for the next run)",
                    kind,
                    user_id,
                    len(rows),
                )
                report["failed"] += len(rows)
                continue

            await self.repo.mark_reminders_sent([entry[0].id for entry in rows], kind=kind)
            if kind == ACTION:
                covered = [entry[0].id for entry in rows if entry[2].reason == TRIAL]
                await self.repo.mark_reminders_sent(covered, kind=NOTICE)
            await self.repo.session.commit()
            emailed.add(user_id)
            # Un compte peut recevoir les trois familles dans le meme passage :
            # 'users' ne dit donc pas ce que le quota Resend a reellement paye.
            report["emails_sent"] += 1
            report[counter] += len(rows)

    async def _open_actions(self, reference: date) -> list[tuple]:
        actionable = []
        for occurrence, commitment in await self.repo.action_candidates(reference):
            window = action_window(commitment, occurrence.due_date, reference=reference)
            if window is not None and window.is_open(reference):
                actionable.append((occurrence, commitment, window))
        return actionable

    async def send_due(self, *, on_date: date | None = None) -> dict:
        reference = on_date or today_utc()
        # emails_sent est un plancher, pas la facture : le quota Resend compte
        # aussi les transactionnels (verification, mot de passe, changement
        # d'adresse), qui ne passent pas par ici. Le tableau de bord Resend fait
        # foi.
        report = {
            "users": 0,
            "emails_sent": 0,
            "occurrences": 0,
            "overdue": 0,
            "actions": 0,
            "skipped": 0,
            "failed": 0,
        }
        emailed: set[str] = set()

        await self._dispatch(
            ACTION,
            await self._open_actions(reference),
            reference=reference,
            counter="actions",
            report=report,
            emailed=emailed,
        )
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
