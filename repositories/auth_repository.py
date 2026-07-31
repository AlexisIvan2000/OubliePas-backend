from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.db import User


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
        result = await self.session.execute(select(User).where(User.id == user_id))
        return result.scalar_one()

    async def update_verification_status(self, user_id: str) -> User:
        return await self.update_user(user_id, {
            "is_verified": True,
            "verification_code_hash": None,
            "verification_code_expires_at": None,
            "verification_attempts": 0,
        })

    async def save_reset_code(
        self, email: str, code_hash: str, expires_at, sent_at, resend_count: int
    ) -> User | None:
        await self.session.execute(
            update(User).where(User.email == email).values(
                reset_code_hash=code_hash,
                reset_code_expires_at=expires_at,
                verification_attempts=0,
                last_code_sent_at=sent_at,
                code_resend_count=resend_count,
            )
        )
        await self.session.flush()
        result = await self.session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def update_password(self, user_id: str, new_password_hash: str) -> User:
        return await self.update_user(user_id, {
            "password_hash": new_password_hash,
            "reset_code_hash": None,
            "reset_code_expires_at": None,
            "verification_attempts": 0,
        })

    async def increment_verification_attempts(self, user_id: str) -> None:
        await self.session.execute(
            update(User).where(User.id == user_id).values(
                verification_attempts=User.verification_attempts + 1
            )
        )
        await self.session.flush()

    async def reset_verification_attempts(self, user_id: str) -> None:
        await self.session.execute(
            update(User).where(User.id == user_id).values(verification_attempts=0)
        )
        await self.session.flush()

    async def delete_user(self, user_id: str) -> None:
        user = await self.get_user_by_id(user_id)
        if user:
            await self.session.delete(user)
            await self.session.flush()