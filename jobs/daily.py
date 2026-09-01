import asyncio
import json
import logging
import sys
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import OPERATOR_EMAIL
from models.db.commitments_db import (
    PURGE_AFTER_DAYS,
    PURGE_REMINDERS_AFTER_DAYS,
)
from repositories.auth_repository import AuthRepository
from repositories.commitment_repository import CommitmentRepository
from repositories.push_repository import PushRepository
from services.commitments.occurrence_generator import OccurrenceGenerator, today_utc
from services.emailing.email_sender import EmailSender
from repositories.digest_repository import DigestRepository
from services.notifications.reminder_service import ReminderService
from services.notifications.weekly_digest import WeeklyDigestService
from services.pushing.push_sender import PushSender

LOCK_KEY = 8142026

# Doit rester d'accord avec la planification Railway. Elle sert à rejouer une
# date passée : le calcul de chacun part d'un instant, et un instant sans heure
# n'existe pas. Le tableau des heures par fuseau est dans le README.
CRON_HOUR = time(12, 0)

# 80 % des 100 envois quotidiens du plan gratuit. La marge couvre l'angle mort
# du compteur : les transactionnels prennent du quota sans passer par ici.
RESEND_DAILY_ALERT_THRESHOLD = 80

logger = logging.getLogger(__name__)


def should_alert(emails_sent: int, operator: str | None) -> bool:
    return bool(operator) and emails_sent > RESEND_DAILY_ALERT_THRESHOLD


async def alert_operator(reference: date, emails_sent: int) -> None:
    # Le thermomètre, pas le patient : son échec ne doit pas faire échouer un
    # passage où tous les rappels sont partis.
    try:
        await EmailSender().send_admin_email(
            OPERATOR_EMAIL,
            "OubliePas : le quota d'envoi approche",
            f"Le passage du {reference.isoformat()} a envoye {emails_sent} rappels, "
            f"au-dela du seuil de {RESEND_DAILY_ALERT_THRESHOLD} sur les 100 envois "
            "quotidiens du plan gratuit Resend, transactionnels non comptes.",
        )
    except Exception:
        logger.exception("quota alert could not be delivered to the operator")


async def run_daily(session: AsyncSession, *, today: date | None = None) -> dict:
    moment = (
        datetime.now(timezone.utc)
        if today is None
        else datetime.combine(today, CRON_HOUR, tzinfo=timezone.utc)
    )
    reference = moment.date()
    repo = CommitmentRepository(session)

    purged = await repo.purge_deleted(
        datetime.now(timezone.utc) - timedelta(days=PURGE_AFTER_DAYS)
    )
    forgotten = await repo.purge_reminders(
        reference - timedelta(days=PURGE_REMINDERS_AFTER_DAYS)
    )
    generated = await OccurrenceGenerator(repo).sync_all_active(today=reference)
    await session.commit()

    reminders = await ReminderService(
        repo,
        AuthRepository(session),
        EmailSender(),
        push_repo=PushRepository(session),
        push_sender=PushSender(),
    ).send_due(at=moment)

    # Pas de planification propre : c'est ce qui permet de rattraper un lundi
    # manqué les jours suivants.
    digest = await WeeklyDigestService(
        repo, AuthRepository(session), DigestRepository(session), EmailSender()
    ).send(at=moment)

    # Sur le total du passage : un lundi, le récapitulatif part à tous les
    # abonnés d'un coup, et c'est ce pic qui approche du quota.
    total_emails = reminders["emails_sent"] + digest["weekly_sent"]
    if should_alert(total_emails, OPERATOR_EMAIL):
        await alert_operator(reference, total_emails)

    return {
        "date": reference.isoformat(),
        "occurrences_generated": generated,
        "purged": purged,
        "reminders_purged": forgotten,
        **reminders,
        **digest,
    }


async def main() -> dict:
    import core.database as database

    async with database.AsyncSessionLocal() as lock_session:
        acquired = await lock_session.execute(
            text("select pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}
        )
        if not acquired.scalar():
            return {"skipped": "another run is already in progress"}

        try:
            async with database.AsyncSessionLocal() as session:
                return await run_daily(session)
        finally:
            await lock_session.execute(
                text("select pg_advisory_unlock(:key)"), {"key": LOCK_KEY}
            )


async def _cli() -> dict:
    from core.database import dispose_engine

    try:
        return await main()
    finally:
        await dispose_engine()


def exit_code(report: dict) -> int:
    return 1 if report.get("failed") else 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    outcome = asyncio.run(_cli())
    print(json.dumps(outcome))
    if outcome.get("failed"):
        logger.error("%s reminder(s) could not be sent", outcome["failed"])
    raise SystemExit(exit_code(outcome))
