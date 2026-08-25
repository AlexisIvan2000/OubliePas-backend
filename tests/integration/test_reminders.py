from datetime import timedelta

import pytest
from sqlalchemy import text

from jobs.daily import LOCK_KEY, exit_code, main, run_daily
from services.emailing.email_sender import EmailSender
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


@pytest.fixture
def actions(mailbox):
    return lambda: [message for message in mailbox if message["kind"] == "action"]


@pytest.fixture
def other_token(client, mailbox):
    payload = {"first_name": "Sophie", "email": "sophie@example.com", "password": "MotDePasse1!"}
    assert client.post("/v1/auth/register", json=payload).status_code == 201
    code = mailbox[-1]["code"]
    response = client.post(
        "/v1/auth/verify-email", json={"email": payload["email"], "code": code}
    )
    assert response.status_code == 200
    return response.json()["access_token"]


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

    def test_writes_in_the_account_language(self, client, token, run_job, reminders):
        due = today_utc() + timedelta(days=2)
        client.post(
            "/v1/commitments",
            json=netflix(starts_on=due.isoformat(), reminder_days_before=3),
            headers=auth(token),
        )
        client.patch("/v1/users/me", json={"locale": "en"}, headers=auth(token))

        run_job()

        assert reminders()[0]["locale"] == "en"

    def test_falls_back_to_french(self, client, token, run_job, reminders):
        due = today_utc() + timedelta(days=2)
        client.post(
            "/v1/commitments",
            json=netflix(starts_on=due.isoformat(), reminder_days_before=3),
            headers=auth(token),
        )

        run_job()

        assert reminders()[0]["locale"] == "fr"

    def test_respects_the_account_wide_switch(self, client, token, run_job, reminders, db):
        due = today_utc() + timedelta(days=2)
        client.post(
            "/v1/commitments",
            json=netflix(starts_on=due.isoformat(), reminder_days_before=3),
            headers=auth(token),
        )
        client.patch(
            "/v1/users/me", json={"reminder_email_enabled": False}, headers=auth(token)
        )

        report = run_job()

        assert reminders() == []
        assert report["skipped"] == 1
        assert kinds(db) == []

    def test_switching_it_back_on_restores_the_reminder(
        self, client, token, run_job, reminders
    ):
        due = today_utc() + timedelta(days=2)
        client.post(
            "/v1/commitments",
            json=netflix(starts_on=due.isoformat(), reminder_days_before=3),
            headers=auth(token),
        )
        client.patch(
            "/v1/users/me", json={"reminder_email_enabled": False}, headers=auth(token)
        )
        run_job()
        assert reminders() == []

        client.patch(
            "/v1/users/me", json={"reminder_email_enabled": True}, headers=auth(token)
        )
        run_job()

        assert len(reminders()) == 1

    def test_the_switch_also_silences_the_relance(self, client, token, run_job, relances, db):
        client.post("/v1/commitments", json=netflix(), headers=auth(token))
        make_late(db, 4)
        client.patch(
            "/v1/users/me", json={"reminder_email_enabled": False}, headers=auth(token)
        )

        run_job()

        assert relances() == []

    def test_the_switch_also_silences_the_action_warning(
        self, client, token, run_job, actions
    ):
        charge = today_utc() + timedelta(days=3)
        client.post(
            "/v1/commitments",
            json=netflix(starts_on=charge.isoformat(), trial_ends_on=charge.isoformat()),
            headers=auth(token),
        )
        client.patch(
            "/v1/users/me", json={"reminder_email_enabled": False}, headers=auth(token)
        )

        run_job()

        assert actions() == []

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

    def test_a_deeply_backdated_bill_is_deliberately_left_alone(
        self, client, token, run_job, relances
    ):
        client.post(
            "/v1/commitments",
            json=netflix(
                title="Koodo",
                frequency="oneoff",
                starts_on=(today_utc() - timedelta(days=45)).isoformat(),
            ),
            headers=auth(token),
        )

        run_job()

        assert relances() == []

    def test_a_recently_backdated_bill_is_deliberately_relaunched(
        self, client, token, run_job, relances
    ):
        client.post(
            "/v1/commitments",
            json=netflix(
                title="Koodo",
                frequency="oneoff",
                starts_on=(today_utc() - timedelta(days=5)).isoformat(),
            ),
            headers=auth(token),
        )

        run_job()

        sent = relances()
        assert len(sent) == 1
        assert sent[0]["items"][0]["days_left"] == -5

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
class TestAction:
    def test_warns_before_a_trial_turns_into_a_charge(
        self, client, token, run_job, actions, credentials
    ):
        charge = today_utc() + timedelta(days=3)
        client.post(
            "/v1/commitments",
            json=netflix(
                starts_on=charge.isoformat(),
                trial_ends_on=charge.isoformat(),
                reminder_days_before=3,
            ),
            headers=auth(token),
        )

        report = run_job()

        assert report["actions"] == 1
        sent = actions()
        assert len(sent) == 1
        assert sent[0]["to"] == credentials["email"]
        assert sent[0]["items"][0]["reason"] == "trial"
        assert sent[0]["items"][0]["deadline"] == charge
        assert sent[0]["items"][0]["days_left"] == 3

    def test_the_trial_warning_replaces_the_plain_notice(
        self, client, token, run_job, actions, reminders, db
    ):
        charge = today_utc() + timedelta(days=3)
        client.post(
            "/v1/commitments",
            json=netflix(
                starts_on=charge.isoformat(),
                trial_ends_on=charge.isoformat(),
                reminder_days_before=3,
            ),
            headers=auth(token),
        )

        run_job()

        assert len(actions()) == 1
        assert reminders() == []
        assert kinds(db) == ["action_required", "notice"]

    def test_a_trial_below_the_minimum_notice_still_warns_in_time(
        self, client, token, run_job, actions
    ):
        charge = today_utc() + timedelta(days=3)
        client.post(
            "/v1/commitments",
            json=netflix(
                starts_on=charge.isoformat(),
                trial_ends_on=charge.isoformat(),
                reminder_days_before=0,
            ),
            headers=auth(token),
        )

        run_job()

        assert len(actions()) == 1

    def test_warns_before_a_cancellation_window_closes(self, client, token, run_job, actions):
        renewal = today_utc() + timedelta(days=32)
        client.post(
            "/v1/commitments",
            json=netflix(
                title="Assurance auto",
                starts_on=renewal.isoformat(),
                cancellation_notice_days=30,
                reminder_days_before=3,
            ),
            headers=auth(token),
        )

        run_job()

        sent = actions()
        assert len(sent) == 1
        assert sent[0]["items"][0]["reason"] == "cancellation"
        assert sent[0]["items"][0]["deadline"] == renewal - timedelta(days=30)
        assert sent[0]["items"][0]["due_date"] == renewal

    def test_a_cancellation_warning_leaves_the_payment_notice_alone(
        self, client, token, run_job, actions, reminders
    ):
        today = today_utc()
        renewal = today + timedelta(days=32)
        client.post(
            "/v1/commitments",
            json=netflix(
                title="Assurance auto",
                starts_on=renewal.isoformat(),
                frequency="yearly",
                cancellation_notice_days=30,
                reminder_days_before=3,
            ),
            headers=auth(token),
        )

        run_job()
        assert len(actions()) == 1
        assert reminders() == []

        run_job(today=today + timedelta(days=29))
        assert len(reminders()) == 1

    def test_stays_quiet_before_the_window_opens(self, client, token, run_job, actions):
        renewal = today_utc() + timedelta(days=50)
        client.post(
            "/v1/commitments",
            json=netflix(
                starts_on=renewal.isoformat(),
                cancellation_notice_days=30,
                reminder_days_before=3,
            ),
            headers=auth(token),
        )

        run_job()

        assert actions() == []

    def test_never_warns_twice(self, client, token, run_job, actions, db):
        charge = today_utc() + timedelta(days=3)
        client.post(
            "/v1/commitments",
            json=netflix(starts_on=charge.isoformat(), trial_ends_on=charge.isoformat()),
            headers=auth(token),
        )

        run_job()
        run_job()

        assert len(actions()) == 1
        assert kinds(db) == ["action_required", "notice"]

    def test_moving_the_trial_reopens_the_warning(self, client, token, run_job, actions):
        today = today_utc()
        charge = today + timedelta(days=3)
        created = client.post(
            "/v1/commitments",
            json=netflix(starts_on=charge.isoformat(), trial_ends_on=charge.isoformat()),
            headers=auth(token),
        ).json()

        run_job()
        assert len(actions()) == 1

        client.patch(
            f"/v1/commitments/{created['id']}",
            json={"trial_ends_on": (today + timedelta(days=1)).isoformat()},
            headers=auth(token),
        )
        run_job()

        assert len(actions()) == 2
        assert actions()[1]["items"][0]["deadline"] == today + timedelta(days=1)

    def test_ignores_a_commitment_with_reminders_disabled(self, client, token, run_job, actions):
        charge = today_utc() + timedelta(days=3)
        client.post(
            "/v1/commitments",
            json=netflix(
                starts_on=charge.isoformat(),
                trial_ends_on=charge.isoformat(),
                is_reminder_enabled=False,
            ),
            headers=auth(token),
        )

        run_job()

        assert actions() == []

    def test_ignores_a_settled_occurrence(self, client, token, run_job, actions, db):
        charge = today_utc() + timedelta(days=3)
        client.post(
            "/v1/commitments",
            json=netflix(starts_on=charge.isoformat(), trial_ends_on=charge.isoformat()),
            headers=auth(token),
        )
        db("update commitment_occurrences set status = 'paid'")

        run_job()

        assert actions() == []

    def test_a_trial_survives_a_cancellation_notice(self, client, token, run_job, actions):
        charge = today_utc() + timedelta(days=3)
        client.post(
            "/v1/commitments",
            json=netflix(
                title="Gym",
                starts_on=charge.isoformat(),
                trial_ends_on=charge.isoformat(),
                cancellation_notice_days=30,
                reminder_days_before=3,
            ),
            headers=auth(token),
        )

        run_job()

        sent = actions()
        assert len(sent) == 1
        assert sent[0]["items"][0]["reason"] == "trial"
        assert sent[0]["items"][0]["deadline"] == charge

    def test_a_plain_commitment_never_triggers_one(self, client, token, run_job, actions):
        client.post(
            "/v1/commitments",
            json=netflix(starts_on=(today_utc() + timedelta(days=2)).isoformat()),
            headers=auth(token),
        )

        run_job()

        assert actions() == []
