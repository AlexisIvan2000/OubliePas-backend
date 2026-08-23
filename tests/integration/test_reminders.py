from datetime import timedelta

import pytest
from sqlalchemy import text

from jobs.daily import LOCK_KEY, main, run_daily
from services.commitments.occurrence_generator import today_utc

pytestmark = pytest.mark.integration


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def token(verified):
    return verified["tokens"]["access_token"]


def netflix(**overrides):
    return {
        "title": "Netflix",
        "type": "subscription",
        "category": "entertainment",
        "amount": "18.99",
        "frequency": "monthly",
        "starts_on": today_utc().isoformat(),
        **overrides,
    }


@pytest.fixture
def run_job(session_runner):
    def go(**kwargs):
        return session_runner(lambda session: run_daily(session, **kwargs))

    return go


@pytest.fixture
def reminders(mailbox):
    return lambda: [message for message in mailbox if message["kind"] == "reminder"]


@pytest.fixture
def relances(mailbox):
    return lambda: [message for message in mailbox if message["kind"] == "overdue"]


def kinds(db):
    rows = db("select kind from occurrence_reminders order by kind")
    return [row[0] for row in rows]


def make_late(db, days):
    db(f"update commitments set starts_on = starts_on - interval '{days} days'")
    db(f"update commitment_occurrences set due_date = due_date - interval '{days} days'")


class TestDelivery:
    def test_sends_an_email_for_an_upcoming_occurrence(
        self, client, token, run_job, reminders, credentials
    ):
        due = today_utc() + timedelta(days=2)
        client.post(
            "/v1/commitments",
            json=netflix(starts_on=due.isoformat(), reminder_days_before=3),
            headers=auth(token),
        )

        report = run_job()

        assert report["users"] == 1
        assert report["occurrences"] == 1
        sent = reminders()
        assert len(sent) == 1
        assert sent[0]["to"] == credentials["email"]
        assert sent[0]["items"][0]["title"] == "Netflix"

    def test_marks_the_occurrence_as_reminded(self, client, token, run_job, db):
        due = today_utc() + timedelta(days=2)
        client.post(
            "/v1/commitments",
            json=netflix(starts_on=due.isoformat(), reminder_days_before=3),
            headers=auth(token),
        )

        run_job()

        rows = db(
            "select r.kind from occurrence_reminders r"
            " join commitment_occurrences o on o.id = r.occurrence_id"
            " where o.due_date = :d",
            d=due,
        )
        assert [row[0] for row in rows] == ["notice"]

    def test_never_sends_twice(self, client, token, run_job, reminders):
        due = today_utc() + timedelta(days=2)
        client.post(
            "/v1/commitments",
            json=netflix(starts_on=due.isoformat(), reminder_days_before=3),
            headers=auth(token),
        )

        run_job()
        second = run_job()

        assert second["occurrences"] == 0
        assert len(reminders()) == 1

    def test_groups_every_commitment_into_a_single_email(
        self, client, token, run_job, reminders
    ):
        due = today_utc() + timedelta(days=1)
        for title in ("Netflix", "Spotify", "Hydro"):
            client.post(
                "/v1/commitments",
                json=netflix(title=title, starts_on=due.isoformat(), reminder_days_before=3),
                headers=auth(token),
            )

        report = run_job()

        assert report["users"] == 1
        assert report["occurrences"] == 3
        sent = reminders()
        assert len(sent) == 1
        assert {item["title"] for item in sent[0]["items"]} == {"Netflix", "Spotify", "Hydro"}

    def test_carries_the_amount_and_the_currency(self, client, token, run_job, reminders):
        due = today_utc() + timedelta(days=1)
        client.post(
            "/v1/commitments",
            json=netflix(amount="42.50", starts_on=due.isoformat(), reminder_days_before=3),
            headers=auth(token),
        )

        run_job()

        item = reminders()[0]["items"][0]
        assert str(item["amount"]) == "42.50"
        assert reminders()[0]["currency"] == "CAD"

    def test_reports_days_left(self, client, token, run_job, reminders):
        due = today_utc() + timedelta(days=2)
        client.post(
            "/v1/commitments",
            json=netflix(starts_on=due.isoformat(), reminder_days_before=3),
            headers=auth(token),
        )

        run_job()

        assert reminders()[0]["items"][0]["days_left"] == 2


