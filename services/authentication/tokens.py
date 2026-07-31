from datetime import datetime, timedelta, timezone

from core.config import REFRESH_TOKEN_EXPIRE_DAYS
from core.security import Security
from repositories.refresh_token_repository import RefreshTokenRepository


async def issue_tokens(refresh_token_repo: RefreshTokenRepository, user) -> dict:
    user_id = str(user.id)
    access_token = Security.create_access_token(user_id, role=user.role)
    refresh_token = Security.create_refresh_token(user_id)

    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    await refresh_token_repo.create(user_id, Security.hash_token(refresh_token), expires_at)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "role": user.role,
    }
