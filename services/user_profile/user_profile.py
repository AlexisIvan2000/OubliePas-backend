from datetime import datetime, timezone

from core.exceptions import (
    AccountDisabled,
    DisposableEmailNotAllowed,
    EmailAlreadyInUse,
    GoogleOnlyAccount,
    IncorrectCurrentPassword,
    IncorrectPassword,
    InvalidDeletionConfirmation,
    InvalidOrExpiredResetCode,
    InvalidResetCode,
    InvalidVerificationCode,
    NoEmailChangeCode,
    NoFieldsToUpdate,
    NoPendingEmailChange,
    PasswordAlreadySet,
    ResetCodeExpired,
    SameEmailAsCurrent,
    SamePasswordAsBefore,
    TooManyCodeRequests,
    TooManyVerificationAttempts,
    UserNotFound,
    VerificationCodeExpired,
)
from core.security import Security
from core.validators import is_disposable_email, normalize_email
from models.db.user_db import MAX_VERIFICATION_ATTEMPTS
from models.schemas.user_schema import (
    ChangeEmail,
    ChangePassword,
    ConfirmEmailChange,
    DeleteAccount,
    ForgotPassword,
    ResetPassword,
    SetPassword,
    UpdateProfile,
)
from repositories.auth_repository import AuthRepository
from repositories.refresh_token_repository import RefreshTokenRepository
from services.emailing.otp_service import OtpService
from services.storage.object_storage import ObjectStorage, is_stored_key

CLEARABLE_PROFILE_FIELDS = frozenset({"last_name", "avatar_url"})


def confirmation_matches(confirmation: str, email: str) -> bool:
    value = confirmation.strip()
    return bool(value) and normalize_email(value) == email


