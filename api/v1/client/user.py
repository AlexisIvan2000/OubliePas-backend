from fastapi import APIRouter, Request

from api.dependencies import CurrentUserDep, EmailPasswordAuthDep, UserProfileDep
from core.rate_limit import limiter
from models.schemas.auth_schema import MessageResponse, UserResponse
from models.schemas.user_schema import (
    ChangeEmail,
    ChangePassword,
    ConfirmEmailChange,
    SetPassword,
    UpdateProfile,
)

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserResponse)
async def get_profile(user: CurrentUserDep):
    return UserResponse(
        id=str(user.id),
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
        is_verified=user.is_verified,
        role=user.role,
        avatar_url=user.avatar_url,
        currency=user.currency,
    )


@router.patch("/me", response_model=MessageResponse)
@limiter.limit("30/minute")
async def update_profile(
    request: Request, payload: UpdateProfile, user: CurrentUserDep, service: UserProfileDep
):
    return await service.update_profile(str(user.id), payload)


@router.post("/me/change-password", response_model=MessageResponse)
@limiter.limit("5/minute")
async def change_password(
    request: Request, payload: ChangePassword, user: CurrentUserDep, service: UserProfileDep
):
    return await service.change_password(str(user.id), payload)


@router.post("/me/set-password", response_model=MessageResponse)
@limiter.limit("5/minute")
async def set_password(
    request: Request, payload: SetPassword, user: CurrentUserDep, service: UserProfileDep
):
    return await service.set_password(str(user.id), payload)


@router.post("/me/change-email", response_model=MessageResponse)
@limiter.limit("5/hour")
async def change_email(
    request: Request, payload: ChangeEmail, user: CurrentUserDep, service: UserProfileDep
):
    return await service.request_email_change(str(user.id), payload)


@router.post("/me/confirm-email-change", response_model=MessageResponse)
@limiter.limit("10/minute")
async def confirm_email_change(
    request: Request, payload: ConfirmEmailChange, user: CurrentUserDep, service: UserProfileDep
):
    return await service.confirm_email_change(str(user.id), payload)


@router.post("/me/resend-email-change", response_model=MessageResponse)
@limiter.limit("5/hour")
async def resend_email_change(
    request: Request, user: CurrentUserDep, service: EmailPasswordAuthDep
):
    return await service.resend_email_change_verification(str(user.id))
