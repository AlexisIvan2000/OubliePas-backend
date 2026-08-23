from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from services.commitments.action_window import (
    CANCELLATION,
    TRIAL,
    action_window,
)

TODAY = date(2026, 8, 22)


def commitment(**overrides):
    fields = {
        "starts_on": TODAY + timedelta(days=10),
        "trial_ends_on": None,
        "cancellation_notice_days": None,
        "reminder_days_before": 3,
    }
    return SimpleNamespace(**{**fields, **overrides})


class TestTrial:
    def test_lands_on_the_end_of_the_trial(self):
        item = commitment(trial_ends_on=TODAY + timedelta(days=10))
        window = action_window(item, item.starts_on)
        assert window.deadline == TODAY + timedelta(days=10)
        assert window.reason == TRIAL

    def test_only_covers_the_first_charge(self):
        item = commitment(trial_ends_on=TODAY + timedelta(days=10))
        assert action_window(item, item.starts_on + timedelta(days=30)) is None

    def test_forces_a_minimum_notice(self):
        item = commitment(trial_ends_on=TODAY + timedelta(days=10), reminder_days_before=0)
        assert action_window(item, item.starts_on).lead_days == 3

    def test_honours_a_longer_notice(self):
        item = commitment(trial_ends_on=TODAY + timedelta(days=10), reminder_days_before=7)
        assert action_window(item, item.starts_on).lead_days == 7


class TestCancellation:
    def test_sits_ahead_of_the_renewal(self):
        item = commitment(cancellation_notice_days=30)
        due = TODAY + timedelta(days=40)
        window = action_window(item, due)
        assert window.deadline == due - timedelta(days=30)
        assert window.reason == CANCELLATION

    def test_covers_every_renewal(self):
        item = commitment(cancellation_notice_days=30)
        far = item.starts_on + timedelta(days=365)
        assert action_window(item, far).deadline == far - timedelta(days=30)

    def test_keeps_the_configured_notice(self):
        item = commitment(cancellation_notice_days=30, reminder_days_before=0)
        assert action_window(item, TODAY + timedelta(days=40)).lead_days == 0


class TestBoth:
    def test_keeps_the_tighter_deadline(self):
        item = commitment(
            starts_on=TODAY + timedelta(days=40),
            trial_ends_on=TODAY + timedelta(days=38),
            cancellation_notice_days=30,
        )
        window = action_window(item, item.starts_on)
        assert window.deadline == TODAY + timedelta(days=10)
        assert window.reason == CANCELLATION

    def test_the_trial_wins_when_it_comes_first(self):
        item = commitment(
            starts_on=TODAY + timedelta(days=40),
            trial_ends_on=TODAY + timedelta(days=5),
            cancellation_notice_days=30,
        )
        assert action_window(item, item.starts_on).reason == TRIAL


class TestNothingToDo:
    def test_returns_nothing_without_either_field(self):
        assert action_window(commitment(), TODAY + timedelta(days=10)) is None


class TestOpening:
    @pytest.mark.parametrize("offset,expected", [(6, False), (7, True), (10, True), (11, False)])
    def test_the_window_runs_from_the_notice_to_the_deadline(self, offset, expected):
        item = commitment(trial_ends_on=TODAY + timedelta(days=10), reminder_days_before=3)
        window = action_window(item, item.starts_on)
        assert window.is_open(TODAY + timedelta(days=offset)) is expected

    def test_counts_the_days_left(self):
        item = commitment(trial_ends_on=TODAY + timedelta(days=10))
        assert action_window(item, item.starts_on).days_left(TODAY) == 10


class TestHorizon:
    def test_the_notice_can_never_outrun_the_generator(self):
        from models.db.commitments_db import MAX_CANCELLATION_NOTICE_DAYS, MAX_REMINDER_DAYS
        from services.commitments.occurrence_generator import GENERATION_HORIZON_DAYS

        assert MAX_CANCELLATION_NOTICE_DAYS + MAX_REMINDER_DAYS <= GENERATION_HORIZON_DAYS
