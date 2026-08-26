import pytest

from services.commitments.commitment_service import CommitmentService

pytestmark = pytest.mark.integration

ORIGIN = "http://localhost:5173"
SECURITY_HEADERS = (
    "x-content-type-options",
    "x-frame-options",
    "content-security-policy",
    "referrer-policy",
)


def auth(token):
    return {"Authorization": f"Bearer {token}", "Origin": ORIGIN}


def summary(client, token):
    return client.get("/v1/commitments/summary", headers=auth(token))


@pytest.fixture
def token(verified):
    return verified["tokens"]["access_token"]


@pytest.fixture
def broken(monkeypatch):
    async def explode(self, *args, **kwargs):
        raise RuntimeError(
            "connexion refusee vers postgresql://user:motdepasse@db.interne:5432"
        )

    monkeypatch.setattr(CommitmentService, "summary", explode)


@pytest.fixture
def in_production(monkeypatch):
    monkeypatch.setattr("api.middlewares.security_headers.DEBUG", False)


@pytest.mark.usefixtures("broken")
class TestUnhandledError:
    def test_answers_with_the_shared_envelope(self, client, token):
        response = summary(client, token)

        assert response.status_code == 500
        assert response.json() == {
            "detail": {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred",
            }
        }

    def test_says_nothing_about_what_broke(self, client, token):
        body = summary(client, token).text

        for leak in (
            "Traceback",
            "RuntimeError",
            "motdepasse",
            "db.interne",
            "postgresql://",
            "commitment_service",
            "site-packages",
        ):
            assert leak not in body

    def test_carries_the_cors_header(self, client, token):
        # Sans lui le navigateur presente une erreur CORS opaque : le front ne voit
        # ni le code ni le corps, et une panne serveur se lit comme une coupure reseau.
        assert summary(client, token).headers.get("access-control-allow-origin") == ORIGIN

    @pytest.mark.parametrize("header", SECURITY_HEADERS)
    def test_carries_the_security_headers(self, client, token, header):
        assert header in summary(client, token).headers

    def test_carries_hsts_once_out_of_debug(self, client, token, in_production):
        assert "strict-transport-security" in summary(client, token).headers

    def test_is_protected_exactly_like_a_healthy_response(self, client, token):
        healthy = client.get("/health", headers={"Origin": ORIGIN})
        failed = summary(client, token)

        assert failed.status_code == 500
        assert protection(failed) == protection(healthy)


def protection(response):
    kept = {*SECURITY_HEADERS, "strict-transport-security", "access-control-allow-origin"}
    return {name: value for name, value in response.headers.items() if name in kept}


class TestNoStatusIsLeftBare:
    def test_healthy_response(self, client):
        self._assert_protected(client.get("/health", headers={"Origin": ORIGIN}), 200)

    def test_missing_token(self, client):
        self._assert_protected(
            client.get("/v1/commitments", headers={"Origin": ORIGIN}), 401
        )

    def test_unknown_path(self, client):
        self._assert_protected(
            client.get("/v1/nexistepas", headers={"Origin": ORIGIN}), 404
        )

    def test_invalid_payload(self, client):
        self._assert_protected(
            client.post(
                "/v1/auth/login",
                json={"email": "pasunemail"},
                headers={"Origin": ORIGIN},
            ),
            422,
        )

    def test_unhandled_error(self, client, token, broken):
        self._assert_protected(summary(client, token), 500)

    @staticmethod
    def _assert_protected(response, status):
        assert response.status_code == status
        assert [h for h in SECURITY_HEADERS if h not in response.headers] == []
        assert response.headers.get("access-control-allow-origin") == ORIGIN
