import logging
from collections import defaultdict
from datetime import date, timedelta

from repositories.auth_repository import AuthRepository
from repositories.commitment_repository import CommitmentRepository
from repositories.digest_repository import DigestRepository
from services.emailing.email_sender import EmailSender

logger = logging.getLogger(__name__)

WEEKLY_SWITCH = "reminder_weekly_enabled"


def week_start(reference: date) -> date:
    return reference - timedelta(days=reference.weekday())


class WeeklyDigestService:
    def __init__(
        self,
        repo: CommitmentRepository,
        auth_repo: AuthRepository,
        digest_repo: DigestRepository,
        sender: EmailSender,
    ):
        self.repo = repo
        self.auth_repo = auth_repo
        self.digest_repo = digest_repo
        self.sender = sender

    async def send(self, *, on_date: date) -> dict:
        reference = on_date
        report = {"weekly_sent": 0, "weekly_skipped": 0, "weekly_failed": 0}

        start = week_start(reference)
        end = start + timedelta(days=6)

        # C'est cette clef, et non une garde sur le lundi, qui tient l'envoi
        # unique : un lundi en panne est donc rattrape le lendemain.
        already = await self.digest_repo.sent_for_week(start)

        # Du jour meme a dimanche, jamais depuis lundi : un rattrapage
        # annoncerait sinon des echeances passees, deja couvertes par 'overdue'.
        grouped: dict[str, list] = defaultdict(list)
        for occurrence, commitment in await self.repo.due_between(reference, end):
            grouped[str(occurrence.user_id)].append((occurrence, commitment))

        for user_id, rows in grouped.items():
            if user_id in already:
                continue

            user = await self.auth_repo.get_user_by_id(user_id)
            if (
                user is None
                or not user.is_active
                or not user.is_verified
                or not user.reminder_email_enabled
                or not getattr(user, WEEKLY_SWITCH)
            ):
                report["weekly_skipped"] += 1
                continue

            items = [
                {
                    "title": commitment.title,
                    "due_date": occurrence.due_date,
                    "amount": occurrence.amount,
                }
                for occurrence, commitment in rows
            ]

            try:
                await self.sender.send_weekly_digest_email(
                    user.email,
                    first_name=user.first_name,
                    items=items,
                    currency=user.currency,
                    week_start=start,
                    locale=user.locale,
                )
            except Exception:
                logger.exception(
                    "weekly digest failed for user %s (retried on the next run this week)",
                    user_id,
                )
                report["weekly_failed"] += 1
                continue

            await self.digest_repo.mark_sent(user_id, start)
            # Un commit par compte : un echec en cours de passage ne doit pas
            # renvoyer a ceux qui ont deja recu.
            await self.repo.session.commit()
            report["weekly_sent"] += 1

        return report
