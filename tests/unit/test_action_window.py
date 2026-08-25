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
        window = action_window(item, item.starts_on, reference=TODAY)
        assert window.deadline == TODAY + timedelta(days=10)
        assert window.reason == TRIAL

    def test_only_covers_the_first_charge(self):
        item = commitment(trial_ends_on=TODAY + timedelta(days=10))
        assert action_window(item, item.starts_on + timedelta(days=30), reference=TODAY) is None

    def test_forces_a_minimum_notice(self):
        item = commitment(trial_ends_on=TODAY + timedelta(days=10), reminder_days_before=0)
        assert action_window(item, item.starts_on, reference=TODAY).lead_days == 3

    def test_honours_a_longer_notice(self):
        item = commitment(trial_ends_on=TODAY + timedelta(days=10), reminder_days_before=7)
        assert action_window(item, item.starts_on, reference=TODAY).lead_days == 7


class TestCancellation:
    def test_sits_ahead_of_the_renewal(self):
        item = commitment(cancellation_notice_days=30)
        due = TODAY + timedelta(days=40)
        window = action_window(item, due, reference=TODAY)
        assert window.deadline == due - timedelta(days=30)
        assert window.reason == CANCELLATION

    def test_covers_every_renewal(self):
        item = commitment(cancellation_notice_days=30)
        far = item.starts_on + timedelta(days=365)
        assert action_window(item, far, reference=TODAY).deadline == far - timedelta(days=30)

    def test_keeps_the_configured_notice(self):
        item = commitment(cancellation_notice_days=30, reminder_days_before=0)
        assert action_window(item, TODAY + timedelta(days=40), reference=TODAY).lead_days == 0


class TestBoth:
    def test_keeps_the_tighter_deadline(self):
        item = commitment(
            starts_on=TODAY + timedelta(days=40),
            trial_ends_on=TODAY + timedelta(days=38),
            cancellation_notice_days=30,
        )
        window = action_window(item, item.starts_on, reference=TODAY)
        assert window.deadline == TODAY + timedelta(days=10)
        assert window.reason == CANCELLATION

    def test_the_trial_wins_when_it_comes_first(self):
        item = commitment(
            starts_on=TODAY + timedelta(days=40),
            trial_ends_on=TODAY + timedelta(days=5),
            cancellation_notice_days=30,
        )
        assert action_window(item, item.starts_on, reference=TODAY).reason == TRIAL


class TestExpired:
    def test_ignores_a_deadline_that_is_already_gone(self):
        item = commitment(starts_on=TODAY + timedelta(days=14), cancellation_notice_days=30)
        assert action_window(item, item.starts_on, reference=TODAY) is None

    def test_a_dead_notice_never_shadows_a_live_trial(self):
        item = commitment(
            starts_on=TODAY + timedelta(days=14),
            trial_ends_on=TODAY + timedelta(days=14),
            cancellation_notice_days=30,
        )
        window = action_window(item, item.starts_on, reference=TODAY)
        assert window.reason == TRIAL
        assert window.deadline == TODAY + timedelta(days=14)

    def test_the_notice_takes_over_on_the_next_renewal(self):
        item = commitment(
            starts_on=TODAY + timedelta(days=14),
            trial_ends_on=TODAY + timedelta(days=14),
            cancellation_notice_days=30,
        )
        renewal = item.starts_on + timedelta(days=365)
        window = action_window(item, renewal, reference=TODAY)
        assert window.reason == CANCELLATION
        assert window.deadline == renewal - timedelta(days=30)

    def test_still_keeps_the_tighter_of_two_live_deadlines(self):
        item = commitment(
            starts_on=TODAY + timedelta(days=40),
            trial_ends_on=TODAY + timedelta(days=38),
            cancellation_notice_days=30,
        )
        assert action_window(item, item.starts_on, reference=TODAY).reason == CANCELLATION

    def test_the_deadline_of_the_day_still_counts(self):
        item = commitment(starts_on=TODAY, trial_ends_on=TODAY)
        assert action_window(item, TODAY, reference=TODAY).deadline == TODAY


class TestNothingToDo:
    def test_returns_nothing_without_either_field(self):
        assert action_window(commitment(), TODAY + timedelta(days=10), reference=TODAY) is None


class TestOpening:
    @pytest.mark.parametrize("offset,expected", [(6, False), (7, True), (10, True), (11, False)])
    def test_the_window_runs_from_the_notice_to_the_deadline(self, offset, expected):
        item = commitment(trial_ends_on=TODAY + timedelta(days=10), reminder_days_before=3)
        window = action_window(item, item.starts_on, reference=TODAY)
        assert window.is_open(TODAY + timedelta(days=offset)) is expected

    def test_counts_the_days_left(self):
        item = commitment(trial_ends_on=TODAY + timedelta(days=10))
        assert action_window(item, item.starts_on, reference=TODAY).days_left(TODAY) == 10


class TestHorizon:
    def test_the_notice_can_never_outrun_the_generator(self):
        from models.db.commitments_db import MAX_CANCELLATION_NOTICE_DAYS, MAX_REMINDER_DAYS
        from services.commitments.occurrence_generator import GENERATION_HORIZON_DAYS

        assert MAX_CANCELLATION_NOTICE_DAYS + MAX_REMINDER_DAYS <= GENERATION_HORIZON_DAYS
class TestGenerationWindow:
    """La fenetre de generation doit toujours contenir la prochaine echeance."""

    @pytest.mark.parametrize(
        "frequency,step_days",
        [("weekly", 3), ("monthly", 7), ("quarterly", 11), ("yearly", 13)],
    )
    def test_at_least_one_occurrence_exists_any_day_of_the_cycle(self, frequency, step_days):
        from services.commitments.occurrence_generator import horizon_days, occurrence_dates

        start = date(2020, 1, 31)
        window = horizon_days(frequency)
        empty = []
        for offset in range(0, 800, step_days):
            today = date(2026, 1, 1) + timedelta(days=offset)
            dates = occurrence_dates(
                starts_on=start,
                frequency=frequency,
                ends_on=None,
                floor=today,
                horizon=today + timedelta(days=window),
            )
            if not dates:
                empty.append(today)

        assert empty == []

    def test_a_yearly_commitment_keeps_a_date_far_from_its_anniversary(self):
        from services.commitments.occurrence_generator import horizon_days, occurrence_dates

        today = date(2026, 8, 24)
        dates = occurrence_dates(
            starts_on=date(2026, 7, 23),
            frequency="yearly",
            ends_on=None,
            floor=today,
            horizon=today + timedelta(days=horizon_days("yearly")),
        )

        assert dates[0] == date(2027, 7, 23)

    def test_the_short_frequencies_keep_the_ninety_day_window(self):
        from services.commitments.occurrence_generator import (
            GENERATION_HORIZON_DAYS,
            horizon_days,
        )

        assert horizon_days("weekly") == GENERATION_HORIZON_DAYS
        assert horizon_days("monthly") == GENERATION_HORIZON_DAYS
        assert horizon_days("oneoff") == GENERATION_HORIZON_DAYS

    def test_an_ended_commitment_still_generates_nothing(self):
        from services.commitments.occurrence_generator import horizon_days, occurrence_dates

        today = date(2026, 8, 24)
        dates = occurrence_dates(
            starts_on=date(2020, 1, 1),
            frequency="yearly",
            ends_on=date(2025, 1, 1),
            floor=today,
            horizon=today + timedelta(days=horizon_days("yearly")),
        )

        assert dates == []

