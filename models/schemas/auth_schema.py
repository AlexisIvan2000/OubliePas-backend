import re
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from core.clock import DEFAULT_TIMEZONE, MAX_TIMEZONE_LENGTH, is_known_timezone
from core.validators import normalize_currency
from models.db.commitments_db import MAX_COMMITMENTS_PER_TYPE
from models.db.user_db import DEFAULT_LOCALE

DEFAULT_CURRENCY = "CAD"


class UserCreate(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str
    currency: str = Field(default=DEFAULT_CURRENCY, min_length=3, max_length=3)
    locale: Literal["fr", "en"] = DEFAULT_LOCALE
    # Le navigateur le connaît, le serveur ne pourrait que le déduire d'une
    # adresse IP. Absent, on garde UTC et la connexion suivante corrigera.
    timezone: str = Field(default=DEFAULT_TIMEZONE, min_length=1, max_length=MAX_TIMEZONE_LENGTH)

    @field_validator("timezone")
    def validate_timezone(cls, value: str) -> str:
        return value if is_known_timezone(value) else DEFAULT_TIMEZONE

    @field_validator("currency")
    def validate_currency(cls, value: str) -> str:
        return normalize_currency(value)

    @field_validator("password")
    def validate_password(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("Password must be at least 8 characters long")
        if not re.search(r"[A-Z]", value):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"[a-z]", value):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r"[!@#$%^&*(),.?\":{}|<>_+=\[\]\\;'`~-]", value):
            raise ValueError("Password must contain at least one special character")
        return value


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str = "user"


class VerifyEmailRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class GoogleStartRequest(BaseModel):
    state: str = Field(min_length=16, max_length=128)
    code_challenge: str = Field(min_length=43, max_length=128)


class GoogleAuthRequest(BaseModel):
    code: str = Field(min_length=1, max_length=2048)
    code_verifier: str = Field(min_length=43, max_length=128)


class GoogleStartResponse(BaseModel):
    authorization_url: str


class MessageResponse(BaseModel):
    message: str


class UserResponse(BaseModel):
    id: str
    first_name: str
    last_name: str | None = None
    email: EmailStr
    is_verified: bool
    role: str
    avatar_url: str | None = None
    has_custom_avatar: bool = False
    has_password: bool = False
    currency: str = DEFAULT_CURRENCY
    reminder_email_enabled: bool = True
    reminder_push_enabled: bool = False
    reminder_notice_enabled: bool = True
    reminder_overdue_enabled: bool = True
    reminder_action_enabled: bool = True
    reminder_weekly_enabled: bool = False
    default_reminder_days: int = 3
    locale: str = DEFAULT_LOCALE
    timezone: str = DEFAULT_TIMEZONE
    commitment_limit: int = MAX_COMMITMENTS_PER_TYPE
