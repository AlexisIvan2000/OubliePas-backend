from datetime import date, timedelta

import pytest

from jobs.daily import run_daily
from services.commitments.occurrence_generator import today_utc
from services.notifications.weekly_digest import week_start

pytestmark = pytest.mark.integration


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def token(verified):
    return verified["tokens"]["access_token"]


@pytest.fixture
def run_job(session_runner):
    def go(**kwargs):
        return session_runner(lambda session: run_daily(session, **kwargs))

    return go


@pytest.fixture
def digests(mailbox):
    return lambda: [message for message in mailbox if message["kind"] == "weekly"]


@pytest.fixture
def subscribed(client, token):
    client.patch("/v1/users/me", json={"reminder_weekly_enabled": True}, headers=auth(token))
    return token


def a_monday(offset_weeks: int = 0) -> date:
    reference = today_utc() + timedelta(weeks=offset_weeks)
    return week_start(reference)


def netflix(**overrides):
    return {
        "title": "Netflix",
        "type": "subscription",
        "category": "entertainment",
        "amount": "18.99",
        "frequency": "monthly",
        **overrides,
    }


def add(client, token, **overrides):
    return client.post("/v1/commitments", json=netflix(**overrides), headers=auth(token))


class TestTheWeekStart:
    @pytest.mark.parametrize("weekday", range(7))
    def test_every_day_points_at_its_own_monday(self, weekday):
        # Deux clefs pour la meme semaine feraient deux envois.
        monday = date(2026, 8, 31)
        assert week_start(monday + timedelta(days=weekday)) == monday

    def test_a_sunday_belongs_to_the_week_that_opened_it(self):
        assert week_start(date(2026, 9, 6)) == date(2026, 8, 31)


class TestTheSwitch:
    def test_nothing_goes_out_while_the_recap_is_off(self, client, token, run_job, digests):
        monday = a_monday()
        add(client, token, starts_on=(monday + timedelta(days=2)).isoformat())

        run_job(today=monday)

        assert digests() == []

    def test_it_goes_out_once_the_recap_is_on(self, client, subscribed, run_job, digests):
        monday = a_monday()
        add(client, subscribed, starts_on=(monday + timedelta(days=2)).isoformat())

        run_job(today=monday)

        assert len(digests()) == 1
        assert digests()[0]["week_start"] == monday

    def test_cutting_the_email_channel_cuts_the_recap_too(
        self, client, subscribed, run_job, digests
    ):
        monday = a_monday()
        add(client, subscribed, starts_on=(monday + timedelta(days=2)).isoformat())
        client.patch(
            "/v1/users/me", json={"reminder_email_enabled": False}, headers=auth(subscribed)
        )

        run_job(today=monday)

        assert digests() == []


class TestOncePerWeek:
    def test_a_second_run_the_same_day_sends_nothing(
        self, client, subscribed, run_job, digests
    ):
        monday = a_monday()
        add(client, subscribed, starts_on=(monday + timedelta(days=2)).isoformat())

        run_job(today=monday)
        run_job(today=monday)

        assert len(digests()) == 1

    def test_the_days_that_follow_send_nothing_either(
        self, client, subscribed, run_job, digests
    ):
        # C'est la clef, et non une garde sur le jour, qui tient la promesse.
        monday = a_monday()
        add(client, subscribed, starts_on=(monday + timedelta(days=4)).isoformat())

        run_job(today=monday)
        run_job(today=monday + timedelta(days=1))
        run_job(today=monday + timedelta(days=2))

        assert len(digests()) == 1

    def test_the_next_week_gets_its_own(self, client, subscribed, run_job, digests):
        monday = a_monday()
        add(
            client,
            subscribed,
            starts_on=(monday + timedelta(days=2)).isoformat(),
            frequency="weekly",
        )

        run_job(today=monday)
        run_job(today=monday + timedelta(days=7))

        assert len(digests()) == 2
        assert digests()[0]["week_start"] != digests()[1]["week_start"]


class TestTheCatchUp:
    def test_a_failed_monday_is_picked_up_the_next_day(
        self, client, subscribed, run_job, digests
    ):
        from services.emailing.email_sender import EmailSender

        monday = a_monday()
        add(client, subscribed, starts_on=(monday + timedelta(days=3)).isoformat())

        async def refuse(self, *args, **kwargs):
            raise RuntimeError("resend est tombe")

        with pytest.MonkeyPatch.context() as panne:
            panne.setattr(EmailSender, "send_weekly_digest_email", refuse)
            rapport = run_job(today=monday)

        assert rapport["weekly_failed"] == 1
        assert digests() == []

        run_job(today=monday + timedelta(days=1))

        assert len(digests()) == 1


class TestWhatItCarries:
    def test_an_empty_week_is_not_worth_a_message(
        self, client, subscribed, run_job, digests
    ):
        monday = a_monday()
        add(client, subscribed, starts_on=(monday + timedelta(days=40)).isoformat())

        run_job(today=monday)

        assert digests() == []

    def test_it_stops_at_sunday(self, client, subscribed, run_job, digests):
        monday = a_monday()
        add(client, subscribed, starts_on=(monday + timedelta(days=3)).isoformat())
        add(
            client,
            subscribed,
            title="Spotify",
            starts_on=(monday + timedelta(days=9)).isoformat(),
        )

        run_job(today=monday)

        titres = [item["title"] for item in digests()[0]["items"]]
        assert titres == ["Netflix"]

    def test_a_catch_up_looks_forward_only(self, client, subscribed, run_job, digests):
        # La facture est ponctuelle a dessein : le generateur refuse de creer
        # une echeance recurrente anterieure au jour du passage.
        monday = a_monday()
        client.post(
            "/v1/commitments",
            json={
                "title": "Hydro",
                "type": "invoice",
                "category": "utilities",
                "amount": "42.00",
                "frequency": "oneoff",
                "starts_on": monday.isoformat(),
            },
            headers=auth(subscribed),
        )
        add(
            client,
            subscribed,
            title="Spotify",
            starts_on=(monday + timedelta(days=4)).isoformat(),
        )

        run_job(today=monday + timedelta(days=2))

        titres = [item["title"] for item in digests()[0]["items"]]
        assert titres == ["Spotify"]
