import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.db import PushSubscription
from models.db.user_db import MAX_PUSH_SUBSCRIPTIONS_PER_USER


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
        # Un appareil peut changer de compte : le conflit met à jour le
        # propriétaire et les clés, il n'ajoute pas une seconde ligne morte.
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
        saved = result.scalar_one()
        await self.prune(user_id)
        return saved

    async def prune(self, user_id: str, keep: int = MAX_PUSH_SUBSCRIPTIONS_PER_USER) -> int:
        # Le moins récemment vu part, et se réabonnera seul à la prochaine
        # visite. L'identifiant départage les égalités, sinon l'ordre serait
        # celui que la base voudra bien rendre.
        gardes = (
            select(PushSubscription.id)
            .where(PushSubscription.user_id == user_id)
            .order_by(PushSubscription.last_seen_at.desc(), PushSubscription.id.desc())
            .limit(keep)
        )
        result = await self.session.execute(
            delete(PushSubscription).where(
                PushSubscription.user_id == user_id,
                PushSubscription.id.not_in(gardes),
            )
        )
        await self.session.flush()
        return result.rowcount

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
        # Appelée sur 410 Gone. On efface sans vérifier le propriétaire parce
        # que l'adresse est unique : perdre cette unicité rendrait la méthode
        # capable d'effacer la ligne d'un autre compte.
        result = await self.session.execute(
            delete(PushSubscription).where(PushSubscription.endpoint == endpoint)
        )
        await self.session.flush()
        return result.rowcount