class TestFailure:
    def test_a_failed_send_is_counted_and_left_for_the_next_run(
        self, client, token, run_job, reminders, db
    ):
        due = today_utc() + timedelta(days=2)
        client.post(
            "/v1/commitments",
            json=netflix(starts_on=due.isoformat(), reminder_days_before=3),
            headers=auth(token),
        )

        working = EmailSender.send_reminder_email

        async def boom(self, to, *, first_name, items, currency, locale="fr"):
            raise RuntimeError("resend is down")

        EmailSender.send_reminder_email = boom
        try:
            failed = run_job()
        finally:
            EmailSender.send_reminder_email = working

        assert failed["failed"] == 1
        assert failed["occurrences"] == 0
        assert exit_code(failed) == 1
        assert reminders() == []
        assert kinds(db) == []

        recovered = run_job()

        assert recovered["failed"] == 0
        assert recovered["occurrences"] == 1
        assert exit_code(recovered) == 0
        assert len(reminders()) == 1
        assert kinds(db) == ["notice"]

    def test_one_broken_account_never_blocks_the_others(
        self, client, token, other_token, run_job, reminders
    ):
        due = today_utc() + timedelta(days=2)
        for owner in (token, other_token):
            client.post(
                "/v1/commitments",
                json=netflix(starts_on=due.isoformat(), reminder_days_before=3),
                headers=auth(owner),
            )

        working = EmailSender.send_reminder_email
        seen = []

        async def flaky(self, to, *, first_name, items, currency, locale="fr"):
            seen.append(to)
            if len(seen) == 1:
                raise RuntimeError("resend is down")
            return await working(
                self,
                to,
                first_name=first_name,
                items=items,
                currency=currency,
                locale=locale,
            )

        EmailSender.send_reminder_email = flaky
        try:
            report = run_job()
        finally:
            EmailSender.send_reminder_email = working

        assert report["failed"] == 1
        assert report["occurrences"] == 1
        assert report["users"] == 1
        assert len(reminders()) == 1
