import re

from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator

from core.validators import normalize_currency
from models.db.commitments_db import MAX_REMINDER_DAYS

SPECIAL_CHARACTERS = r"[!@#$%^&*(),.?\":{}|<>_+=\[\]\\;'`~-]"
MAX_AVATAR_URL_LENGTH = 2048


def validate_password_strength(value: str) -> str:
    if len(value) < 8:
        raise ValueError("Password must be at least 8 characters long")
    if not re.search(r"[A-Z]", value):
        raise ValueError("Password must contain at least one uppercase letter")
    if not re.search(r"[a-z]", value):
        raise ValueError("Password must contain at least one lowercase letter")
    if not re.search(SPECIAL_CHARACTERS, value):
        raise ValueError("Password must contain at least one special character")
    return value


class UpdateProfile(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    avatar_url: HttpUrl | None = None
    reminder_email_enabled: bool | None = None
    default_reminder_days: int | None = Field(default=None, ge=0, le=MAX_REMINDER_DAYS)

    @field_validator("avatar_url")
    @classmethod
    def validate_avatar_url(cls, value: HttpUrl | None) -> HttpUrl | None:
        if value is not None and len(str(value)) > MAX_AVATAR_URL_LENGTH:
            raise ValueError(f"Avatar URL must be at most {MAX_AVATAR_URL_LENGTH} characters")
        return value

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return normalize_currency(value)


class ChangePassword(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return validate_password_strength(value)


class SetPassword(BaseModel):
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return validate_password_strength(value)


class ForgotPassword(BaseModel):
    email: EmailStr


class ResetPassword(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return validate_password_strength(value)


class ChangeEmail(BaseModel):
    new_email: EmailStr
    password: str


class ConfirmEmailChange(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class DeleteAccount(BaseModel):
    password: str | None = Field(default=None, max_length=128)
    confirmation: str | None = Field(default=None, max_length=255)
