import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.db import PushSubscription


class PushRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_for_user(self, user_id: str) -> list[PushSubscription]:
        result = await self.session.execute(
            select(PushSubscription)
            .where(PushSubscription.user_id == user_id)
            .order_by(PushSubscription.last_seen_at.desc())
        )
        return list(result.scalars().all())

    async def find(self, user_id: str, endpoint: str) -> PushSubscription | None:
        result = await self.session.execute(
            select(PushSubscription).where(
                PushSubscription.user_id == user_id,
                PushSubscription.endpoint == endpoint,
            )
        )
        return result.scalar_one_or_none()

    async def save(
        self, user_id: str, *, endpoint: str, p256dh: str, auth: str, user_agent: str | None
    ) -> PushSubscription:
        now = datetime.now(timezone.utc)
        # Un navigateur reconduit la meme adresse a chaque reabonnement, et un
        # appareil peut changer de compte : le conflit met a jour le
        # proprietaire et les cles, il n'ajoute pas une seconde ligne morte.
        statement = (
            pg_insert(PushSubscription)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                endpoint=endpoint,
                p256dh=p256dh,
                auth=auth,
                user_agent=user_agent,
                created_at=now,
                last_seen_at=now,
            )
            .on_conflict_do_update(
                index_elements=[PushSubscription.endpoint],
                set_={
                    "user_id": user_id,
                    "p256dh": p256dh,
                    "auth": auth,
                    "user_agent": user_agent,
                    "last_seen_at": now,
                },
            )
            .returning(PushSubscription)
        )
        result = await self.session.execute(statement)
        await self.session.flush()
        return result.scalar_one()

    async def remove(self, user_id: str, endpoint: str) -> int:
        result = await self.session.execute(
            delete(PushSubscription).where(
                PushSubscription.user_id == user_id,
                PushSubscription.endpoint == endpoint,
            )
        )
        await self.session.flush()
        return result.rowcount

    async def forget(self, endpoint: str) -> int:
        # Appelee sur 410 Gone : le service de push declare l'adresse morte, et
        # personne n'est la pour le dire a l'utilisateur. On efface sans
        # verifier le proprietaire, puisque l'adresse est unique.
        result = await self.session.execute(
            delete(PushSubscription).where(PushSubscription.endpoint == endpoint)
        )
        await self.session.flush()
        return result.rowcount
