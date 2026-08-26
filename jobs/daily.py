import asyncio
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

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

logger = logging.getLogger(__name__)


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