class TestSelection:
    def test_ignores_an_occurrence_beyond_the_window(self, client, token, run_job, reminders):
        due = today_utc() + timedelta(days=10)
        client.post(
            "/v1/commitments",
            json=netflix(starts_on=due.isoformat(), reminder_days_before=3),
            headers=auth(token),
        )

        report = run_job()

        assert report["occurrences"] == 0
        assert reminders() == []

    def test_ignores_a_commitment_with_reminders_disabled(
        self, client, token, run_job, reminders
    ):
        due = today_utc() + timedelta(days=1)
        client.post(
            "/v1/commitments",
            json=netflix(starts_on=due.isoformat(), is_reminder_enabled=False),
            headers=auth(token),
        )

        run_job()

        assert reminders() == []

    def test_ignores_a_paid_occurrence(self, client, token, run_job, reminders, db):
        due = today_utc() + timedelta(days=1)
        client.post(
            "/v1/commitments",
            json=netflix(starts_on=due.isoformat(), reminder_days_before=3),
            headers=auth(token),
        )
        db("update commitment_occurrences set status = 'paid' where due_date = :d", d=due)

        run_job()

        assert reminders() == []

    def test_ignores_a_past_due_date(self, client, token, run_job, reminders, db):
        client.post("/v1/commitments", json=netflix(), headers=auth(token))
        db("update commitments set starts_on = starts_on - interval '5 days'")
        db("update commitment_occurrences set due_date = due_date - interval '5 days'")

        run_job()

        assert reminders() == []
        assert kinds(db) == ["overdue"]

    def test_skips_a_disabled_account(self, client, token, run_job, reminders, db, credentials):
        due = today_utc() + timedelta(days=1)
        client.post(
            "/v1/commitments",
            json=netflix(starts_on=due.isoformat(), reminder_days_before=3),
            headers=auth(token),
        )
        db("update users set is_active = false where email = :e", e=credentials["email"])

        report = run_job()

        assert report["skipped"] == 1
        assert reminders() == []

    def test_a_skipped_account_stays_eligible_later(
        self, client, token, run_job, reminders, db, credentials
    ):
        due = today_utc() + timedelta(days=1)
        client.post(
            "/v1/commitments",
            json=netflix(starts_on=due.isoformat(), reminder_days_before=3),
            headers=auth(token),
        )
        db("update users set is_active = false where email = :e", e=credentials["email"])
        run_job()

        db("update users set is_active = true where email = :e", e=credentials["email"])
        report = run_job()

        assert report["occurrences"] == 1
        assert len(reminders()) == 1