class TestFamilySwitches:
    def test_silencing_the_relance_keeps_the_notice(
        self, client, token, run_job, reminders, relances
    ):
        due = today_utc() + timedelta(days=2)
        client.post(
            "/v1/commitments",
            json=netflix(starts_on=due.isoformat(), reminder_days_before=3),
            headers=auth(token),
        )
        client.patch(
            "/v1/users/me", json={"reminder_overdue_enabled": False}, headers=auth(token)
        )

        run_job()

        assert len(reminders()) == 1
        assert relances() == []

    def test_silencing_the_relance_leaves_a_late_occurrence_untouched(
        self, client, token, run_job, relances, db
    ):
        client.post("/v1/commitments", json=netflix(), headers=auth(token))
        make_late(db, 4)
        client.patch(
            "/v1/users/me", json={"reminder_overdue_enabled": False}, headers=auth(token)
        )

        report = run_job()

        assert relances() == []
        assert report["skipped"] == 1
        assert "overdue" not in kinds(db)

    def test_turning_the_relance_back_on_sends_it(
        self, client, token, run_job, relances, db
    ):
        client.post("/v1/commitments", json=netflix(), headers=auth(token))
        make_late(db, 4)
        client.patch(
            "/v1/users/me", json={"reminder_overdue_enabled": False}, headers=auth(token)
        )
        run_job()
        assert relances() == []

        client.patch(
            "/v1/users/me", json={"reminder_overdue_enabled": True}, headers=auth(token)
        )
        run_job()

        assert len(relances()) == 1

    def test_silencing_the_notice_keeps_the_relance(
        self, client, token, run_job, reminders, relances, db
    ):
        client.post("/v1/commitments", json=netflix(), headers=auth(token))
        make_late(db, 4)
        client.patch(
            "/v1/users/me", json={"reminder_notice_enabled": False}, headers=auth(token)
        )

        run_job()

        assert reminders() == []
        assert len(relances()) == 1

    def test_silencing_the_action_family_keeps_the_others(
        self, client, token, run_job, actions, reminders
    ):
        charge = today_utc() + timedelta(days=3)
        client.post(
            "/v1/commitments",
            json=netflix(starts_on=charge.isoformat(), trial_ends_on=charge.isoformat()),
            headers=auth(token),
        )
        client.patch(
            "/v1/users/me", json={"reminder_action_enabled": False}, headers=auth(token)
        )

        run_job()

        assert actions() == []
        assert len(reminders()) == 1

    def test_the_switches_default_to_on(self, client, token):
        body = client.get("/v1/users/me", headers=auth(token)).json()

        assert body["reminder_notice_enabled"] is True
        assert body["reminder_overdue_enabled"] is True
        assert body["reminder_action_enabled"] is True

    def test_the_switches_survive_a_round_trip(self, client, token):
        client.patch(
            "/v1/users/me", json={"reminder_overdue_enabled": False}, headers=auth(token)
        )

        body = client.get("/v1/users/me", headers=auth(token)).json()

        assert body["reminder_overdue_enabled"] is False
        assert body["reminder_notice_enabled"] is True
        assert body["reminder_email_enabled"] is True


