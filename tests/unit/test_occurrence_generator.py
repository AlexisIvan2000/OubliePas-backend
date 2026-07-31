from datetime import date, timedelta

import pytest

from services.commitments.occurrence_generator import (
    MAX_OCCURRENCES_PER_COMMITMENT,
    add_months,
    nth_due_date,
    occurrence_dates,
)

pytestmark = pytest.mark.unit


def dates(**kwargs):
    return occurrence_dates(**kwargs)


class TestAddMonths:
    def test_clamps_to_the_shorter_month(self):
        assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)

    def test_clamping_never_drifts_on_the_following_month(self):
        assert add_months(date(2026, 1, 31), 2) == date(2026, 3, 31)
        assert add_months(date(2026, 1, 31), 3) == date(2026, 4, 30)
        assert add_months(date(2026, 1, 31), 4) == date(2026, 5, 31)

    def test_crosses_the_year_boundary(self):
        assert add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)

    def test_february_29_falls_back_on_a_common_year(self):
        assert add_months(date(2024, 2, 29), 12) == date(2025, 2, 28)

    def test_february_29_survives_the_next_leap_year(self):
        assert add_months(date(2024, 2, 29), 48) == date(2028, 2, 29)


class TestNthDueDate:
    def test_weekly_advances_by_seven_days(self):
        assert nth_due_date(date(2026, 3, 2), "weekly", 3) == date(2026, 3, 23)

    def test_quarterly_advances_by_three_months(self):
        assert nth_due_date(date(2026, 1, 15), "quarterly", 2) == date(2026, 7, 15)

    def test_yearly_advances_by_twelve_months(self):
        assert nth_due_date(date(2026, 6, 1), "yearly", 2) == date(2028, 6, 1)


class TestMonthlySeries:
    def test_keeps_the_anchor_day_across_a_short_month(self):
        result = dates(
            starts_on=date(2026, 1, 31),
            frequency="monthly",
            ends_on=None,
            floor=date(2026, 1, 1),
            horizon=date(2026, 6, 30),
        )
        assert result == [
            date(2026, 1, 31),
            date(2026, 2, 28),
            date(2026, 3, 31),
            date(2026, 4, 30),
            date(2026, 5, 31),
            date(2026, 6, 30),
        ]

    def test_stops_at_the_horizon(self):
        result = dates(
            starts_on=date(2026, 1, 1),
            frequency="monthly",
            ends_on=None,
            floor=date(2026, 1, 1),
            horizon=date(2026, 3, 15),
        )
        assert result == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]

    def test_stops_at_the_end_of_the_term(self):
        result = dates(
            starts_on=date(2026, 1, 1),
            frequency="monthly",
            ends_on=date(2026, 3, 15),
            floor=date(2026, 1, 1),
            horizon=date(2026, 12, 31),
        )
        assert result == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]


class TestWeeklySeries:
    def test_generates_every_seven_days(self):
        result = dates(
            starts_on=date(2026, 1, 1),
            frequency="weekly",
            ends_on=None,
            floor=date(2026, 1, 1),
            horizon=date(2026, 1, 29),
        )
        assert result == [
            date(2026, 1, 1),
            date(2026, 1, 8),
            date(2026, 1, 15),
            date(2026, 1, 22),
            date(2026, 1, 29),
        ]

    def test_is_capped_on_a_very_long_horizon(self):
        result = dates(
            starts_on=date(2026, 1, 1),
            frequency="weekly",
            ends_on=None,
            floor=date(2026, 1, 1),
            horizon=date(2029, 1, 1),
        )
        assert len(result) == MAX_OCCURRENCES_PER_COMMITMENT


class TestQuarterlyAndYearly:
    def test_quarterly_lands_four_times_a_year(self):
        result = dates(
            starts_on=date(2026, 1, 15),
            frequency="quarterly",
            ends_on=None,
            floor=date(2026, 1, 1),
            horizon=date(2026, 12, 31),
        )
        assert result == [
            date(2026, 1, 15),
            date(2026, 4, 15),
            date(2026, 7, 15),
            date(2026, 10, 15),
        ]

    def test_yearly_lands_once(self):
        result = dates(
            starts_on=date(2026, 3, 1),
            frequency="yearly",
            ends_on=None,
            floor=date(2026, 1, 1),
            horizon=date(2026, 12, 31),
        )
        assert result == [date(2026, 3, 1)]


class TestOneOff:
    def test_yields_a_single_date(self):
        result = dates(
            starts_on=date(2026, 8, 15),
            frequency="oneoff",
            ends_on=None,
            floor=date(2026, 7, 31),
            horizon=date(2026, 10, 29),
        )
        assert result == [date(2026, 8, 15)]

    def test_is_ignored_beyond_the_horizon(self):
        result = dates(
            starts_on=date(2027, 8, 15),
            frequency="oneoff",
            ends_on=None,
            floor=date(2026, 7, 31),
            horizon=date(2026, 10, 29),
        )
        assert result == []

    def test_is_ignored_when_already_past(self):
        result = dates(
            starts_on=date(2026, 1, 15),
            frequency="oneoff",
            ends_on=None,
            floor=date(2026, 7, 31),
            horizon=date(2026, 10, 29),
        )
        assert result == []


class TestPastCommitments:
    def test_never_backfills_history(self):
        floor = date(2026, 7, 31)
        result = dates(
            starts_on=date(2024, 1, 10),
            frequency="monthly",
            ends_on=None,
            floor=floor,
            horizon=date(2026, 10, 31),
        )
        assert result == [date(2026, 8, 10), date(2026, 9, 10), date(2026, 10, 10)]
        assert all(due >= floor for due in result)

    def test_monthly_does_not_skip_the_first_due_date_after_the_floor(self):
        floor = date(2026, 7, 31)
        result = dates(
            starts_on=date(2019, 3, 5),
            frequency="monthly",
            ends_on=None,
            floor=floor,
            horizon=date(2026, 10, 31),
        )
        assert result[0] == date(2026, 8, 5)
        assert result[0] - floor < timedelta(days=31)

    def test_weekly_does_not_skip_the_first_due_date_after_the_floor(self):
        floor = date(2026, 7, 31)
        result = dates(
            starts_on=date(2020, 1, 1),
            frequency="weekly",
            ends_on=None,
            floor=floor,
            horizon=date(2026, 8, 31),
        )
        assert result[0] >= floor
        assert result[0] - floor < timedelta(days=7)
        gaps = {b - a for a, b in zip(result, result[1:])}
        assert gaps == {timedelta(days=7)}


class TestDegenerateWindows:
    def test_returns_nothing_when_the_horizon_precedes_the_floor(self):
        result = dates(
            starts_on=date(2026, 1, 1),
            frequency="monthly",
            ends_on=None,
            floor=date(2026, 8, 1),
            horizon=date(2026, 7, 1),
        )
        assert result == []

    def test_returns_nothing_when_the_term_ended_before_the_floor(self):
        result = dates(
            starts_on=date(2025, 1, 1),
            frequency="monthly",
            ends_on=date(2025, 12, 31),
            floor=date(2026, 7, 31),
            horizon=date(2026, 10, 29),
        )
        assert result == []
