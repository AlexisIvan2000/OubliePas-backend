from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.db.commitments_db import DEFAULT_REMINDER_CHANNEL, WeeklyDigest


class DigestRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def sent_for_week(
        self, week_start: date, *, channel: str = DEFAULT_REMINDER_CHANNEL
    ) -> set[str]:
        # Une requête pour la semaine plutôt qu'une par compte : la liste tient
        # en mémoire.
        result = await self.session.execute(
            select(WeeklyDigest.user_id).where(
                WeeklyDigest.week_start == week_start,
                WeeklyDigest.channel == channel,
            )
        )
        return {str(user_id) for user_id in result.scalars().all()}

    async def mark_sent(
        self, user_id: str, week_start: date, *, channel: str = DEFAULT_REMINDER_CHANNEL
    ) -> None:
        self.session.add(
            WeeklyDigest(user_id=user_id, week_start=week_start, channel=channel)
        )