class TestLateFeed:
    def test_lists_a_past_occurrence_left_pending(self, client, token, db):
        client.post("/v1/commitments", json=netflix(), headers=auth(token))
        make_late(db, 5)

        rows = client.get("/v1/commitments/occurrences/late", headers=auth(token)).json()

        assert len(rows) == 1
        assert rows[0]["title"] == "Netflix"
        assert rows[0]["is_late"] is True

    def test_ignores_an_occurrence_that_is_still_to_come(self, client, token):
        due = today_utc() + timedelta(days=2)
        client.post(
            "/v1/commitments", json=netflix(starts_on=due.isoformat()), headers=auth(token)
        )

        rows = client.get("/v1/commitments/occurrences/late", headers=auth(token)).json()

        assert rows == []

    def test_drops_an_occurrence_once_it_is_settled(self, client, token, db):
        client.post("/v1/commitments", json=netflix(), headers=auth(token))
        make_late(db, 5)
        late = client.get("/v1/commitments/occurrences/late", headers=auth(token)).json()
        client.patch(
            f"/v1/commitments/occurrences/{late[0]['id']}",
            json={"status": "paid"},
            headers=auth(token),
        )

        rows = client.get("/v1/commitments/occurrences/late", headers=auth(token)).json()

        assert rows == []

    def test_never_leaks_another_account(self, client, token, other_token, db):
        client.post("/v1/commitments", json=netflix(), headers=auth(token))
        make_late(db, 5)

        rows = client.get(
            "/v1/commitments/occurrences/late", headers=auth(other_token)
        ).json()

        assert rows == []

    def test_matches_the_late_count_of_the_summary(self, client, token, db):
        client.post("/v1/commitments", json=netflix(), headers=auth(token))
        client.post(
            "/v1/commitments", json=netflix(title="Spotify"), headers=auth(token)
        )
        make_late(db, 6)

        rows = client.get("/v1/commitments/occurrences/late", headers=auth(token)).json()
        summary = client.get("/v1/commitments/summary", headers=auth(token)).json()

        assert len(rows) == summary["late_count"] == 2

    def test_needs_a_token(self, client):
        assert client.get("/v1/commitments/occurrences/late").status_code == 401

