import pytest

from core.validators import DISPOSABLE_EMAIL_DOMAINS, is_disposable_email, normalize_email

pytestmark = pytest.mark.unit


class TestDisposableEmail:
    @pytest.mark.parametrize(
        "email",
        [
            "a@yopmail.com",
            "a@mailinator.com",
            "a@guerrillamail.com",
            "a@10minutemail.com",
            "a@trashmail.de",
        ],
    )
    def test_known_providers_are_rejected(self, email):
        assert is_disposable_email(email) is True

    @pytest.mark.parametrize(
        "email",
        ["alexis@gmail.com", "a@oubliepas.com", "a@example.com", "a@yahoo.fr"],
    )
    def test_regular_providers_are_accepted(self, email):
        assert is_disposable_email(email) is False

    def test_subdomains_are_rejected(self):
        assert is_disposable_email("a@inbox.mailinator.com") is True

    def test_lookalike_domain_is_accepted(self):
        assert is_disposable_email("a@pasmailinator.com") is False

    def test_suffix_match_stops_at_a_label_boundary(self):
        assert is_disposable_email("a@mailinatorlike.com") is False

    def test_detection_is_case_insensitive(self):
        assert is_disposable_email("A@YOPMAIL.COM") is True

    def test_string_without_at_sign_is_not_disposable(self):
        assert is_disposable_email("pas-un-email") is False

    @pytest.mark.parametrize("email", ["a@", "a@com", "a@."])
    def test_degenerate_domains_are_not_disposable(self, email):
        assert is_disposable_email(email) is False

    def test_blocklist_is_loaded(self):
        assert len(DISPOSABLE_EMAIL_DOMAINS) > 5000


class TestNormalizeEmail:
    def test_lowercases(self):
        assert normalize_email("Alexis@Example.COM") == "alexis@example.com"

    def test_strips_surrounding_whitespace(self):
        assert normalize_email("  alexis@example.com  ") == "alexis@example.com"

    def test_already_normalized_is_unchanged(self):
        assert normalize_email("alexis@example.com") == "alexis@example.com"
