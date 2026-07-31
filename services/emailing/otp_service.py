from datetime import datetime, timedelta, timezone
from core.exceptions import TooManyCodeRequests
from core.security import Security
from repositories.auth_repository import AuthRepository
from services.emailing.email_sender import EmailSender

MAX_RESEND_PER_HOUR = 5
OTP_EXPIRY_MINUTES = 15


class OtpService:
    def __init__(self, auth_repo: AuthRepository):
        self.repo = auth_repo
        self.email_sender = EmailSender()

    async def send_verification_otp(self, email: str, user_id: str, db_user=None):
        if db_user:
            self._check_resend_rate_limit(db_user)

        otp_code = Security.generate_otp_code()
        code_hash = Security.hash_token(otp_code)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRY_MINUTES)
        now = datetime.now(timezone.utc)

        new_resend_count = self._compute_resend_count(db_user, now) if db_user else 1

        await self.repo.update_user(user_id, {
            "verification_code_hash": code_hash,
            "verification_code_expires_at": expires_at,
            "verification_attempts": 0,
            "last_code_sent_at": now,
            "code_resend_count": new_resend_count,
        })
        
        await self.email_sender.send_verification_email(email, code=otp_code)

    async def send_reset_otp(self, email: str, db_user=None):
        if db_user:
            self._check_resend_rate_limit(db_user)

        otp_code = Security.generate_otp_code()
        code_hash = Security.hash_token(otp_code)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRY_MINUTES)
        now = datetime.now(timezone.utc)

        new_resend_count = self._compute_resend_count(db_user, now) if db_user else 1

        await self.repo.save_reset_code(email, code_hash, expires_at, now, new_resend_count)
        await self.email_sender.send_reset_password_email(email, code=otp_code)

    async def send_email_change_otp(self, pending_email: str, user_id: str, db_user=None):
        if db_user:
            self._check_resend_rate_limit(db_user)

        otp_code = Security.generate_otp_code()
        code_hash = Security.hash_token(otp_code)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRY_MINUTES)
        now = datetime.now(timezone.utc)

        new_resend_count = self._compute_resend_count(db_user, now) if db_user else 1

        await self.repo.update_user(user_id, {
            "email_change_code_hash": code_hash,
            "email_change_code_expires_at": expires_at,
            "verification_attempts": 0,
            "last_code_sent_at": now,
            "code_resend_count": new_resend_count,
        })
        await self.email_sender.send_email_change_email(pending_email, code=otp_code)

    @staticmethod
    def _check_resend_rate_limit(db_user):
        if db_user.last_code_sent_at:
            one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
            if db_user.last_code_sent_at > one_hour_ago and db_user.code_resend_count >= MAX_RESEND_PER_HOUR:
                raise TooManyCodeRequests()

    @staticmethod
    def _compute_resend_count(db_user, now: datetime) -> int:
        one_hour_ago = now - timedelta(hours=1)
        if db_user.last_code_sent_at and db_user.last_code_sent_at > one_hour_ago:
            return db_user.code_resend_count + 1
        return 1