from datetime import datetime, timedelta, timezone

from core.exceptions import TooManyCodeRequests
from core.security import Security
from repositories.auth_repository import AuthRepository
from services.emailing.email_sender import EmailSender
from services.emailing.messages import DEFAULT_LOCALE

MAX_RESEND_PER_HOUR = 5
OTP_EXPIRY_MINUTES = 15

CODE_COLUMNS = {
    "verification": ("verification_code_hash", "verification_code_expires_at"),
    "reset": ("reset_code_hash", "reset_code_expires_at"),
    "email_change": ("email_change_code_hash", "email_change_code_expires_at"),
}


class OtpService:
    def __init__(self, auth_repo: AuthRepository):
        self.repo = auth_repo
        self.email_sender = EmailSender()

    async def _issue(self, kind: str, user_id: str, *, db_user, locale, write, send):
        # Le trajet est le meme pour les trois codes : garde de renvoi, tirage,
        # empreinte, expiration, compteur. Seuls la colonne visee, la cle
        # d'ecriture et le courriel changent, et ils arrivent en parametres.
        if db_user:
            self._check_resend_rate_limit(db_user)

        code = Security.generate_otp_code()
        now = datetime.now(timezone.utc)
        hash_column, expires_column = CODE_COLUMNS[kind]

        await write({
            hash_column: Security.hash_token(code),
            expires_column: now + timedelta(minutes=OTP_EXPIRY_MINUTES),
            "last_code_sent_at": now,
            "code_resend_count": self._compute_resend_count(db_user, now) if db_user else 1,
        })
        # Un code neuf rend ses essais au flux qui l'a demande, et a lui seul :
        # demander une verification ne doit pas deverrouiller une
        # reinitialisation epuisee.
        if user_id:
            await self.repo.clear_attempts(user_id, kind)

        await send(code, locale or self._locale(db_user))

    async def send_verification_otp(
        self, email: str, user_id: str, db_user=None, locale: str | None = None
    ):
        await self._issue(
            "verification",
            user_id,
            db_user=db_user,
            locale=locale,
            write=lambda data: self.repo.update_user(user_id, data),
            send=lambda code, lang: self.email_sender.send_verification_email(
                email, code=code, locale=lang
            ),
        )

    async def send_reset_otp(self, email: str, db_user=None, locale: str | None = None):
        await self._issue(
            "reset",
            str(db_user.id) if db_user else None,
            db_user=db_user,
            locale=locale,
            write=lambda data: self.repo.update_user_by_email(email, data),
            send=lambda code, lang: self.email_sender.send_reset_password_email(
                email, code=code, locale=lang
            ),
        )

    async def send_email_change_otp(
        self, pending_email: str, user_id: str, db_user=None, locale: str | None = None
    ):
        await self._issue(
            "email_change",
            user_id,
            db_user=db_user,
            locale=locale,
            write=lambda data: self.repo.update_user(user_id, data),
            send=lambda code, lang: self.email_sender.send_email_change_email(
                pending_email, code=code, locale=lang
            ),
        )

    @staticmethod
    def _locale(db_user) -> str:
        return getattr(db_user, "locale", None) or DEFAULT_LOCALE

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
