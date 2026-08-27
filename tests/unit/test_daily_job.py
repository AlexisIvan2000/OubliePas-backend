import pytest

from jobs.daily import RESEND_DAILY_ALERT_THRESHOLD, exit_code, should_alert

SEUIL = RESEND_DAILY_ALERT_THRESHOLD
OPERATEUR = "ops@example.com"


class TestExitCode:
    def test_a_clean_run_succeeds(self):
        assert exit_code({"users": 3, "occurrences": 7, "failed": 0}) == 0

    def test_a_failed_delivery_fails_the_run(self):
        assert exit_code({"users": 0, "occurrences": 0, "failed": 2}) == 1

    def test_a_skipped_run_is_not_a_failure(self):
        assert exit_code({"skipped": "another run is already in progress"}) == 0

    def test_a_skipped_account_is_not_a_failure(self):
        assert exit_code({"users": 1, "occurrences": 2, "skipped": 4, "failed": 0}) == 0

    @pytest.mark.parametrize("failed", [1, 5, 500])
    def test_any_failure_is_enough(self, failed):
        assert exit_code({"failed": failed}) == 1


class TestQuotaAlert:
    def test_it_stays_silent_under_the_threshold(self):
        assert should_alert(SEUIL - 1, OPERATEUR) is False

    def test_it_stays_silent_on_the_threshold(self):
        # Le seuil est une limite atteinte, pas franchie : 80 est encore dans
        # la marge, 81 est le premier chiffre qui doit faire lever la tete.
        assert should_alert(SEUIL, OPERATEUR) is False

    def test_it_speaks_one_above(self):
        assert should_alert(SEUIL + 1, OPERATEUR) is True

    @pytest.mark.parametrize("absent", [None, ""])
    def test_no_operator_means_no_alert(self, absent):
        assert should_alert(SEUIL + 1, absent) is False

    def test_a_quiet_day_never_alerts(self):
        assert should_alert(0, OPERATEUR) is False
