from models.db import User
from models.schemas.auth_schema import UserResponse
from services.storage.object_storage import public_avatar_url


def user_response(user: User) -> UserResponse:
    return UserResponse(
        id=str(user.id),
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
        is_verified=user.is_verified,
        role=user.role,
        avatar_url=public_avatar_url(user.avatar_key, user.avatar_url),
        has_custom_avatar=bool(user.avatar_key),
        has_password=bool(user.password_hash),
        currency=user.currency,
        reminder_email_enabled=user.reminder_email_enabled,
        default_reminder_days=user.default_reminder_days,
        locale=user.locale,
    )
