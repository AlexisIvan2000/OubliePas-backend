import pytest

from core.config import REFRESH_COOKIE_NAME

pytestmark = pytest.mark.integration


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_returns_the_current_user(client, verified):
    response = client.get("/v1/auth/me", headers=auth(verified["tokens"]["access_token"]))
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == verified["email"]
    assert body["first_name"] == verified["first_name"]
    assert body["is_verified"] is True
    assert body["role"] == "user"


def test_never_exposes_sensitive_fields(client, verified):
    body = client.get("/v1/auth/me", headers=auth(verified["tokens"]["access_token"])).json()
    for field in ("password_hash", "verification_code_hash", "admin_notes", "reset_code_hash"):
        assert field not in body


def test_missing_token_is_rejected(client):
    response = client.get("/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "INVALID_ACCESS_TOKEN"


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c"])
def test_malformed_token_is_rejected(client, token):
    assert client.get("/v1/auth/me", headers=auth(token)).status_code == 401


def test_refresh_token_is_not_accepted(client, verified):
    response = client.get("/v1/auth/me", headers=auth(client.cookies.get(REFRESH_COOKIE_NAME)))
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "INVALID_ACCESS_TOKEN"


def test_token_of_a_deleted_user_is_rejected(client, verified, db):
    db("delete from users where email = :e", e=verified["email"])
    response = client.get("/v1/auth/me", headers=auth(verified["tokens"]["access_token"]))
    assert response.status_code == 401


def test_disabled_account_is_rejected(client, verified, db):
    db("update users set is_active = false where email = :e", e=verified["email"])
    response = client.get("/v1/auth/me", headers=auth(verified["tokens"]["access_token"]))
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ACCOUNT_DISABLED"


def test_role_change_is_reflected_immediately(client, verified, db):
    db("update users set role = 'admin' where email = :e", e=verified["email"])
    body = client.get("/v1/auth/me", headers=auth(verified["tokens"]["access_token"])).json()
    assert body["role"] == "admin"


def test_health_endpoint_needs_no_auth(client):
    assert client.get("/health").json() == {"status": "ok"}
