import pytest

from core.config import REFRESH_COOKIE_NAME

pytestmark = pytest.mark.integration


def test_login_returns_tokens(client, verified):
    response = client.post(
        "/v1/auth/login",
        json={"email": verified["email"], "password": verified["password"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert "refresh_token" not in body
    assert REFRESH_COOKIE_NAME in response.headers.get("set-cookie", "")
    assert body["token_type"] == "bearer"
    assert body["role"] == "user"


def test_login_stores_a_hashed_refresh_token(client, verified, db):
    response = client.post(
        "/v1/auth/login",
        json={"email": verified["email"], "password": verified["password"]},
    )
    [(stored,)] = db("select token_hash from refresh_tokens order by created_at desc limit 1")
    assert stored != client.cookies.get(REFRESH_COOKIE_NAME)
    assert len(stored) == 64
    assert len(stored) == 64


def test_login_is_case_insensitive_on_email(client, verified):
    response = client.post(
        "/v1/auth/login",
        json={"email": verified["email"].upper(), "password": verified["password"]},
    )
    assert response.status_code == 200


def test_wrong_password_is_rejected(client, verified):
    response = client.post("/v1/auth/login", json={"email": verified["email"], "password": "Mauvais1!"})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "INVALID_CREDENTIALS"


def test_unknown_email_is_rejected(client):
    response = client.post("/v1/auth/login", json={"email": "inconnu@example.com", "password": "MotDePasse1!"})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "INVALID_CREDENTIALS"


def test_unknown_email_and_wrong_password_are_indistinguishable(client, verified):
    unknown = client.post("/v1/auth/login", json={"email": "inconnu@example.com", "password": "MotDePasse1!"})
    wrong = client.post("/v1/auth/login", json={"email": verified["email"], "password": "Mauvais1!"})
    assert unknown.status_code == wrong.status_code
    assert unknown.json() == wrong.json()


def test_unverified_account_cannot_log_in(client, registered):
    response = client.post(
        "/v1/auth/login",
        json={"email": registered["email"], "password": registered["password"]},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "EMAIL_NOT_VERIFIED"


def test_disabled_account_cannot_log_in(client, verified, db):
    db("update users set is_active = false where email = :e", e=verified["email"])
    response = client.post(
        "/v1/auth/login",
        json={"email": verified["email"], "password": verified["password"]},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ACCOUNT_DISABLED"


def test_disabled_account_reveals_nothing_about_the_password(client, verified, db):
    db("update users set is_active = false where email = :e", e=verified["email"])
    response = client.post("/v1/auth/login", json={"email": verified["email"], "password": "Mauvais1!"})
    assert response.status_code == 401


def test_admin_role_is_returned(client, verified, db):
    db("update users set role = 'admin' where email = :e", e=verified["email"])
    response = client.post(
        "/v1/auth/login",
        json={"email": verified["email"], "password": verified["password"]},
    )
    assert response.json()["role"] == "admin"


def test_login_is_rate_limited(client, verified, rate_limit_on):
    codes = [
        client.post("/v1/auth/login", json={"email": verified["email"], "password": "Mauvais1!"}).status_code
        for _ in range(12)
    ]
    assert 429 in codes
