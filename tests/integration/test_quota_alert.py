import pytest

from jobs import daily
from jobs.daily import RESEND_DAILY_ALERT_THRESHOLD, exit_code, run_daily
from services.emailing.email_sender import EmailSender
from services.notifications.reminder_service import ReminderService

pytestmark = pytest.mark.integration

SEUIL = RESEND_DAILY_ALERT_THRESHOLD
OPERATEUR = "ops@example.com"

VIDE = {
    "users": 0,
    "emails_sent": 0,
    "occurrences": 0,
    "overdue": 0,
    "actions": 0,
    "skipped": 0,
    "failed": 0,
}


@pytest.fixture
def run_job(session_runner, monkeypatch):
    # Atteindre 81 envois reels demanderait 81 comptes : on remplace le passage
    # des rappels par son resultat, ce que l'alerte est seule a lire.
    def go(emails_sent, operator=OPERATEUR, failed=0):
        async def send_due(self, *, at=None):
            return {**VIDE, "emails_sent": emails_sent, "failed": failed}

        monkeypatch.setattr(ReminderService, "send_due", send_due)
        monkeypatch.setattr(daily, "OPERATOR_EMAIL", operator)
        return session_runner(lambda session: run_daily(session))

    return go


@pytest.fixture
def alerts(mailbox):
    return lambda: [message for message in mailbox if message["kind"] == "admin"]


class TestWhenItSpeaks:
    def test_one_above_the_threshold_it_leaves(self, run_job, alerts):
        run_job(SEUIL + 1)

        assert len(alerts()) == 1
        assert alerts()[0]["to"] == OPERATEUR

    def test_on_the_threshold_it_stays_silent(self, run_job, alerts):
        run_job(SEUIL)

        assert alerts() == []

    def test_under_the_threshold_it_stays_silent(self, run_job, alerts):
        run_job(SEUIL - 1)

        assert alerts() == []

    def test_without_an_operator_address_it_stays_silent(self, run_job, alerts):
        run_job(SEUIL + 1, operator=None)

        assert alerts() == []

    def test_it_leaves_once_per_run(self, run_job, alerts):
        run_job(SEUIL + 40)

        assert len(alerts()) == 1


class TestWhatItSays:
    def test_it_carries_the_number_of_the_day_and_the_ceiling(self, run_job, monkeypatch):
        capture = {}

        async def spy(self, to, subject, body_text):
            capture.update(to=to, subject=subject, body=body_text)
            return {"id": "test"}

        monkeypatch.setattr(EmailSender, "send_admin_email", spy)

        run_job(SEUIL + 3)

        assert str(SEUIL + 3) in capture["body"]
        assert "100" in capture["body"]
        assert str(SEUIL) in capture["body"]


class TestItNeverBreaksTheRun:
    def test_the_alert_is_not_counted_in_the_report(self, run_job):
        report = run_job(SEUIL + 1)

        assert report["emails_sent"] == SEUIL + 1
        assert report["users"] == 0
        assert report["failed"] == 0

    def test_a_failing_alert_changes_neither_report_nor_exit_code(self, run_job, monkeypatch):
        # L'alerte est le thermometre : casse, il ne doit pas declarer le
        # malade mort. Le code de sortie reste celui des rappels utilisateurs.
        async def boom(self, to, subject, body_text):
            raise RuntimeError("resend is down")

        monkeypatch.setattr(EmailSender, "send_admin_email", boom)

        report = run_job(SEUIL + 1)

        assert report["failed"] == 0
        assert report["emails_sent"] == SEUIL + 1
        assert exit_code(report) == 0

    def test_a_failing_reminder_still_fails_the_run(self, run_job):
        report = run_job(SEUIL + 1, failed=2)

        assert exit_code(report) == 1
