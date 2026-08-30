from models.db import User
from models.db.commitments_db import MAX_COMMITMENTS_PER_TYPE
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
        reminder_push_enabled=user.reminder_push_enabled,
        reminder_notice_enabled=user.reminder_notice_enabled,
        reminder_overdue_enabled=user.reminder_overdue_enabled,
        reminder_action_enabled=user.reminder_action_enabled,
        reminder_weekly_enabled=user.reminder_weekly_enabled,
        default_reminder_days=user.default_reminder_days,
        locale=user.locale,
        commitment_limit=MAX_COMMITMENTS_PER_TYPE,
    )
