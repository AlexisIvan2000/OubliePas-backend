import logging
from collections import defaultdict
from datetime import date

from repositories.auth_repository import AuthRepository
from repositories.commitment_repository import CommitmentRepository
from services.commitments.action_window import TRIAL, action_window
from core.config import push_configured
from repositories.push_repository import PushRepository
from services.emailing.email_sender import EmailSender
from services.pushing.push_sender import PushSender

logger = logging.getLogger(__name__)

NOTICE = "notice"
OVERDUE = "overdue"
ACTION = "action_required"

FAMILY_SWITCH = {
    NOTICE: "reminder_notice_enabled",
    OVERDUE: "reminder_overdue_enabled",
    ACTION: "reminder_action_enabled",
}

EMAIL = "email"
PUSH = "push"

# Chaque canal a son interrupteur de compte, son compteur d'envois et son
# compteur d'echecs. Seul celui du courriel decide du code de sortie du job : le
# push est le canal rapide, pas le canal fiable, et un telephone endormi ne doit
# pas faire echouer un passage ou tous les courriels sont partis.
CHANNELS = {
    EMAIL: {"switch": "reminder_email_enabled", "sent": "emails_sent", "failed": "failed"},
    PUSH: {"switch": "reminder_push_enabled", "sent": "push_sent", "failed": "push_failed"},
}


class ReminderService:
    def __init__(
        self,
        repo: CommitmentRepository,
        auth_repo: AuthRepository,
        sender: EmailSender,
        push_repo: PushRepository | None = None,
        push_sender: PushSender | None = None,
    ):
        self.repo = repo
        self.auth_repo = auth_repo
        self.sender = sender
        # Absents, le push ne part pas et le courriel ne s'en apercoit pas.
        self.push_repo = push_repo
        self.push_sender = push_sender

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

    async def _deliver_push(self, kind: str, user, entries: list[tuple], reference: date) -> None:
        subscriptions = await self.push_repo.list_for_user(str(user.id))
        if not subscriptions:
            # Interrupteur allume sans appareil enregistre : rien a envoyer,
            # mais rien d'anormal non plus.
            return

        items = self._items(kind, entries, reference)
        for subscription in subscriptions:
            outcome = await self.push_sender.send_reminder(
                subscription, kind=kind, items=items, locale=user.locale
            )
            if outcome == "gone":
                await self.push_repo.forget(subscription.endpoint)

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
        channel: str = EMAIL,
    ) -> None:
        rules = CHANNELS[channel]
        grouped: dict[str, list[tuple]] = defaultdict(list)
        for entry in entries:
            grouped[str(entry[0].user_id)].append(entry)

        for user_id, rows in grouped.items():
            user = await self.auth_repo.get_user_by_id(user_id)
            if (
                user is None
                or not user.is_active
                or not user.is_verified
                or not getattr(user, rules["switch"])
                or not getattr(user, FAMILY_SWITCH[kind])
            ):
                report["skipped"] += len(rows)
                continue

            try:
                if channel == PUSH:
                    await self._deliver_push(kind, user, rows, reference)
                else:
                    await self._deliver(kind, user, rows, reference)
            except Exception:
                logger.exception(
                    "reminder '%s' failed on %s for user %s (%d occurrence(s) left for the next run)",
                    kind,
                    channel,
                    user_id,
                    len(rows),
                )
                report[rules["failed"]] += len(rows)
                continue

            await self.repo.mark_reminders_sent(
                [entry[0].id for entry in rows], kind=kind, channel=channel
            )
            if kind == ACTION:
                covered = [entry[0].id for entry in rows if entry[2].reason == TRIAL]
                await self.repo.mark_reminders_sent(covered, kind=NOTICE, channel=channel)
            await self.repo.session.commit()
            if channel == EMAIL:
                emailed.add(user_id)
            # Un compte peut recevoir les trois familles dans le meme passage :
            # 'users' ne dit donc pas ce que le quota Resend a reellement paye.
            report[rules["sent"]] += 1
            if channel == EMAIL:
                report[counter] += len(rows)

    async def _open_actions(self, reference: date, channel: str = EMAIL) -> list[tuple]:
        actionable = []
        for occurrence, commitment in await self.repo.action_candidates(reference, channel=channel):
            window = action_window(commitment, occurrence.due_date, reference=reference)
            if window is not None and window.is_open(reference):
                actionable.append((occurrence, commitment, window))
        return actionable

    def _channels(self) -> list[str]:
        # Le courriel d'abord, toujours : si le processus meurt entre les deux,
        # c'est le canal fiable qui sera parti.
        if self.push_repo is None or self.push_sender is None or not push_configured():
            return [EMAIL]
        return [EMAIL, PUSH]

    async def send_due(self, *, on_date: date) -> dict:
        reference = on_date
        # emails_sent est un plancher, pas la facture : le quota Resend compte
        # aussi les transactionnels (verification, mot de passe, changement
        # d'adresse), qui ne passent pas par ici. Le tableau de bord Resend fait
        # foi.
        report = {
            "users": 0,
            "emails_sent": 0,
            "push_sent": 0,
            "push_failed": 0,
            "occurrences": 0,
            "overdue": 0,
            "actions": 0,
            "skipped": 0,
            "failed": 0,
        }
        emailed: set[str] = set()

        for channel in self._channels():
            # L'ordre importe : action_required tamponne aussi le notice de la
            # meme echeance, pour ne pas annoncer deux fois la meme chose.
            await self._dispatch(
                ACTION,
                await self._open_actions(reference, channel),
                reference=reference,
                counter="actions",
                report=report,
                emailed=emailed,
                channel=channel,
            )
            await self._dispatch(
                NOTICE,
                await self.repo.due_for_reminder(reference, channel=channel),
                reference=reference,
                counter="occurrences",
                report=report,
                emailed=emailed,
                channel=channel,
            )
            await self._dispatch(
                OVERDUE,
                await self.repo.overdue_for_reminder(reference, channel=channel),
                reference=reference,
                counter="overdue",
                report=report,
                emailed=emailed,
                channel=channel,
            )

        report["users"] = len(emailed)
        return report
