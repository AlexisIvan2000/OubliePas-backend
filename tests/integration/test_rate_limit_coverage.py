import pytest

from app import app
from limits import parse_many

from core.rate_limit import EMAIL_LIMIT, READ_LIMIT, limiter

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


MAILERS = [
    "api.v1.client.auth.register",
    "api.v1.client.auth.resend_verification",
    "api.v1.client.auth.forgot_password",
]

BOUNDS = sorted(parse_many(EMAIL_LIMIT), key=lambda item: item.GRANULARITY.seconds)
SHORT, LONG = BOUNDS[0], BOUNDS[-1]

FORGOT = "/v1/auth/forgot-password"
UNKNOWN = {"email": "personne@example.com"}


def roll_the_short_window():
    # Attendre soixante secondes dans la suite n'est pas une option : on vide le
    # compteur de la borne courte a la main, celui de la borne longue continue
    # de courir. C'est le seul moyen d'atteindre la seconde borne.
    storage = limiter._storage
    for key in [key for key in storage.storage if key.endswith("minute")]:
        storage.storage.pop(key, None)
        storage.expirations.pop(key, None)


class TestEmailLimit:
    def test_the_limit_is_a_pair_of_bounds(self):
        assert len(BOUNDS) == 2
        assert SHORT.GRANULARITY.seconds < LONG.GRANULARITY.seconds
        assert SHORT.amount < LONG.amount

    def test_the_pair_is_declared_on_every_route_that_sends_a_mail(self):
        attendu = {str(SHORT), str(LONG)}

        for name in MAILERS:
            declare = {str(item.limit) for item in limiter._route_limits[name]}
            assert declare == attendu, name

    def test_the_short_bound_refuses_the_next_one_in_the_same_minute(self, client, rate_limit_on):
        codes = [client.post(FORGOT, json=UNKNOWN).status_code for _ in range(SHORT.amount + 1)]

        assert codes == [200] * SHORT.amount + [429]

    def test_a_shared_address_gets_a_second_round_at_the_next_minute(self, client, rate_limit_on):
        # C'est la raison du changement : sous un plafond horaire unique, la
        # sixieme tentative d'un NAT partage restait refusee jusqu'a l'heure
        # suivante.
        for _ in range(SHORT.amount):
            client.post(FORGOT, json=UNKNOWN)
        assert client.post(FORGOT, json=UNKNOWN).status_code == 429

        roll_the_short_window()

        assert client.post(FORGOT, json=UNKNOWN).status_code == 200

    def test_the_long_bound_still_closes_the_hour(self, client, rate_limit_on):
        codes = []
        for _ in range(LONG.amount + 1):
            codes.append(client.post(FORGOT, json=UNKNOWN).status_code)
            if len(codes) % SHORT.amount == 0:
                roll_the_short_window()

        assert codes[: LONG.amount] == [200] * LONG.amount
        assert codes[-1] == 429

    def test_the_refusal_keeps_the_shared_envelope(self, client, rate_limit_on):
        for _ in range(SHORT.amount + 1):
            response = client.post(FORGOT, json=UNKNOWN)

        assert response.json()["detail"]["code"] == "RATE_LIMIT_EXCEEDED"


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