class TestConcurrency:
    def test_a_second_run_backs_off_while_one_holds_the_lock(
        self, client, token, reminders, session_runner
    ):
        due = today_utc() + timedelta(days=1)
        client.post(
            "/v1/commitments",
            json=netflix(starts_on=due.isoformat(), reminder_days_before=3),
            headers=auth(token),
        )

        async def hold_then_run(session):
            await session.execute(
                text("select pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}
            )
            try:
                return await main()
            finally:
                await session.execute(
                    text("select pg_advisory_unlock(:key)"), {"key": LOCK_KEY}
                )

        report = session_runner(hold_then_run)

        assert report == {"skipped": "another run is already in progress"}
        assert reminders() == []

    def test_the_lock_is_released_for_the_next_run(self, client, token, reminders, session_runner):
        due = today_utc() + timedelta(days=1)
        client.post(
            "/v1/commitments",
            json=netflix(starts_on=due.isoformat(), reminder_days_before=3),
            headers=auth(token),
        )

        first = session_runner(lambda _: main())
        second = session_runner(lambda _: main())

        assert first["occurrences"] == 1
        assert second["occurrences"] == 0
        assert len(reminders()) == 1


class TestGeneration:
    def test_extends_occurrences_past_the_horizon(self, client, token, run_job, db):
        client.post("/v1/commitments", json=netflix(), headers=auth(token))
        before = db("select count(*) from commitment_occurrences")[0][0]

        db("update commitment_occurrences set due_date = due_date - interval '60 days'")
        report = run_job()

        after = db("select count(*) from commitment_occurrences")[0][0]
        assert report["occurrences_generated"] > 0
        assert after > before

    def test_reports_the_reference_date(self, run_job):
        assert run_job()["date"] == today_utc().isoformat()

    def test_is_idempotent_on_generation(self, client, token, run_job):
        client.post("/v1/commitments", json=netflix(), headers=auth(token))
        run_job()

        assert run_job()["occurrences_generated"] == 0
class TestOverdue:
    def test_relaunches_an_occurrence_left_pending(
        self, client, token, run_job, relances, db, credentials
    ):
        client.post("/v1/commitments", json=netflix(), headers=auth(token))
        make_late(db, 4)

        report = run_job()

        assert report["overdue"] == 1
        sent = relances()
        assert len(sent) == 1
        assert sent[0]["to"] == credentials["email"]
        assert sent[0]["items"][0]["title"] == "Netflix"
        assert sent[0]["items"][0]["days_left"] == -4

    def test_stays_quiet_before_the_grace_period(self, client, token, run_job, relances, db):
        client.post("/v1/commitments", json=netflix(), headers=auth(token))
        make_late(db, 2)

        run_job()

        assert relances() == []

    def test_never_relaunches_twice(self, client, token, run_job, relances, db):
        client.post("/v1/commitments", json=netflix(), headers=auth(token))
        make_late(db, 4)

        run_job()
        run_job()

        assert len(relances()) == 1
        assert kinds(db) == ["overdue"]

    def test_ignores_an_occurrence_settled_late(self, client, token, run_job, relances, db):
        client.post("/v1/commitments", json=netflix(), headers=auth(token))
        make_late(db, 4)
        db("update commitment_occurrences set status = 'paid'")

        run_job()

        assert relances() == []

    def test_ignores_a_skipped_occurrence(self, client, token, run_job, relances, db):
        client.post("/v1/commitments", json=netflix(), headers=auth(token))
        make_late(db, 4)
        db("update commitment_occurrences set status = 'skipped'")

        run_job()

        assert relances() == []

    def test_ignores_an_ancient_occurrence(self, client, token, run_job, relances, db):
        client.post(
            "/v1/commitments", json=netflix(frequency="oneoff"), headers=auth(token)
        )
        make_late(db, 45)

        run_job()

        assert relances() == []

    def test_ignores_a_commitment_with_reminders_disabled(
        self, client, token, run_job, relances, db
    ):
        client.post(
            "/v1/commitments",
            json=netflix(is_reminder_enabled=False),
            headers=auth(token),
        )
        make_late(db, 4)

        run_job()

        assert relances() == []

    def test_a_notice_does_not_consume_the_relance(
        self, client, token, run_job, reminders, relances, db
    ):
        due = today_utc() + timedelta(days=1)
        client.post(
            "/v1/commitments",
            json=netflix(starts_on=due.isoformat(), reminder_days_before=3),
            headers=auth(token),
        )

        run_job()
        assert len(reminders()) == 1

        make_late(db, 5)
        run_job()

        assert len(relances()) == 1
        assert kinds(db) == ["notice", "overdue"]

    def test_groups_every_late_occurrence_into_a_single_email(
        self, client, token, run_job, relances, db
    ):
        client.post("/v1/commitments", json=netflix(), headers=auth(token))
        client.post(
            "/v1/commitments",
            json=netflix(title="Spotify", amount="11.99"),
            headers=auth(token),
        )
        make_late(db, 4)

        run_job()

        sent = relances()
        assert len(sent) == 1
        assert len(sent[0]["items"]) == 2
