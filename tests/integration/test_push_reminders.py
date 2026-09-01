from datetime import date, datetime, timedelta, timezone

import pytest

from repositories.auth_repository import AuthRepository
from repositories.commitment_repository import CommitmentRepository
from repositories.push_repository import PushRepository
from services.emailing.email_sender import EmailSender
from jobs.daily import CRON_HOUR
from services.notifications.reminder_service import ReminderService
from services.pushing.push_sender import PushSender

pytestmark = pytest.mark.integration

ENDPOINT = "https://fcm.googleapis.com/fcm/send/telephone"


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def token(verified):
    return verified["tokens"]["access_token"]


@pytest.fixture
def abonne(client, token):
    response = client.post(
        "/v1/push/subscriptions",
        json={"endpoint": ENDPOINT, "p256dh": "cle", "auth": "secret"},
        headers=auth(token),
    )
    assert response.status_code == 201
    client.patch("/v1/users/me", json={"reminder_push_enabled": True}, headers=auth(token))
    return token


@pytest.fixture
def passage(session_runner, monkeypatch):
    # push_configured est faux en test : sans cette bascule le canal ne serait
    # jamais essaye, et les tests ne prouveraient rien.
    import services.notifications.reminder_service as service

    monkeypatch.setattr(service, "push_configured", lambda: True)

    def run(on_date=None):
        async def work(session):
            repo = CommitmentRepository(session)
            return await ReminderService(
                repo,
                AuthRepository(session),
                EmailSender(),
                push_repo=PushRepository(session),
                push_sender=PushSender(),
            ).send_due(at=datetime.combine(on_date or date.today(), CRON_HOUR, tzinfo=timezone.utc))

        return session_runner(work)

    return run


def netflix(client, token, days_ahead=2):
    due = date.today() + timedelta(days=days_ahead)
    response = client.post(
        "/v1/commitments",
        json={
            "title": "Netflix",
            "type": "subscription",
            "category": "entertainment",
            "amount": "18.99",
            "frequency": "monthly",
            "starts_on": due.isoformat(),
            "reminder_days_before": 3,
        },
        headers=auth(token),
    )
    assert response.status_code == 201
    return response.json()


class TestTheTwoChannelsTravelTogether:
    def test_one_run_sends_both(self, client, abonne, mailbox, pushbox, passage, db):
        netflix(client, abonne)

        report = passage()

        assert report["emails_sent"] == 1
        assert report["push_sent"] == 1
        assert [m["kind"] for m in mailbox if m["kind"] == "reminder"] == ["reminder"]
        assert [p["endpoint"] for p in pushbox] == [ENDPOINT]

    def test_the_log_carries_one_line_per_channel(self, client, abonne, mailbox, pushbox, passage, db):
        netflix(client, abonne)

        passage()

        rows = db("select kind, channel from occurrence_reminders order by channel")
        assert [tuple(row) for row in rows] == [("notice", "email"), ("notice", "push")]

    def test_the_second_run_sends_nothing(self, client, abonne, mailbox, pushbox, passage):
        netflix(client, abonne)
        passage()
        mailbox.clear()
        pushbox.clear()

        report = passage()

        assert report["emails_sent"] == 0
        assert report["push_sent"] == 0
        assert mailbox == []
        assert pushbox == []


