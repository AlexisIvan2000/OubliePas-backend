from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session
from core.exceptions import AccountDisabled, InsufficientPermissions, InvalidAccessToken
from core.security import Security
from models.db import User
from repositories.auth_repository import AuthRepository
from repositories.commitment_repository import CommitmentRepository
from repositories.refresh_token_repository import RefreshTokenRepository
from services.authentication.email_password import EmailPasswordAuth
from services.authentication.google_auth import GoogleAuth, GoogleTokenClient
from services.commitments.commitment_service import CommitmentService
from services.commitments.occurrence_generator import OccurrenceGenerator
from services.emailing.otp_service import OtpService
from services.storage.object_storage import storage
from services.user_profile.avatar_service import AvatarService
from services.user_profile.user_profile import UserProfile

bearer_scheme = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_auth_repository(session: SessionDep) -> AuthRepository:
    return AuthRepository(session)


def get_refresh_token_repository(session: SessionDep) -> RefreshTokenRepository:
    return RefreshTokenRepository(session)


AuthRepoDep = Annotated[AuthRepository, Depends(get_auth_repository)]
RefreshTokenRepoDep = Annotated[RefreshTokenRepository, Depends(get_refresh_token_repository)]


def get_otp_service(auth_repo: AuthRepoDep) -> OtpService:
    return OtpService(auth_repo)


OtpServiceDep = Annotated[OtpService, Depends(get_otp_service)]


def get_email_password_auth(
    auth_repo: AuthRepoDep,
    rt_repo: RefreshTokenRepoDep,
    otp_service: OtpServiceDep,
) -> EmailPasswordAuth:
    return EmailPasswordAuth(auth_repo, rt_repo, otp_service)


EmailPasswordAuthDep = Annotated[EmailPasswordAuth, Depends(get_email_password_auth)]


def get_google_auth(auth_repo: AuthRepoDep, rt_repo: RefreshTokenRepoDep) -> GoogleAuth:
    return GoogleAuth(auth_repo, rt_repo, GoogleTokenClient())


GoogleAuthDep = Annotated[GoogleAuth, Depends(get_google_auth)]


def get_user_profile(
    auth_repo: AuthRepoDep,
    rt_repo: RefreshTokenRepoDep,
    otp_service: OtpServiceDep,
) -> UserProfile:
    return UserProfile(auth_repo, rt_repo, otp_service)


UserProfileDep = Annotated[UserProfile, Depends(get_user_profile)]


def get_avatar_service(auth_repo: AuthRepoDep) -> AvatarService:
    return AvatarService(auth_repo, storage)


AvatarServiceDep = Annotated[AvatarService, Depends(get_avatar_service)]


def get_commitment_repository(session: SessionDep) -> CommitmentRepository:
    return CommitmentRepository(session)


CommitmentRepoDep = Annotated[CommitmentRepository, Depends(get_commitment_repository)]


def get_commitment_service(repo: CommitmentRepoDep) -> CommitmentService:
    return CommitmentService(repo, OccurrenceGenerator(repo))


CommitmentServiceDep = Annotated[CommitmentService, Depends(get_commitment_service)]


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    auth_repo: AuthRepoDep,
) -> User:
    if credentials is None:
        raise InvalidAccessToken()

    payload = Security.decode_token(credentials.credentials)
    if not payload or payload.get("type") != "access":
        raise InvalidAccessToken()

    user_id = payload.get("sub")
    if not user_id:
        raise InvalidAccessToken()

    user = await auth_repo.get_user_by_id(user_id)
    if not user:
        raise InvalidAccessToken()

    if not user.is_active:
        raise AccountDisabled()

    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUserDep) -> User:
    if user.role not in ("admin", "super_admin"):
        raise InsufficientPermissions()
    return user


AdminUserDep = Annotated[User, Depends(require_admin)]
