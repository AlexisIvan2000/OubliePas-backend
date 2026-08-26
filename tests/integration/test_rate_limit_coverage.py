import pytest

from app import app
from core.rate_limit import READ_LIMIT, limiter

pytestmark = pytest.mark.integration

MARKED = limiter._Limiter__marked_for_limiting


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def token(verified):
    return verified["tokens"]["access_token"]


CLIENT_MODULES = "api.v1.client."


def walk(node):
    # FastAPI n'aplatit pas les routers inclus : il les remplace par un
    # _IncludedRouter qui n'expose ses routes que via original_router. Sans cette
    # descente, l'inventaire serait vide et le test passerait a vide.
    inner = getattr(node, "original_router", None)
    if inner is not None:
        yield from walk(inner)
        return

    for route in getattr(node, "routes", []):
        if getattr(route, "original_router", None) is not None or hasattr(route, "routes"):
            yield from walk(route)
            continue
        if getattr(route, "endpoint", None) is not None:
            yield route


def client_routes():
    for route in walk(app):
        endpoint = route.endpoint
        if not endpoint.__module__.startswith(CLIENT_MODULES):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            yield method, route.path, f"{endpoint.__module__}.{endpoint.__name__}"


class TestEveryRouteIsCapped:
    def test_no_client_route_is_left_without_a_limit(self):
        bare = [
            f"{method} {path}"
            for method, path, name in client_routes()
            if name not in MARKED
        ]

        assert bare == []

    def test_the_inventory_is_not_empty(self):
        assert len(list(client_routes())) > 25


class TestReadLimit:
    def test_it_is_a_single_value_shared_by_the_reads(self):
        reads = {
            name
            for method, path, name in client_routes()
            if method == "GET"
        }

        assert reads, "il doit rester des lectures a couvrir"
        assert reads <= set(MARKED)

    def test_a_read_eventually_answers_429(self, client, token, rate_limit_on):
        allowed = int(READ_LIMIT.split("/")[0])

        codes = {
            client.get("/v1/auth/me", headers=auth(token)).status_code
            for _ in range(allowed + 2)
        }

        assert codes == {200, 429}

    def test_the_refusal_keeps_the_shared_envelope(self, client, token, rate_limit_on):
        allowed = int(READ_LIMIT.split("/")[0])
        for _ in range(allowed + 2):
            response = client.get("/v1/auth/me", headers=auth(token))
            if response.status_code == 429:
                break

        assert response.json()["detail"]["code"] == "RATE_LIMIT_EXCEEDED"

    def test_the_quota_follows_the_account_not_the_address(
        self, client, verified, other_token, rate_limit_on
    ):
        # Meme socket, deux comptes : le second ne doit pas payer pour le premier.
        allowed = int(READ_LIMIT.split("/")[0])
        first = verified["tokens"]["access_token"]
        for _ in range(allowed + 2):
            client.get("/v1/auth/me", headers=auth(first))

        assert client.get("/v1/auth/me", headers=auth(first)).status_code == 429
        assert client.get("/v1/auth/me", headers=auth(other_token)).status_code == 200


@pytest.fixture
def other_token(client, mailbox):
    account = {
        "first_name": "Sophie",
        "email": "sophie@example.com",
        "password": "MotDePasse1!",
    }
    assert client.post("/v1/auth/register", json=account).status_code == 201
    code = mailbox[-1]["code"]
    response = client.post(
        "/v1/auth/verify-email", json={"email": account["email"], "code": code}
    )
    assert response.status_code == 200
    return response.json()["access_token"]
