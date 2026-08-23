from datetime import date
from decimal import Decimal
from typing import get_args

import pytest
from pydantic import ValidationError

from models.db import commitments_db
from models.schemas import commitment_schema
from models.schemas.commitment_schema import CommitmentCreate, CommitmentUpdate

pytestmark = pytest.mark.unit


def payload(**overrides):
    return {
        "title": "Netflix",
        "type": "subscription",
        "amount": Decimal("18.99"),
        "starts_on": date(2026, 8, 5),
        **overrides,
    }


class TestVocabularyStaysAlignedWithTheDatabase:
    @pytest.mark.parametrize(
        "literal_name, db_name",
        [
            ("CommitmentType", "COMMITMENT_TYPES"),
            ("CommitmentFrequency", "COMMITMENT_FREQUENCIES"),
            ("CommitmentStatus", "COMMITMENT_STATUSES"),
            ("OccurrenceStatus", "OCCURRENCE_STATUSES"),
        ],
    )
    def test_the_two_lists_match(self, literal_name, db_name):
        allowed = get_args(getattr(commitment_schema, literal_name))
        assert allowed == getattr(commitments_db, db_name)


class TestCommitmentCreate:
    def test_applies_the_shared_defaults(self):
        model = CommitmentCreate(**payload())
        assert model.category == commitments_db.DEFAULT_CATEGORY
        assert model.frequency == "monthly"
        assert model.is_reminder_enabled is True
        assert model.ends_on is None

    def test_leaves_the_reminder_delay_unset_for_the_account_default(self):
        model = CommitmentCreate(**payload())
        assert model.reminder_days_before is None

    def test_keeps_an_explicit_reminder_delay(self):
        model = CommitmentCreate(**payload(reminder_days_before=0))
        assert model.reminder_days_before == 0

    def test_trims_surrounding_whitespace(self):
        model = CommitmentCreate(**payload(title="  Spotify  ", category="  music  "))
        assert model.title == "Spotify"
        assert model.category == "music"

    @pytest.mark.parametrize("title", ["", "   ", "\t\n"])
    def test_refuses_a_blank_title(self, title):
        with pytest.raises(ValidationError):
            CommitmentCreate(**payload(title=title))

    @pytest.mark.parametrize("amount", ["0", "-1.00", "12.345", "100000000.00"])
    def test_refuses_an_impossible_amount(self, amount):
        with pytest.raises(ValidationError):
            CommitmentCreate(**payload(amount=Decimal(amount)))

    def test_refuses_a_term_ending_before_it_starts(self):
        with pytest.raises(ValidationError):
            CommitmentCreate(**payload(ends_on=date(2026, 1, 1)))

    def test_accepts_a_term_ending_the_same_day(self):
        model = CommitmentCreate(**payload(ends_on=date(2026, 8, 5)))
        assert model.ends_on == date(2026, 8, 5)

    @pytest.mark.parametrize("days", [-1, 31, 400])
    def test_refuses_a_notice_window_outside_the_cap(self, days):
        with pytest.raises(ValidationError):
            CommitmentCreate(**payload(reminder_days_before=days))


class TestCommitmentUpdate:
    def test_everything_is_optional(self):
        assert CommitmentUpdate().model_dump(exclude_unset=True) == {}

    def test_only_the_supplied_fields_are_reported(self):
        model = CommitmentUpdate(amount=Decimal("24.99"))
        assert model.model_dump(exclude_unset=True) == {"amount": Decimal("24.99")}

    def test_clearing_the_term_is_expressible(self):
        model = CommitmentUpdate(ends_on=None)
        assert "ends_on" in model.model_dump(exclude_unset=True)

    def test_refuses_a_reversed_term_when_both_are_supplied(self):
        with pytest.raises(ValidationError):
            CommitmentUpdate(starts_on=date(2026, 8, 5), ends_on=date(2026, 1, 1))

    def test_allows_a_lone_end_date(self):
        assert CommitmentUpdate(ends_on=date(2026, 1, 1)).ends_on == date(2026, 1, 1)
