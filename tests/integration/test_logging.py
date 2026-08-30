import logging

import pytest

from core.observability import ContextFilter
from services.commitments.occurrence_generator import today_utc

pytestmark = pytest.mark.integration


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def token(verified):
    return verified["tokens"]["access_token"]


@pytest.fixture
def user_id(client, token):
    return client.get("/v1/users/me", headers=auth(token)).json()["id"]


@pytest.fixture
def enrichi(caplog):
    # caplog pose son propre gestionnaire : sans le filtre, ses lignes n'ont
    # ni request_id ni caller.
    caplog.handler.addFilter(ContextFilter())
    return caplog


def lignes(caplog, logger_name=None):
    return [
        record
        for record in caplog.records
        if logger_name is None or record.name == logger_name
    ]


class TestTheRequestIdentifier:
    def test_it_comes_back_in_the_response(self, client):
        response = client.get("/health")

        assert response.headers["X-Request-ID"]

    def test_a_supplied_one_is_kept(self, client):
        response = client.get("/health", headers={"X-Request-ID": "trace-abc"})

        assert response.headers["X-Request-ID"] == "trace-abc"

    def test_a_forged_one_is_replaced(self, client):
        response = client.get(
            "/health", headers={"X-Request-ID": "faux INFO ligne inventee"}
        )

        assert response.headers["X-Request-ID"] != "faux INFO ligne inventee"

    def test_two_requests_get_different_ones(self, client):
        premier = client.get("/health").headers["X-Request-ID"]
        second = client.get("/health").headers["X-Request-ID"]

        assert premier != second


class TestTheAccessLog:
    def test_each_request_leaves_a_line(self, client, token, caplog):
        with caplog.at_level(logging.INFO, logger="api.access"):
            client.get("/v1/users/me", headers=auth(token))

        message = lignes(caplog, "api.access")[-1].getMessage()
        assert "GET /v1/users/me" in message
        assert "200" in message

    def test_the_line_says_who_asked(self, client, token, user_id, enrichi):
        with enrichi.at_level(logging.INFO, logger="api.access"):
            client.get("/v1/users/me", headers=auth(token))

        assert lignes(enrichi, "api.access")[-1].caller == user_id

    def test_an_anonymous_caller_is_marked_as_such(self, client, enrichi):
        with enrichi.at_level(logging.INFO, logger="api.access"):
            client.get("/v1/commitments")

        assert lignes(enrichi, "api.access")[-1].caller.startswith("ip:")

    def test_the_health_probe_stays_quiet(self, client, caplog):
        with caplog.at_level(logging.INFO, logger="api.access"):
            client.get("/health")

        assert lignes(caplog, "api.access") == []


class TestWhatTheErrorsSay:
    def test_a_refused_action_names_its_code(self, client, token, caplog):
        with caplog.at_level(logging.INFO, logger="app"):
            client.post(
                "/v1/commitments",
                json={"title": "x", "type": "subscription", "amount": "1.00"},
                headers=auth(token),
            )

        assert any("VALIDATION_ERROR" in r.getMessage() or "validation refused" in r.getMessage()
                   for r in caplog.records)

    def test_a_validation_line_carries_field_names_and_not_their_values(
        self, client, token, caplog
    ):
        with caplog.at_level(logging.INFO):
            client.patch(
                "/v1/users/me", json={"currency": "MAUVAIS"}, headers=auth(token)
            )

        journal = " ".join(r.getMessage() for r in caplog.records)
        assert "currency" in journal
        assert "MAUVAIS" not in journal

    def test_an_expired_token_does_not_shout(self, client, caplog):
        with caplog.at_level(logging.INFO):
            client.get("/v1/users/me", headers=auth("jeton-invalide"))

        assert not any("INVALID_ACCESS_TOKEN" in r.getMessage() for r in caplog.records)


class TestTheTrail:
    def test_a_deletion_is_recoverable_from_the_log(self, client, token, user_id, caplog):
        created = client.post(
            "/v1/commitments",
            json={
                "title": "Netflix",
                "type": "subscription",
                "category": "entertainment",
                "amount": "18.99",
                "frequency": "monthly",
                "starts_on": today_utc().isoformat(),
            },
            headers=auth(token),
        ).json()
        assert "id" in created, created

        with caplog.at_level(logging.INFO):
            client.delete(f"/v1/commitments/{created['id']}", headers=auth(token))

        journal = " ".join(r.getMessage() for r in caplog.records)
        assert user_id in journal
        assert created["id"] in journal
