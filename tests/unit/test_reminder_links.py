import asyncio
from datetime import date
from decimal import Decimal

import pytest

from core.config import FRONTEND_URL
from services.emailing.email_sender import EmailSender

pytestmark = pytest.mark.unit

# La fixture mailbox remplace ces methodes pour toute la suite.
REAL = {
    "notice": EmailSender.send_reminder_email,
    "overdue": EmailSender.send_overdue_email,
    "action": EmailSender.send_action_email,
}

BASE = {
    "title": "Netflix",
    "due_date": date(2026, 9, 4),
    "amount": Decimal("18.99"),
    "days_left": 2,
}

ITEMS = {
    "notice": [BASE],
    "overdue": [BASE],
    "action": [{**BASE, "reason": "trial", "deadline": date(2026, 9, 2)}],
}


def mail(kind, locale="fr"):
    captured = {}

    async def fake_send(self, params):
        captured.update(params)
        return {"id": "test"}

    sender = EmailSender()
    sender._send = fake_send.__get__(sender, EmailSender)
    asyncio.run(
        REAL[kind](
            sender,
            "alexis@example.com",
            first_name="Alexis",
            items=ITEMS[kind],
            currency="CAD",
            locale=locale,
        )
    )
    return captured


class TestEveryReminderLeadsBack:
    @pytest.mark.parametrize("kind", list(REAL))
    def test_the_rich_version_carries_a_button(self, kind):
        assert f'href="{FRONTEND_URL}' in mail(kind)["html"]

    @pytest.mark.parametrize("kind", list(REAL))
    def test_the_plain_version_carries_the_same_address(self, kind):
        # Le bouton n'existe que dans la version riche. Sans cette ligne, un
        # lecteur en texte seul n'a aucun chemin vers l'application, et le seul
        # lien du message est celui du desabonnement.
        lignes = [
            line
            for line in mail(kind)["text"].splitlines()
            if FRONTEND_URL in line and "/rappels" not in line
        ]

        assert len(lignes) == 1, mail(kind)["text"]

    @pytest.mark.parametrize("kind", list(REAL))
    def test_the_label_says_where_it_goes(self, kind):
        ligne = next(
            line
            for line in mail(kind)["text"].splitlines()
            if FRONTEND_URL in line and "/rappels" not in line
        )
        label, adresse = ligne.split(" : ", 1)

        assert label.strip()
        assert adresse.startswith(FRONTEND_URL)

    def test_an_upcoming_reminder_points_at_the_calendar(self):
        # C'est la ou les echeances sont datees ; la racine ferait chercher.
        assert f"{FRONTEND_URL}/calendrier" in mail("notice")["html"]

    def test_the_label_follows_the_account_language(self):
        assert "Voir le calendrier" in mail("notice", "fr")["text"]
        assert "See the calendar" in mail("notice", "en")["text"]
