import asyncio
from datetime import date
from decimal import Decimal

import pytest

from services.emailing.email_sender import EmailSender, _due_label

REAL_SEND = EmailSender.send_reminder_email

pytestmark = pytest.mark.unit


def render(items, currency="CAD", first_name="Alexis", locale="fr"):
    captured = {}

    async def fake_send(self, params):
        captured.update(params)
        return {"id": "test"}

    sender = EmailSender()
    sender._send = fake_send.__get__(sender, EmailSender)
    asyncio.run(
        REAL_SEND(
            sender,
            "alexis@example.com",
            first_name=first_name,
            items=items,
            currency=currency,
            locale=locale,
        )
    )
    return captured


def item(**overrides):
    return {
        "title": "Netflix",
        "due_date": date(2026, 8, 4),
        "amount": Decimal("18.99"),
        "days_left": 2,
        **overrides,
    }


class TestDueLabel:
    @pytest.mark.parametrize(
        "days,expected",
        [(0, "aujourd'hui"), (1, "demain"), (2, "dans 2 jours"), (-1, "aujourd'hui")],
    )
    def test_wording(self, days, expected):
        assert _due_label(days) == expected


class TestSubject:
    def test_singular(self):
        assert render([item()])["subject"] == "1 échéance à venir"

    def test_plural(self):
        assert render([item(), item(title="Spotify")])["subject"] == "2 échéances à venir"


class TestBody:
    def test_lists_every_item(self):
        body = render([item(), item(title="Spotify")])["html"]

        assert "Netflix" in body
        assert "Spotify" in body

    def test_formats_the_amount_in_french(self):
        body = render([item(amount=Decimal("1042.5"))])["html"]

        assert "1\u00a0042,50\u00a0CAD" in body

    def test_formats_the_amount_in_english(self):
        body = render([item(amount=Decimal("1042.5"))], locale="en")["html"]

        assert "1,042.50 CAD" in body

    def test_formats_the_due_date_in_french(self):
        assert "4 ao\u00fbt 2026" in render([item()])["html"]

    def test_formats_the_due_date_in_english(self):
        assert "Aug 4, 2026" in render([item()], locale="en")["html"]

    def test_greets_the_user(self):
        assert "Bonjour Alexis," in render([item()])["html"]

    def test_greets_the_user_in_english(self):
        assert "Hi Alexis," in render([item()], locale="en")["html"]

    def test_carries_a_plain_text_alternative(self):
        captured = render([item()])

        assert "Netflix" in captured["text"]
        assert "<div" not in captured["text"]

    def test_carries_an_unsubscribe_header(self):
        captured = render([item()])

        assert captured["headers"]["List-Unsubscribe"].startswith("<http")
        assert captured["headers"]["List-Unsubscribe"].endswith("/rappels>")

    def test_an_unknown_locale_falls_back_to_french(self):
        assert "Bonjour Alexis," in render([item()], locale="de")["html"]

    def test_recipient(self):
        assert render([item()])["to"] == ["alexis@example.com"]


class TestEscaping:
    def test_escapes_a_hostile_title(self):
        body = render([item(title="<script>alert(1)</script>")])["html"]

        assert "<script>alert(1)</script>" not in body
        assert "&lt;script&gt;" in body

    def test_escapes_the_first_name(self):
        body = render([item()], first_name="<b>Alexis</b>")["html"]

        assert "<b>Alexis</b>" not in body
        assert "&lt;b&gt;Alexis&lt;/b&gt;" in body

    def test_escapes_the_currency(self):
        body = render([item()], currency="<i>CAD</i>")["html"]

        assert "<i>CAD</i>" not in body