class UserProfile:
    def __init__(
        self,
        auth_repo: AuthRepository,
        refresh_token_repo: RefreshTokenRepository,
        otp_service: OtpService,
        storage: ObjectStorage,
    ):
        self.repo = auth_repo
        self.rt_repo = refresh_token_repo
        self.otp_svc = otp_service
        self.storage = storage

    async def _get_user(self, user_id: str):
        db_user = await self.repo.get_user_by_id(user_id)
        if not db_user:
            raise UserNotFound()
        return db_user

    async def update_profile(self, user_id: str, data: UpdateProfile):
        data_dict = {
            field: value
            for field, value in data.model_dump(mode="json", exclude_unset=True).items()
            if value is not None or field in CLEARABLE_PROFILE_FIELDS
        }
        if not data_dict:
            raise NoFieldsToUpdate()

        await self._get_user(user_id)
        await self.repo.update_user(user_id, data_dict)
        return {"message": "Profile updated successfully"}

    async def change_password(self, user_id: str, data: ChangePassword):
        db_user = await self._get_user(user_id)

        if not db_user.password_hash:
            raise GoogleOnlyAccount()

        if not await Security.verify_password_async(db_user.password_hash, data.current_password):
            raise IncorrectCurrentPassword()

        if await Security.verify_password_async(db_user.password_hash, data.new_password):
            raise SamePasswordAsBefore()

        # update_password et non update_user : il efface aussi le code de
        # reinitialisation en cours. Sans cela, un code parti par courriel
        # resterait valide un quart d'heure apres le changement.
        await self.repo.update_password(
            user_id, await Security.hash_password_async(data.new_password)
        )
        await self.rt_repo.revoke_all_for_user(user_id)

        return {"message": "Password changed successfully"}

    async def set_password(self, user_id: str, data: SetPassword):
        db_user = await self._get_user(user_id)

        if db_user.password_hash:
            raise PasswordAlreadySet()

        await self.repo.update_user(user_id, {
            "password_hash": await Security.hash_password_async(data.new_password),
        })

        return {"message": "Password set successfully"}

    async def forgot_password(self, data: ForgotPassword):
        email = normalize_email(data.email)
        db_user = await self.repo.get_user_by_email(email)

        if db_user and db_user.is_active and db_user.password_hash:
            try:
                await self.otp_svc.send_reset_otp(email, db_user=db_user)
            except TooManyCodeRequests:
                pass

        return {"message": "If this email is registered, a reset code has been sent"}

    async def reset_password(self, data: ResetPassword):
        db_user = await self.repo.get_user_by_email(normalize_email(data.email))
        if not db_user or not db_user.reset_code_hash:
            raise InvalidOrExpiredResetCode()

        if not db_user.is_active:
            raise AccountDisabled()

        user_id = str(db_user.id)
        if await self.repo.attempts(user_id, "reset") >= MAX_VERIFICATION_ATTEMPTS:
            raise TooManyVerificationAttempts()

        await self.repo.bump_attempts(user_id, "reset")

        expires_at = db_user.reset_code_expires_at
        if not expires_at or expires_at < datetime.now(timezone.utc):
            raise ResetCodeExpired()

        if not Security.verify_otp(data.code, db_user.reset_code_hash):
            raise InvalidResetCode()

        if await Security.verify_password_async(db_user.password_hash, data.new_password):
            raise SamePasswordAsBefore()

        await self.repo.update_password(
            user_id, await Security.hash_password_async(data.new_password)
        )
        await self.repo.clear_attempts(user_id, "reset")
        await self.rt_repo.revoke_all_for_user(user_id)

        return {"message": "Password reset successfully"}

    async def request_email_change(self, user_id: str, data: ChangeEmail):
        db_user = await self._get_user(user_id)

        if not db_user.password_hash:
            raise GoogleOnlyAccount()

        if not await Security.verify_password_async(db_user.password_hash, data.password):
            raise IncorrectPassword()

        new_email = normalize_email(data.new_email)
        if new_email == db_user.email:
            raise SameEmailAsCurrent()

        if is_disposable_email(new_email):
            raise DisposableEmailNotAllowed()

        if await self.repo.get_user_by_email(new_email):
            raise EmailAlreadyInUse()

        await self.otp_svc.send_email_change_otp(new_email, user_id, db_user=db_user)
        await self.repo.update_user(user_id, {"pending_email": new_email})

        return {"message": "Verification code sent to new address"}

    async def confirm_email_change(self, user_id: str, data: ConfirmEmailChange):
        db_user = await self._get_user(user_id)

        pending_email = db_user.pending_email
        if not pending_email:
            raise NoPendingEmailChange()

        if not db_user.email_change_code_hash:
            raise NoEmailChangeCode()

        if await self.repo.attempts(user_id, "email_change") >= MAX_VERIFICATION_ATTEMPTS:
            raise TooManyVerificationAttempts()

        await self.repo.bump_attempts(user_id, "email_change")

        expires_at = db_user.email_change_code_expires_at
        if not expires_at or expires_at < datetime.now(timezone.utc):
            raise VerificationCodeExpired()

        if not Security.verify_otp(data.code, db_user.email_change_code_hash):
            raise InvalidVerificationCode()

        existing = await self.repo.get_user_by_email(pending_email)
        if existing and str(existing.id) != user_id:
            raise EmailAlreadyInUse()

        await self.repo.update_user(user_id, {
            "email": pending_email,
            "pending_email": None,
            "email_change_code_hash": None,
            "email_change_code_expires_at": None,
        })
        await self.repo.clear_attempts(user_id, "email_change")

        return {"message": "Email changed successfully"}

    async def delete_account(self, user_id: str, data: DeleteAccount):
        db_user = await self._get_user(user_id)

        if db_user.password_hash:
            if not data.password or not await Security.verify_password_async(
                db_user.password_hash, data.password
            ):
                raise IncorrectPassword()
        elif not confirmation_matches(data.confirmation or "", db_user.email):
            raise InvalidDeletionConfirmation()

        avatar_key = db_user.avatar_key

        await self.repo.delete_user(user_id)

        if is_stored_key(avatar_key):
            await self.storage.delete(avatar_key)

        return {"message": "Account deleted successfully"}
