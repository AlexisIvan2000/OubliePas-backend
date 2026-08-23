import pytest

from jobs.daily import exit_code


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
