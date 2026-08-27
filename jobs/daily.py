import asyncio
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import OPERATOR_EMAIL
from models.db.commitments_db import (
    PURGE_AFTER_DAYS,
    PURGE_REMINDERS_AFTER_DAYS,
)
from repositories.auth_repository import AuthRepository
from repositories.commitment_repository import CommitmentRepository
from services.commitments.occurrence_generator import OccurrenceGenerator, today_utc
from services.emailing.email_sender import EmailSender
from services.notifications.reminder_service import ReminderService

LOCK_KEY = 8142026

# 80 % des 100 envois quotidiens du plan gratuit Resend. La marge de 20 %
# couvre l'angle mort du compteur : les transactionnels (codes de verification,
# reinitialisations) prennent aussi du quota sans passer par emails_sent.
RESEND_DAILY_ALERT_THRESHOLD = 80

logger = logging.getLogger(__name__)


def should_alert(emails_sent: int, operator: str | None) -> bool:
    return bool(operator) and emails_sent > RESEND_DAILY_ALERT_THRESHOLD


async def alert_operator(reference: date, emails_sent: int) -> None:
    # L'alerte est le thermometre, pas le patient : si elle echoue, le job
    # continue et son code de sortie reste celui des rappels utilisateurs.
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
    reference = today or today_utc()
    repo = CommitmentRepository(session)

    purged = await repo.purge_deleted(
        datetime.now(timezone.utc) - timedelta(days=PURGE_AFTER_DAYS)
    )
    forgotten = await repo.purge_reminders(
        reference - timedelta(days=PURGE_REMINDERS_AFTER_DAYS)
    )
    generated = await OccurrenceGenerator(repo).sync_all_active(today=reference)
    await session.commit()

    reminders = await ReminderService(repo, AuthRepository(session), EmailSender()).send_due(
        on_date=reference
    )

    if should_alert(reminders["emails_sent"], OPERATOR_EMAIL):
        await alert_operator(reference, reminders["emails_sent"])

    return {
        "date": reference.isoformat(),
        "occurrences_generated": generated,
        "purged": purged,
        "reminders_purged": forgotten,
        **reminders,
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
