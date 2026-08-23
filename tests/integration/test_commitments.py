from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError

from models.db.commitments_db import Commitment
from repositories.commitment_repository import CommitmentRepository
from services.commitments.occurrence_generator import OccurrenceGenerator

pytestmark = pytest.mark.integration

TODAY = date(2026, 7, 31)


@pytest.fixture
def user_id(db, verified):
    rows = db("SELECT id FROM users WHERE email = :email", email=verified["email"])
    return rows[0][0]


@pytest.fixture
def other_user_id(client, db, mailbox):
    payload = {"first_name": "Sophie", "email": "sophie@example.com", "password": "MotDePasse1!"}
    assert client.post("/v1/auth/register", json=payload).status_code == 201
    rows = db("SELECT id FROM users WHERE email = :email", email=payload["email"])
    return rows[0][0]


def netflix(user_id, **overrides):
    return {
        "user_id": user_id,
        "title": "Netflix",
        "type": "subscription",
        "category": "entertainment",
        "amount": Decimal("18.99"),
        "frequency": "monthly",
        "starts_on": date(2026, 8, 5),
        **overrides,
    }


def loyer(user_id, **overrides):
    return {
        "user_id": user_id,
        "title": "Loyer",
        "type": "invoice",
        "category": "housing",
        "amount": Decimal("1250.00"),
        "frequency": "monthly",
        "starts_on": date(2026, 8, 1),
        **overrides,
    }


class TestCommitmentCrud:
    def test_creates_with_the_expected_defaults(self, session_runner, user_id):
        async def work(session):
            repo = CommitmentRepository(session)
            created = await repo.create(netflix(user_id))
            return {
                "status": created.status,
                "category": created.category,
                "reminder_days_before": created.reminder_days_before,
                "is_reminder_enabled": created.is_reminder_enabled,
            }

        assert session_runner(work) == {
            "status": "active",
            "category": "entertainment",
            "reminder_days_before": 3,
            "is_reminder_enabled": True,
        }

    def test_is_scoped_to_its_owner(self, session_runner, user_id, other_user_id):
        async def work(session):
            repo = CommitmentRepository(session)
            created = await repo.create(netflix(user_id))
            mine = await repo.get_by_id(created.id, user_id)
            theirs = await repo.get_by_id(created.id, other_user_id)
            return mine is not None, theirs

        found, stolen = session_runner(work)
        assert found is True
        assert stolen is None

    def test_lists_only_the_requested_type(self, session_runner, user_id):
        async def work(session):
            repo = CommitmentRepository(session)
            await repo.create(netflix(user_id))
            await repo.create(loyer(user_id))
            subscriptions = await repo.list_for_user(user_id, commitment_type="subscription")
            invoices = await repo.list_for_user(user_id, commitment_type="invoice")
            return [c.title for c in subscriptions], [c.title for c in invoices]

        subscriptions, invoices = session_runner(work)
        assert subscriptions == ["Netflix"]
        assert invoices == ["Loyer"]

    def test_deleting_a_commitment_removes_its_occurrences(self, session_runner, user_id):
        async def work(session):
            repo = CommitmentRepository(session)
            commitment = await repo.create(netflix(user_id))
            await OccurrenceGenerator(repo).sync(commitment, today=TODAY)
            before = len(await repo.list_occurrences(user_id, start=TODAY, end=TODAY + timedelta(days=365)))
            await repo.delete(commitment.id, user_id)
            after = len(await repo.list_occurrences(user_id, start=TODAY, end=TODAY + timedelta(days=365)))
            return before, after

        before, after = session_runner(work)
        assert before > 0
        assert after == 0


class TestDatabaseConstraints:
    def _rejects(self, session_runner, user_id, **overrides):
        async def work(session):
            repo = CommitmentRepository(session)
            with pytest.raises((IntegrityError, DBAPIError)):
                await repo.create(netflix(user_id, **overrides))
            await session.rollback()
            return True

        return session_runner(work)

    def test_rejects_a_negative_amount(self, session_runner, user_id):
        assert self._rejects(session_runner, user_id, amount=Decimal("-1.00")) is True

    def test_rejects_an_unknown_type(self, session_runner, user_id):
        assert self._rejects(session_runner, user_id, type="mortgage") is True

    def test_rejects_an_unknown_frequency(self, session_runner, user_id):
        assert self._rejects(session_runner, user_id, frequency="daily") is True

    def test_rejects_a_term_ending_before_it_starts(self, session_runner, user_id):
        assert self._rejects(session_runner, user_id, ends_on=date(2026, 1, 1)) is True

    def test_rejects_a_notice_window_beyond_the_cap(self, session_runner, user_id):
        assert self._rejects(session_runner, user_id, reminder_days_before=45) is True


