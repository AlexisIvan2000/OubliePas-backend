from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from core.exceptions import FuturePaymentDate
from services.commitments.commitment_service import Settlement, settle

pytestmark = pytest.mark.unit

TODAY = date(2026, 8, 26)
STAMPED = datetime(2026, 8, 20, 9, 30, tzinfo=timezone.utc)


def occurrence(paid_at=None, paid_on=None):
    return SimpleNamespace(paid_at=paid_at, paid_on=paid_on)


class TestNotPaid:
    @pytest.mark.parametrize("status", ["pending", "skipped"])
    def test_nothing_is_recorded(self, status):
        assert settle(
            occurrence(), status=status, paid_on=TODAY, today=TODAY
        ) == Settlement(None, None)

    @pytest.mark.parametrize("status", ["pending", "skipped"])
    def test_a_previous_payment_is_wiped(self, status):
        settled = settle(
            occurrence(paid_at=STAMPED, paid_on=TODAY),
            status=status,
            paid_on=None,
            today=TODAY,
        )

        assert settled == Settlement(None, None)

    def test_a_future_date_is_not_refused(self):
        # La date ne compte que si la ligne est marquee payee : sans cette garde,
        # repasser une ligne en attente deviendrait impossible.
        settled = settle(
            occurrence(), status="pending", paid_on=TODAY + timedelta(days=5), today=TODAY
        )

        assert settled == Settlement(None, None)


class TestPaid:
    def test_no_date_given_means_today(self):
        assert settle(occurrence(), status="paid", paid_on=None, today=TODAY).on == TODAY

    def test_a_past_date_is_kept(self):
        late = TODAY - timedelta(days=2)

        assert settle(occurrence(), status="paid", paid_on=late, today=TODAY).on == late

    def test_today_is_allowed(self):
        assert settle(occurrence(), status="paid", paid_on=TODAY, today=TODAY).on == TODAY

    def test_tomorrow_is_refused(self):
        with pytest.raises(FuturePaymentDate):
            settle(
                occurrence(), status="paid", paid_on=TODAY + timedelta(days=1), today=TODAY
            )

    def test_the_first_settlement_is_stamped(self):
        settled = settle(occurrence(), status="paid", paid_on=TODAY, today=TODAY)

        assert settled.at is not None
        assert settled.at.tzinfo is not None

    def test_an_existing_stamp_is_never_moved(self):
        settled = settle(
            occurrence(paid_at=STAMPED), status="paid", paid_on=TODAY, today=TODAY
        )

        assert settled.at == STAMPED

    def test_the_two_fields_always_travel_together(self):
        settled = settle(occurrence(), status="paid", paid_on=TODAY, today=TODAY)

        assert (settled.at is None) == (settled.on is None)
