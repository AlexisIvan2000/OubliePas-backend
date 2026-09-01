import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from core.clock import MAX_TIMEZONE_LENGTH, is_known_timezone
from core.validators import normalize_currency
from models.db.commitments_db import MAX_REMINDER_DAYS

SPECIAL_CHARACTERS = r"[!@#$%^&*(),.?\":{}|<>_+=\[\]\\;'`~-]"


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
    # avatar_url n'est pas ici : ouverte au client, elle contournerait le
    # plafond, le nettoyeur d'image et le stockage, et ferait fuir l'adresse IP
    # du visiteur vers le serveur de son choix. Les champs inconnus sont refusés
    # plutôt qu'ignorés, pour que le refus se voie.
    model_config = ConfigDict(extra="forbid")

    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    reminder_email_enabled: bool | None = None
    reminder_push_enabled: bool | None = None
    reminder_notice_enabled: bool | None = None
    reminder_overdue_enabled: bool | None = None
    reminder_action_enabled: bool | None = None
    reminder_weekly_enabled: bool | None = None
    default_reminder_days: int | None = Field(default=None, ge=0, le=MAX_REMINDER_DAYS)
    locale: Literal["fr", "en"] | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=MAX_TIMEZONE_LENGTH)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        # Contre la base tz réellement installée, jamais une liste recopiée :
        # c'est elle qui décidera du jour de cette personne.
        if value is None or is_known_timezone(value):
            return value
        raise ValueError("Unknown time zone")

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
    # Aucun plafond sur le mot de passe : un 422 sur un mot de passe trop long
    # dirait a l'appelant que sa longueur, elle, ne convenait pas. Le service le
    # traite comme un mot de passe faux, verification a vide comprise.
    password: str | None = None
    confirmation: str | None = Field(default=None, max_length=255)
