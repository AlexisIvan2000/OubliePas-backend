from datetime import datetime, timedelta, timezone

from core.exceptions import TooManyCodeRequests, TooManyVerificationAttempts
from core.security import Security
from models.db.user_db import MAX_VERIFICATION_ATTEMPTS
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



async def check_otp(repo, db_user, kind: str, code: str, *, expired, invalid) -> None:
    """Le meme sequenceur pour les trois codes, les erreurs en parametres.

    Les trois flux n'appellent pas les memes exceptions : la reinitialisation
    dit ResetCodeExpired la ou les deux autres disent VerificationCodeExpired,
    et le front s'appuie sur ces codes pour choisir son message.
    """
    user_id = str(db_user.id)

    if await repo.attempts(user_id, kind) >= MAX_VERIFICATION_ATTEMPTS:
        raise TooManyVerificationAttempts()

    # L'incrément précède la comparaison, et c'est la propriété de sécurité :
    # compté après coup, un essai qui échoue ne coûterait rien, puisque la
    # transaction est validée malgré l'exception.
    await repo.bump_attempts(user_id, kind)

    hash_column, expires_column = CODE_COLUMNS[kind]

    expires_at = getattr(db_user, expires_column)
    if not expires_at or expires_at < datetime.now(timezone.utc):
        raise expired()

    if not Security.verify_otp(code, getattr(db_user, hash_column)):
        raise invalid()


class OtpService:
    def __init__(self, auth_repo: AuthRepository):
        self.repo = auth_repo
        self.email_sender = EmailSender()

    async def _issue(self, kind: str, user_id: str, *, db_user, locale, write, send):
        # Le même trajet pour les trois codes. Seuls la colonne visée, la clé
        # d'écriture et le courriel changent, et ils arrivent en paramètres.
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
        # Un code neuf rend ses essais au flux qui l'a demandé, et à lui seul :
        # demander une vérification ne déverrouille pas une réinitialisation
        # épuisée.
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