class TestOccurrenceGeneration:
    def test_creates_the_upcoming_occurrences(self, session_runner, user_id):
        async def work(session):
            repo = CommitmentRepository(session)
            commitment = await repo.create(netflix(user_id))
            created = await OccurrenceGenerator(repo).sync(commitment, today=TODAY)
            rows = await repo.list_occurrences(user_id, start=TODAY, end=TODAY + timedelta(days=365))
            return created, [row.due_date for row in rows], [row.amount for row in rows]

        created, due_dates, amounts = session_runner(work)
        assert created == 3
        assert due_dates == [date(2026, 8, 5), date(2026, 9, 5), date(2026, 10, 5)]
        assert amounts == [Decimal("18.99")] * 3

    def test_running_it_twice_creates_nothing_new(self, session_runner, user_id):
        async def work(session):
            repo = CommitmentRepository(session)
            generator = OccurrenceGenerator(repo)
            commitment = await repo.create(netflix(user_id))
            first = await generator.sync(commitment, today=TODAY)
            second = await generator.sync(commitment, today=TODAY)
            third = await generator.sync(commitment, today=TODAY)
            total = len(await repo.list_occurrences(user_id, start=TODAY, end=TODAY + timedelta(days=365)))
            return first, second, third, total

        first, second, third, total = session_runner(work)
        assert (first, second, third) == (3, 0, 0)
        assert total == 3

    def test_skips_a_paused_commitment(self, session_runner, user_id):
        async def work(session):
            repo = CommitmentRepository(session)
            commitment = await repo.create(netflix(user_id, status="paused"))
            return await OccurrenceGenerator(repo).sync(commitment, today=TODAY)

        assert session_runner(work) == 0

    def test_sync_all_active_covers_every_commitment(self, session_runner, user_id):
        async def work(session):
            repo = CommitmentRepository(session)
            await repo.create(netflix(user_id))
            await repo.create(loyer(user_id))
            await repo.create(netflix(user_id, title="Spotify", status="archived"))
            return await OccurrenceGenerator(repo).sync_all_active(today=TODAY)

        assert session_runner(work) == 6

    def test_resync_replaces_pending_but_keeps_paid_history(self, session_runner, user_id):
        async def work(session):
            repo = CommitmentRepository(session)
            generator = OccurrenceGenerator(repo)
            commitment = await repo.create(netflix(user_id))
            await generator.sync(commitment, today=TODAY)

            rows = await repo.list_occurrences(user_id, start=TODAY, end=TODAY + timedelta(days=365))
            await repo.set_occurrence_status(
                rows[0].id, user_id, status="paid", paid_at=datetime.now(timezone.utc)
            )

            commitment.amount = Decimal("24.99")
            await session.flush()
            await generator.resync(commitment, today=TODAY)

            refreshed = await repo.list_occurrences(
                user_id, start=TODAY, end=TODAY + timedelta(days=365)
            )
            return [(row.due_date, row.status, row.amount) for row in refreshed]

        rows = session_runner(work)
        assert rows[0] == (date(2026, 8, 5), "paid", Decimal("18.99"))
        assert rows[1] == (date(2026, 9, 5), "pending", Decimal("24.99"))
        assert rows[2] == (date(2026, 10, 5), "pending", Decimal("24.99"))


