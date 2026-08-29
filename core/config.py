from dotenv import load_dotenv
import os

load_dotenv()

SAMESITE_VALUES = ("lax", "strict", "none")


def check_cookie_policy(samesite: str, secure: bool) -> None:
    if samesite not in SAMESITE_VALUES:
        raise RuntimeError(
            f"COOKIE_SAMESITE must be one of {', '.join(SAMESITE_VALUES)}, "
            f"got {samesite!r}. A value the browser does not understand makes it "
            f"drop the refresh cookie without a word."
        )
    if samesite == "none" and not secure:
        raise RuntimeError(
            "COOKIE_SAMESITE=none requires COOKIE_SECURE=true: browsers refuse a "
            "cross-site cookie that is not Secure, and they refuse it silently. "
            "Serve the API over HTTPS and set COOKIE_SECURE=true."
        )


def check_cors_policy(origins: list[str]) -> None:
    if "*" in origins:
        raise RuntimeError(
            "CORS_ORIGINS cannot be '*' while credentials are allowed: browsers "
            "reject that combination. List the exact front-end origins instead."
        )


# Le pilote asynchrone ne se devine pas depuis un URL "postgresql://", que
# toutes les plateformes fournissent sous cette forme. La regle vit ici pour
# qu'Alembic l'applique aussi : sans elle, un -x db_url colle depuis le tableau
# de bord echoue sur une erreur de pilote qui affiche le mot de passe.
def to_async_url(raw: str) -> str:
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    return raw


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Set it before starting the app."
        )
    return value

JWT_SECRET_KEY = _require("JWT_SECRET_KEY")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "30"))

_raw_db_url = os.getenv("DB_URL") or os.getenv("DATABASE_URL")
if not _raw_db_url:
    raise RuntimeError(
        "Missing required environment variable: DATABASE_URL (or DB_URL). "
        "Set it before starting the app."
    )

DATABASE_URL = to_async_url(_raw_db_url)

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

DEBUG = os.getenv("DEBUG", "false").lower() in ("1", "true", "yes")

SQL_ECHO = os.getenv("SQL_ECHO", "false").lower() in ("1", "true", "yes")

CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", FRONTEND_URL).split(",")
    if origin.strip()
]

check_cors_policy(CORS_ORIGINS)

_DEV_ORIGIN_REGEX = r"^http://(localhost|127\.0\.0\.1)(:\d+)?$"

CORS_ORIGIN_REGEX = os.getenv("CORS_ORIGIN_REGEX") or (_DEV_ORIGIN_REGEX if DEBUG else None)

REFRESH_COOKIE_NAME = os.getenv("REFRESH_COOKIE_NAME", "oubliepas_refresh")
REFRESH_COOKIE_PATH = "/v1/auth"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false" if DEBUG else "true").lower() in ("1", "true", "yes")
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "lax").lower()
COOKIE_DOMAIN = os.getenv("COOKIE_DOMAIN") or None
check_cookie_policy(COOKIE_SAMESITE, COOKIE_SECURE)

REDIS_URL = os.getenv("REDIS_URL")

TRUSTED_PROXY_COUNT = int(os.getenv("TRUSTED_PROXY_COUNT", "0"))

RESEND_API_KEY = _require("RESEND_API_KEY")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "hello@oubliepas.com")
RESEND_FROM_NAME = os.getenv("RESEND_FROM_NAME", "OubliePas")
RESEND_FROM_EMAIL_SUPPORT = os.getenv("RESEND_FROM_EMAIL_SUPPORT", "support@oubliepas.com")
RESEND_FROM_EMAIL_REMINDER = os.getenv("RESEND_FROM_EMAIL_REMINDER", "reminder@oubliepas.com")
# Supervision, pas dependance : absente, l'alerte de quota se tait au lieu
# d'empecher le demarrage.
OPERATOR_EMAIL = os.getenv("OPERATOR_EMAIL")

API_S3 = os.getenv("API_S3")
S3_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID")
S3_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY")
JETON_VALUE = os.getenv("JETON_VALUE")
FOLDER_NAME = os.getenv("FOLDER_NAME", "avatars")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "oubliepas")
AVATAR_URL_EXPIRE_SECONDS = int(os.getenv("AVATAR_URL_EXPIRE_SECONDS", str(24 * 3600)))
