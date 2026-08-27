import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.db import User, VerificationAttempt


class AuthRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_user_by_email(self, email: str) -> User | None:
        result = await self.session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def create_user(self, user_data: dict) -> User:
        user = User(**user_data)
        self.session.add(user)
        await self.session.flush()
        return user

    async def get_user_by_google_sub(self, google_sub: str) -> User | None:
        result = await self.session.execute(select(User).where(User.google_sub == google_sub))
        return result.scalar_one_or_none()

    async def get_user_by_id(self, user_id: str) -> User | None:
        result = await self.session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def update_user(self, user_id: str, data: dict) -> User:
        await self.session.execute(
            update(User).where(User.id == user_id).values(**data)
        )
        await self.session.flush()
        result = await self.session.execute(
            select(User).where(User.id == user_id).execution_options(populate_existing=True)
        )
        return result.scalar_one()

    async def update_verification_status(self, user_id: str) -> User:
        return await self.update_user(user_id, {
            "is_verified": True,
            "verification_code_hash": None,
            "verification_code_expires_at": None,
        })

    # Le mot de passe oublie ne connait que l'adresse : l'utilisateur n'est pas
    # authentifie a ce moment-la. C'est la seule ecriture de code qui ne passe
    # pas par l'identifiant, et la raison pour laquelle les emetteurs d'OTP ne
    # peuvent pas etre fondus en un seul appel de depot.
    async def update_user_by_email(self, email: str, data: dict) -> User | None:
        await self.session.execute(update(User).where(User.email == email).values(**data))
        await self.session.flush()
        result = await self.session.execute(
            select(User).where(User.email == email).execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def update_password(self, user_id: str, new_password_hash: str) -> User:
        return await self.update_user(user_id, {
            "password_hash": new_password_hash,
            "reset_code_hash": None,
            "reset_code_expires_at": None,
        })

    async def attempts(self, user_id: str, kind: str) -> int:
        result = await self.session.execute(
            select(VerificationAttempt.count).where(
                VerificationAttempt.user_id == user_id,
                VerificationAttempt.kind == kind,
            )
        )
        return result.scalar_one_or_none() or 0

    async def bump_attempts(self, user_id: str, kind: str) -> int:
        # Une seule aller-retour, et la contrainte d'unicite arbitre deux essais
        # simultanes au lieu d'un lire-puis-ecrire qui en perdrait un.
        statement = (
            pg_insert(VerificationAttempt)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                kind=kind,
                count=1,
                updated_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_update(
                constraint="uq_verification_attempt_user_kind",
                set_={
                    "count": VerificationAttempt.count + 1,
                    "updated_at": datetime.now(timezone.utc),
                },
            )
            .returning(VerificationAttempt.count)
        )
        result = await self.session.execute(statement)
        await self.session.flush()
        return result.scalar_one()

    async def clear_attempts(self, user_id: str, kind: str) -> None:
        await self.session.execute(
            delete(VerificationAttempt).where(
                VerificationAttempt.user_id == user_id,
                VerificationAttempt.kind == kind,
            )
        )
        await self.session.flush()

    async def record_failed_login(self, user_id: str, *, count: int, at) -> None:
        await self.session.execute(
            update(User).where(User.id == user_id).values(
                failed_login_count=count, last_failed_login_at=at
            )
        )
        await self.session.flush()

    async def clear_failed_logins(self, user_id: str) -> None:
        await self.session.execute(
            update(User).where(User.id == user_id).values(
                failed_login_count=0, last_failed_login_at=None
            )
        )
        await self.session.flush()

    async def delete_user(self, user_id: str) -> None:
        user = await self.get_user_by_id(user_id)
        if user:
            await self.session.delete(user)
            await self.session.flush()