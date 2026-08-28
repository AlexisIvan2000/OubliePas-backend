import asyncio
from datetime import date
from decimal import Decimal
from html import unescape

import pytest

from core.config import RESEND_FROM_EMAIL, RESEND_FROM_EMAIL_REMINDER
from services.emailing.email_sender import EmailSender

# La fixture mailbox remplace ces methodes pour toute la suite : on garde une
# reference sur les vraies avant qu'elle ne passe.
REAL = {
    "verify": EmailSender.send_verification_email,
    "reset": EmailSender.send_reset_password_email,
    "change": EmailSender.send_email_change_email,
    "reminder": EmailSender.send_reminder_email,
}

pytestmark = pytest.mark.unit


def send(kind, *args, **kwargs):
    captured = {}

    async def fake_send(self, params):
        captured.update(params)
        return {"id": "test"}

    sender = EmailSender()
    sender._send = fake_send.__get__(sender, EmailSender)
    asyncio.run(REAL[kind](sender, *args, **kwargs))
    return captured


def code_mail(kind="verify", locale="fr", code="482913"):
    return send(kind, "alexis@example.com", code=code, locale=locale)


def reminder():
    return send(
        "reminder",
        "alexis@example.com",
        first_name="Alexis",
        items=[{
            "title": "Netflix",
            "due_date": date(2026, 9, 4),
            "amount": Decimal("18.99"),
            "days_left": 2,
        }],
        currency="CAD",
        locale="fr",
    )


class TestLocale:
    def test_the_verification_subject_follows_the_account_language(self):
        assert code_mail(locale="fr")["subject"] == "Vérification de votre adresse"
        assert code_mail(locale="en")["subject"] == "Verify your email address"

    def test_the_reset_subject_follows_it_too(self):
        assert code_mail("reset", "fr")["subject"] == "Réinitialisation du mot de passe"
        assert code_mail("reset", "en")["subject"] == "Password reset"

    def test_the_change_subject_follows_it_too(self):
        assert code_mail("change", "fr")["subject"] == "Confirmation du changement d'adresse"
        assert code_mail("change", "en")["subject"] == "Confirm your new email address"

    def test_an_unknown_language_falls_back_to_french(self):
        assert "Bienvenue sur OubliePas" in code_mail(locale="de")["html"]

    def test_the_document_declares_the_language(self):
        assert '<html lang="en">' in code_mail(locale="en")["html"]


class TestBody:
    def test_the_code_is_in_the_html_and_in_the_plain_text(self):
        mail = code_mail(code="739104")

        assert "739104" in mail["html"]
        assert "739104" in mail["text"]

    def test_the_plain_text_carries_no_markup(self):
        assert "<" not in code_mail()["text"]

    def test_the_expiry_is_stated(self):
        assert "Ce code expire dans 15 minutes." in code_mail()["html"]

    def test_the_footer_tells_what_to_do_when_it_was_not_you(self):
        # L'apostrophe ressort echappee : on relit ce que le lecteur verra.
        lisible = unescape(code_mail()["html"])

        assert "Si vous n'avez pas créé de compte, ignorez ce message." in lisible


class TestEnvelope:
    def test_a_code_mail_leaves_from_the_main_address(self):
        assert code_mail()["from"].endswith(f"<{RESEND_FROM_EMAIL}>")

    def test_a_reminder_leaves_from_the_reminder_address(self):
        assert reminder()["from"].endswith(f"<{RESEND_FROM_EMAIL_REMINDER}>")

    def test_the_two_addresses_are_not_the_same(self):
        assert RESEND_FROM_EMAIL != RESEND_FROM_EMAIL_REMINDER

    def test_a_code_mail_carries_no_unsubscribe_header(self):
        # Se desabonner d'un code de verification n'a pas de sens : l'en-tete
        # appartient aux seuls rappels, que l'utilisateur peut couper.
        assert "headers" not in code_mail()

    def test_a_reminder_still_carries_one(self):
        assert reminder()["headers"]["List-Unsubscribe"].endswith("/rappels>")


class TestBrand:
    def test_the_header_names_the_app(self):
        header = code_mail()["html"]

        assert "OubliePas" in header

    def test_no_mail_carries_an_image(self):
        # Aucune image dans les courriels : la plupart des boites les bloquent
        # par defaut, et une adresse distante est une dependance de plus qui
        # peut mourir apres l'envoi.
        for mail in (code_mail(), code_mail("reset"), code_mail("change"), reminder()):
            assert "<img" not in mail["html"]

    def test_the_palette_is_the_one_of_the_app(self):
        body = code_mail()["html"]

        assert "#0f2233" in body
        assert "#f1f5f9" in body
        assert "0d9488" not in body.lower()
        assert "4db6ac" not in body.lower()