class TestTheSwitchesAreIndependent:
    def test_push_off_still_sends_the_email(self, client, abonne, mailbox, pushbox, passage):
        # Le bouton de l'interface coupe le push et rien d'autre : le courriel
        # est le canal fiable, il ne doit jamais dependre de l'autre.
        client.patch(
            "/v1/users/me", json={"reminder_push_enabled": False}, headers=auth(abonne)
        )
        netflix(client, abonne)

        report = passage()

        assert report["emails_sent"] == 1
        assert report["push_sent"] == 0
        assert pushbox == []

    def test_push_off_leaves_the_subscription_alone(self, client, abonne, passage, db):
        # Rallumer ne doit pas exiger une nouvelle permission du navigateur.
        client.patch(
            "/v1/users/me", json={"reminder_push_enabled": False}, headers=auth(abonne)
        )

        assert len(db("select id from push_subscriptions")) == 1

    def test_email_off_still_sends_the_push(self, client, abonne, mailbox, pushbox, passage):
        client.patch(
            "/v1/users/me", json={"reminder_email_enabled": False}, headers=auth(abonne)
        )
        netflix(client, abonne)

        report = passage()

        assert report["emails_sent"] == 0
        assert report["push_sent"] == 1
        assert [p["endpoint"] for p in pushbox] == [ENDPOINT]

    def test_the_family_switch_silences_both(self, client, abonne, mailbox, pushbox, passage):
        client.patch(
            "/v1/users/me", json={"reminder_notice_enabled": False}, headers=auth(abonne)
        )
        netflix(client, abonne)

        report = passage()

        assert report["emails_sent"] == 0
        assert report["push_sent"] == 0


class TestWhatThePushSays:
    def test_it_never_carries_the_amount(self, client, abonne, mailbox, pushbox, passage):
        # Une notification s'affiche sur un ecran verrouille, dans un lieu
        # public : le montant n'y a pas sa place.
        netflix(client, abonne)

        passage()

        assert "18" not in pushbox[0]["body"]
        assert "18" not in pushbox[0]["title"]

    def test_it_leads_to_the_calendar(self, client, abonne, mailbox, pushbox, passage):
        netflix(client, abonne)

        passage()

        assert pushbox[0]["url"].endswith("/calendrier")


class TestADeadSubscription:
    def test_it_is_forgotten(self, client, abonne, mailbox, pushbox, passage, db, monkeypatch):
        from services.pushing.push_sender import PushSender as Sender

        async def gone(self, subscription, *, title, body, url):
            pushbox.append({"endpoint": subscription.endpoint, "title": title, "body": body, "url": url})
            return "gone"

        monkeypatch.setattr(Sender, "send", gone)
        netflix(client, abonne)

        passage()

        # Le service declare l'adresse morte ; la garder ferait echouer chaque
        # passage suivant sans que personne ne le sache.
        assert db("select id from push_subscriptions") == []

    def test_the_email_still_left(self, client, abonne, mailbox, pushbox, passage, monkeypatch):
        from services.pushing.push_sender import PushSender as Sender

        async def gone(self, subscription, *, title, body, url):
            return "gone"

        monkeypatch.setattr(Sender, "send", gone)
        netflix(client, abonne)

        report = passage()

        assert report["emails_sent"] == 1


class TestAPushFailure:
    def test_it_never_fails_the_job(self, client, abonne, mailbox, pushbox, passage, monkeypatch):
        # Un telephone endormi ne doit pas faire echouer un passage ou tous les
        # courriels sont partis : 'failed' decide du code de sortie.
        from services.pushing.push_sender import PushSender as Sender

        async def boom(self, subscription, *, title, body, url):
            raise RuntimeError("service de push injoignable")

        monkeypatch.setattr(Sender, "send", boom)
        netflix(client, abonne)

        report = passage()

        assert report["emails_sent"] == 1
        assert report["failed"] == 0
        assert report["push_failed"] == 1

    def test_the_push_is_retried_next_run(self, client, abonne, mailbox, pushbox, passage, db):
        from services.pushing.push_sender import PushSender as Sender
        import pytest as _pytest

        netflix(client, abonne)

        with _pytest.MonkeyPatch().context() as patch:
            async def boom(self, subscription, *, title, body, url):
                raise RuntimeError("injoignable")

            patch.setattr(Sender, "send", boom)
            passage()

        rows = db("select channel from occurrence_reminders")
        assert [row[0] for row in rows] == ["email"]

        report = passage()
        assert report["push_sent"] == 1
