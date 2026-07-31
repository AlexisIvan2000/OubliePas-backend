import pytest

from core import rate_limit
from core.rate_limit import client_ip, get_user_id_from_jwt
from core.security import Security

pytestmark = pytest.mark.unit


class FakeRequest:
    def __init__(self, headers=None, peer="203.0.113.7"):
        self.headers = headers or {}
        self.client = type("Client", (), {"host": peer})() if peer else None


@pytest.fixture
def proxies(monkeypatch):
    def setter(count):
        monkeypatch.setattr(rate_limit, "TRUSTED_PROXY_COUNT", count)

    return setter


class TestClientIp:
    def test_without_trusted_proxy_the_socket_wins(self, proxies):
        proxies(0)
        request = FakeRequest({"x-forwarded-for": "9.9.9.9"})
        assert client_ip(request) == "203.0.113.7"

    def test_single_forwarded_entry_is_used(self, proxies):
        proxies(1)
        request = FakeRequest({"x-forwarded-for": "198.51.100.4"})
        assert client_ip(request) == "198.51.100.4"

    def test_spoofed_prefix_is_ignored(self, proxies):
        proxies(1)
        request = FakeRequest({"x-forwarded-for": "9.9.9.9, 198.51.100.4"})
        assert client_ip(request) == "198.51.100.4"

    def test_long_spoofed_chain_is_ignored(self, proxies):
        proxies(1)
        request = FakeRequest({"x-forwarded-for": "1.1.1.1, 2.2.2.2, 3.3.3.3, 198.51.100.4"})
        assert client_ip(request) == "198.51.100.4"

    def test_two_trusted_proxies_skip_two_hops(self, proxies):
        proxies(2)
        request = FakeRequest({"x-forwarded-for": "9.9.9.9, 198.51.100.4, 10.0.0.1"})
        assert client_ip(request) == "198.51.100.4"

    def test_more_trusted_proxies_than_hops_falls_back_to_the_first(self, proxies):
        proxies(3)
        request = FakeRequest({"x-forwarded-for": "198.51.100.4"})
        assert client_ip(request) == "198.51.100.4"

    def test_whitespace_is_trimmed(self, proxies):
        proxies(1)
        request = FakeRequest({"x-forwarded-for": "  9.9.9.9 ,  198.51.100.4  "})
        assert client_ip(request) == "198.51.100.4"

    def test_empty_header_falls_back_to_the_socket(self, proxies):
        proxies(1)
        request = FakeRequest({"x-forwarded-for": "   "})
        assert client_ip(request) == "203.0.113.7"

    def test_missing_header_falls_back_to_the_socket(self, proxies):
        proxies(1)
        assert client_ip(FakeRequest()) == "203.0.113.7"

    def test_no_socket_and_no_header_is_still_a_key(self, proxies):
        proxies(0)
        assert client_ip(FakeRequest(peer=None)) == "127.0.0.1"


class TestJwtKey:
    def test_valid_token_keys_on_the_user(self, proxies):
        proxies(0)
        token = Security.create_access_token("abc-123")
        request = FakeRequest({"authorization": f"Bearer {token}"})
        assert get_user_id_from_jwt(request) == "abc-123"

    @pytest.mark.parametrize("header", ["", "Bearer", "Bearer garbage", "Bearer a.b.c", "Basic xyz"])
    def test_unusable_header_falls_back_to_the_ip(self, proxies, header):
        proxies(0)
        request = FakeRequest({"authorization": header} if header else {})
        assert get_user_id_from_jwt(request) == "203.0.113.7"

    def test_fallback_respects_the_proxy_setting(self, proxies):
        proxies(1)
        request = FakeRequest({"x-forwarded-for": "9.9.9.9, 198.51.100.4"})
        assert get_user_id_from_jwt(request) == "198.51.100.4"


class TestLocalDefaults:
    def test_storage_is_in_memory_without_redis(self):
        from core.config import REDIS_URL
        from core.rate_limit import limiter

        if REDIS_URL:
            pytest.skip("REDIS_URL est defini dans cet environnement")
        assert type(limiter._storage).__name__ == "MemoryStorage"

    def test_default_trusted_proxy_count_is_zero(self):
        import os

        if "TRUSTED_PROXY_COUNT" in os.environ:
            pytest.skip("TRUSTED_PROXY_COUNT est defini dans cet environnement")
        from core.config import TRUSTED_PROXY_COUNT

        assert TRUSTED_PROXY_COUNT == 0