class TestReminders:
    def test_selects_only_what_falls_inside_the_notice_window(self, session_runner, user_id):
        async def work(session):
            repo = CommitmentRepository(session)
            commitment = await repo.create(
                netflix(user_id, starts_on=TODAY + timedelta(days=3), reminder_days_before=3)
            )
            far = await repo.create(
                loyer(user_id, starts_on=TODAY + timedelta(days=20), reminder_days_before=3)
            )
            generator = OccurrenceGenerator(repo)
            await generator.sync(commitment, today=TODAY)
            await generator.sync(far, today=TODAY)
            due = await repo.due_for_reminder(TODAY)
            return [(occurrence.due_date, item.title) for occurrence, item in due]

        due = session_runner(work)
        assert due == [(TODAY + timedelta(days=3), "Netflix")]

    def test_ignores_a_commitment_with_reminders_switched_off(self, session_runner, user_id):
        async def work(session):
            repo = CommitmentRepository(session)
            commitment = await repo.create(
                netflix(user_id, starts_on=TODAY + timedelta(days=2), is_reminder_enabled=False)
            )
            await OccurrenceGenerator(repo).sync(commitment, today=TODAY)
            return await repo.due_for_reminder(TODAY)

        assert session_runner(work) == []

    def test_never_selects_the_same_occurrence_twice(self, session_runner, user_id):
        async def work(session):
            repo = CommitmentRepository(session)
            commitment = await repo.create(netflix(user_id, starts_on=TODAY + timedelta(days=2)))
            await OccurrenceGenerator(repo).sync(commitment, today=TODAY)

            first = await repo.due_for_reminder(TODAY)
            await repo.mark_reminders_sent(
                [occurrence.id for occurrence, _ in first], kind="notice"
            )
            second = await repo.due_for_reminder(TODAY)
            return len(first), len(second)

        first, second = session_runner(work)
        assert first == 1
        assert second == 0

    def test_ignores_an_occurrence_already_paid(self, session_runner, user_id):
        async def work(session):
            repo = CommitmentRepository(session)
            commitment = await repo.create(netflix(user_id, starts_on=TODAY + timedelta(days=2)))
            await OccurrenceGenerator(repo).sync(commitment, today=TODAY)
            rows = await repo.list_occurrences(user_id, start=TODAY, end=TODAY + timedelta(days=10))
            await repo.set_occurrence_status(
                rows[0].id, user_id, status="paid", paid_at=datetime.now(timezone.utc)
            )
            return await repo.due_for_reminder(TODAY)

        assert session_runner(work) == []


    def test_selects_what_is_late_enough_to_relaunch(self, session_runner, user_id):
        async def work(session):
            repo = CommitmentRepository(session)
            late = TODAY - timedelta(days=5)
            commitment = await repo.create(
                netflix(user_id, starts_on=late, frequency="oneoff")
            )
            await OccurrenceGenerator(repo).sync(commitment, today=late)
            due = await repo.overdue_for_reminder(TODAY)
            return [occurrence.due_date for occurrence, _ in due]

        assert session_runner(work) == [TODAY - timedelta(days=5)]

    def test_leaves_a_fresh_miss_alone(self, session_runner, user_id):
        async def work(session):
            repo = CommitmentRepository(session)
            late = TODAY - timedelta(days=1)
            commitment = await repo.create(
                netflix(user_id, starts_on=late, frequency="oneoff")
            )
            await OccurrenceGenerator(repo).sync(commitment, today=late)
            return await repo.overdue_for_reminder(TODAY)

        assert session_runner(work) == []

    def test_a_notice_never_consumes_the_relance(self, session_runner, user_id):
        async def work(session):
            repo = CommitmentRepository(session)
            late = TODAY - timedelta(days=5)
            commitment = await repo.create(
                netflix(user_id, starts_on=late, frequency="oneoff")
            )
            await OccurrenceGenerator(repo).sync(commitment, today=late)
            rows = await repo.list_occurrences(user_id, start=late, end=TODAY)
            await repo.mark_reminders_sent([rows[0].id], kind="notice")

            first = await repo.overdue_for_reminder(TODAY)
            await repo.mark_reminders_sent(
                [occurrence.id for occurrence, _ in first], kind="overdue"
            )
            second = await repo.overdue_for_reminder(TODAY)
            return len(first), len(second)

        assert session_runner(work) == (1, 0)

    def test_marking_the_same_kind_twice_is_harmless(self, session_runner, user_id):
        async def work(session):
            repo = CommitmentRepository(session)
            commitment = await repo.create(netflix(user_id, starts_on=TODAY + timedelta(days=2)))
            await OccurrenceGenerator(repo).sync(commitment, today=TODAY)
            due = await repo.due_for_reminder(TODAY)
            ids = [occurrence.id for occurrence, _ in due]
            await repo.mark_reminders_sent(ids, kind="notice")
            return await repo.mark_reminders_sent(ids, kind="notice")

        assert session_runner(work) == 0


class TestTotals:
    def test_sums_the_window(self, session_runner, user_id):
        async def work(session):
            repo = CommitmentRepository(session)
            generator = OccurrenceGenerator(repo)
            await generator.sync(await repo.create(netflix(user_id)), today=TODAY)
            await generator.sync(await repo.create(loyer(user_id)), today=TODAY)
            return await repo.total_between(
                user_id, start=date(2026, 8, 1), end=date(2026, 8, 31)
            )

        assert session_runner(work) == Decimal("1268.99")

    def test_excludes_another_user(self, session_runner, user_id, other_user_id):
        async def work(session):
            repo = CommitmentRepository(session)
            generator = OccurrenceGenerator(repo)
            await generator.sync(await repo.create(netflix(user_id)), today=TODAY)
            await generator.sync(await repo.create(loyer(other_user_id)), today=TODAY)
            return await repo.total_between(
                other_user_id, start=date(2026, 8, 1), end=date(2026, 8, 31)
            )

        assert session_runner(work) == Decimal("1250.00")


class TestCascade:
    def test_deleting_the_user_removes_everything(self, session_runner, db, user_id):
        async def work(session):
            repo = CommitmentRepository(session)
            commitment = await repo.create(netflix(user_id))
            await OccurrenceGenerator(repo).sync(commitment, today=TODAY)
            return True

        session_runner(work)
        db("DELETE FROM users WHERE id = :id", id=user_id)
        remaining = db("SELECT count(*) FROM commitment_occurrences")
        assert remaining[0][0] == 0
        assert db("SELECT count(*) FROM commitments")[0][0] == 0


def test_commitment_model_exposes_its_occurrences(session_runner, user_id):
    async def work(session):
        repo = CommitmentRepository(session)
        commitment = await repo.create(netflix(user_id))
        await OccurrenceGenerator(repo).sync(commitment, today=TODAY)
        found = await session.get(Commitment, commitment.id)
        await session.refresh(found, ["occurrences"])
        return len(found.occurrences)

    assert session_runner(work) == 3
