import pytest

from core.config import check_cookie_policy, check_cors_policy


class TestSameSite:
    @pytest.mark.parametrize("samesite", ["lax", "strict", "none"])
    def test_accepts_the_three_browser_values(self, samesite):
        check_cookie_policy(samesite, True)

    def test_lax_does_not_need_a_secure_channel(self):
        check_cookie_policy("lax", False)

    def test_cross_site_without_secure_is_refused(self):
        with pytest.raises(RuntimeError, match="COOKIE_SECURE"):
            check_cookie_policy("none", False)

    @pytest.mark.parametrize("samesite", ["None", "LAX", "", "lax; secure", "same-site"])
    def test_a_value_no_browser_understands_is_refused(self, samesite):
        with pytest.raises(RuntimeError, match="COOKIE_SAMESITE"):
            check_cookie_policy(samesite, True)


class TestCorsOrigins:
    def test_accepts_explicit_origins(self):
        check_cors_policy(["https://app.oubliepas.com", "https://oubliepas.com"])

    def test_accepts_an_empty_list(self):
        check_cors_policy([])

    def test_refuses_the_wildcard(self):
        with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
            check_cors_policy(["https://app.oubliepas.com", "*"])
