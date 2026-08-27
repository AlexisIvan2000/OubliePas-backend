from datetime import datetime, timedelta, timezone

from core.exceptions import (
    AccountDisabled,
    DisposableEmailNotAllowed,
    EmailAlreadyRegistered,
    EmailNotVerified,
    InvalidCredentials,
    InvalidRefreshToken,
    InvalidVerificationCode,
    InvalidVerificationRequest,
    NoPendingEmailChange,
    TokenReuseDetected,
    TooManyVerificationAttempts,
    UserNotFound,
    VerificationCodeExpired,
)
from core.security import Security
from core.validators import is_disposable_email, normalize_email
from models.db.user_db import MAX_VERIFICATION_ATTEMPTS
from models.schemas.auth_schema import UserCreate, UserLogin
from repositories.auth_repository import AuthRepository
from repositories.refresh_token_repository import RefreshTokenRepository
from services.authentication.tokens import issue_tokens
from services.emailing.otp_service import OtpService

MAX_LOGIN_ATTEMPTS_PER_HOUR = 20
LOGIN_WINDOW_HOURS = 1


class EmailPasswordAuth:
    def __init__(self, auth_repo: AuthRepository, refresh_token_repo: RefreshTokenRepository, otp_service: OtpService):
        self.repo = auth_repo
        self.rt_repo = refresh_token_repo
        self.otp_svc = otp_service

    async def _issue_tokens(self, user) -> dict:
        return await issue_tokens(self.rt_repo, user)

    async def register_user(self, user: UserCreate):
        email = normalize_email(user.email)

        if is_disposable_email(email):
            raise DisposableEmailNotAllowed()

        if await self.repo.get_user_by_email(email):
            raise EmailAlreadyRegistered()

        password_hash = await Security.hash_password_async(user.password)

        new_user = await self.repo.create_user({
            "first_name": user.first_name,
            "email": email,
            "password_hash": password_hash,
            "currency": user.currency,
            "locale": user.locale,
        })
        await self.otp_svc.send_verification_otp(email, str(new_user.id), locale=user.locale)

        return {"message": "Account created. Please check your email for the verification code."}

    @staticmethod
    def _login_locked(db_user, now: datetime) -> bool:
        return (
            db_user.last_failed_login_at is not None
            and db_user.last_failed_login_at > now - timedelta(hours=LOGIN_WINDOW_HOURS)
            and db_user.failed_login_count >= MAX_LOGIN_ATTEMPTS_PER_HOUR
        )

    @staticmethod
    def _next_failure_count(db_user, now: datetime) -> int:
        window_start = now - timedelta(hours=LOGIN_WINDOW_HOURS)
        if db_user.last_failed_login_at and db_user.last_failed_login_at > window_start:
            return db_user.failed_login_count + 1
        return 1

    async def login_user(self, user: UserLogin):
        now = datetime.now(timezone.utc)
        db_user = await self.repo.get_user_by_email(normalize_email(user.email))
        if not db_user:
            await Security.dummy_verify_async()
            raise InvalidCredentials()

        if self._login_locked(db_user, now):
            # Meme reponse et meme cout qu'un mot de passe faux : sans cette
            # verification a vide, le refus serait plus rapide et signalerait
            # que le compte existe.
            await Security.dummy_verify_async()
            raise InvalidCredentials()

        if not await Security.verify_password_async(db_user.password_hash, user.password):
            await self.repo.record_failed_login(
                str(db_user.id),
                count=self._next_failure_count(db_user, now),
                at=now,
            )
            raise InvalidCredentials()

        if db_user.failed_login_count:
            await self.repo.clear_failed_logins(str(db_user.id))

        if not db_user.is_active:
            raise AccountDisabled()

        if not db_user.is_verified:
            raise EmailNotVerified()

        if Security.needs_rehash(db_user.password_hash):
            await self.repo.update_user(str(db_user.id), {
                "password_hash": await Security.hash_password_async(user.password),
            })

        return await self._issue_tokens(db_user)

    async def verify_email(self, email: str, code: str):
        db_user = await self.repo.get_user_by_email(normalize_email(email))
        if not db_user or db_user.is_verified:
            raise InvalidVerificationRequest()

        if not db_user.is_active:
            raise AccountDisabled()

        if db_user.verification_attempts >= MAX_VERIFICATION_ATTEMPTS:
            raise TooManyVerificationAttempts()

        await self.repo.increment_verification_attempts(str(db_user.id))

        expires_at = db_user.verification_code_expires_at
        if not expires_at or expires_at < datetime.now(timezone.utc):
            raise VerificationCodeExpired()

        if not Security.verify_otp(code, db_user.verification_code_hash):
            raise InvalidVerificationCode()

        user_id = str(db_user.id)
        updated = await self.repo.update_verification_status(user_id)

        return await self._issue_tokens(updated)

    async def resend_verification_email(self, email: str):
        db_user = await self.repo.get_user_by_email(normalize_email(email))
        if db_user and db_user.is_active and not db_user.is_verified:
            await self.otp_svc.send_verification_otp(db_user.email, str(db_user.id), db_user=db_user)
        return {"message": "If this email is registered and unverified, a new verification code has been sent"}

    async def resend_email_change_verification(self, user_id: str):
        db_user = await self.repo.get_user_by_id(user_id)
        if not db_user:
            raise UserNotFound()

        if not db_user.pending_email:
            raise NoPendingEmailChange()

        await self.otp_svc.send_email_change_otp(db_user.pending_email, user_id, db_user=db_user)
        return {"message": "Verification code resent to new address"}

    async def refresh_access_token(self, refresh_token: str):
        payload = Security.decode_token(refresh_token)
        if not payload or payload.get("type") != "refresh":
            raise InvalidRefreshToken()

        token_hash = Security.hash_token(refresh_token)
        db_token = await self.rt_repo.get_by_token_hash(token_hash)

        if not db_token:
            replayed = await self.rt_repo.get_any_by_token_hash(token_hash)
            if replayed:
                await self.rt_repo.revoke_all_for_user(replayed.user_id)
                raise TokenReuseDetected()
            raise InvalidRefreshToken()

        if db_token.expires_at < datetime.now(timezone.utc):
            raise InvalidRefreshToken()

        user_id = payload.get("sub")
        if not user_id or str(db_token.user_id) != user_id:
            raise InvalidRefreshToken()

        db_user = await self.repo.get_user_by_id(user_id)
        if not db_user:
            raise InvalidRefreshToken()

        if not db_user.is_active:
            await self.rt_repo.revoke_all_for_user(db_token.user_id)
            raise AccountDisabled()

        await self.rt_repo.revoke(token_hash)

        return await self._issue_tokens(db_user)

    async def logout_user(self, refresh_token: str):
        await self.rt_repo.revoke(Security.hash_token(refresh_token))
        return {"message": "User logged out successfully"}
