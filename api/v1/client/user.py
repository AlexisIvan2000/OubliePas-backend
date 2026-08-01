from typing import Annotated

from fastapi import APIRouter, File, Request, UploadFile

from api.dependencies import (
    AvatarServiceDep,
    CurrentUserDep,
    EmailPasswordAuthDep,
    UserProfileDep,
)
from api.responses import user_response
from core.exceptions import AvatarTooLarge
from core.rate_limit import limiter
from models.schemas.auth_schema import MessageResponse, UserResponse
from models.schemas.user_schema import (
    ChangeEmail,
    ChangePassword,
    ConfirmEmailChange,
    SetPassword,
    UpdateProfile,
)
from services.user_profile.avatar_service import MAX_AVATAR_BYTES

router = APIRouter(prefix="/users", tags=["users"])

MULTIPART_OVERHEAD_BYTES = 8 * 1024


def _reject_oversized_body(request: Request) -> None:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit():
        if int(declared) > MAX_AVATAR_BYTES + MULTIPART_OVERHEAD_BYTES:
            raise AvatarTooLarge()


@router.get("/me", response_model=UserResponse)
async def get_profile(user: CurrentUserDep):
    return user_response(user)


@router.post("/me/avatar", response_model=UserResponse)
@limiter.limit("10/hour")
async def upload_avatar(
    request: Request,
    user: CurrentUserDep,
    service: AvatarServiceDep,
    file: Annotated[UploadFile, File()],
):
    _reject_oversized_body(request)
    updated = await service.replace(str(user.id), file)
    return user_response(updated)


@router.delete("/me/avatar", response_model=UserResponse)
@limiter.limit("20/hour")
async def delete_avatar(request: Request, user: CurrentUserDep, service: AvatarServiceDep):
    updated = await service.remove(str(user.id))
    return user_response(updated)


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
