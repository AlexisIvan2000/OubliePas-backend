import pytest

from core.config import REFRESH_COOKIE_NAME
from services.authentication.email_password import MAX_LOGIN_ATTEMPTS_PER_HOUR

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


@pytest.fixture
def other_verified(client, mailbox):
    account = {
        "first_name": "Sophie",
        "email": "sophie@example.com",
        "password": "MotDePasse1!",
    }
    assert client.post("/v1/auth/register", json=account).status_code == 201
    code = mailbox[-1]["code"]
    assert client.post(
        "/v1/auth/verify-email", json={"email": account["email"], "code": code}
    ).status_code == 200
    return account


def wrong(client, email):
    return client.post("/v1/auth/login", json={"email": email, "password": "Mauvais1!"})


def right(client, account):
    return client.post(
        "/v1/auth/login",
        json={"email": account["email"], "password": account["password"]},
    )


def exhaust(client, account):
    for _ in range(MAX_LOGIN_ATTEMPTS_PER_HOUR):
        assert wrong(client, account["email"]).status_code == 401


class TestPerAccountLimit:
    def test_the_counter_lives_in_the_database(self, client, verified, db):
        wrong(client, verified["email"])
        wrong(client, verified["email"])

        [(count, at)] = db(
            "select failed_login_count, last_failed_login_at from users where email = :e",
            e=verified["email"],
        )
        assert count == 2
        assert at is not None

    def test_the_right_password_is_refused_once_the_quota_is_spent(
        self, client, verified
    ):
        exhaust(client, verified)

        response = right(client, verified)

        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "INVALID_CREDENTIALS"

    def test_the_refusal_is_indistinguishable_from_a_wrong_password(
        self, client, verified
    ):
        exhaust(client, verified)

        locked = right(client, verified)
        unknown = wrong(client, "inconnu@example.com")

        assert locked.status_code == unknown.status_code
        assert locked.json() == unknown.json()

    def test_one_attempt_short_of_the_quota_still_lets_you_in(self, client, verified):
        for _ in range(MAX_LOGIN_ATTEMPTS_PER_HOUR - 1):
            wrong(client, verified["email"])

        assert right(client, verified).status_code == 200

    def test_a_successful_login_clears_the_counter(self, client, verified, db):
        wrong(client, verified["email"])
        right(client, verified)

        [(count, at)] = db(
            "select failed_login_count, last_failed_login_at from users where email = :e",
            e=verified["email"],
        )
        assert count == 0
        assert at is None

    def test_the_quota_reopens_after_the_window(self, client, verified, db):
        exhaust(client, verified)
        assert right(client, verified).status_code == 401

        db(
            "update users set last_failed_login_at = last_failed_login_at"
            " - interval '2 hours' where email = :e",
            e=verified["email"],
        )

        assert right(client, verified).status_code == 200

    def test_an_unknown_address_is_never_locked(self, client):
        codes = {
            wrong(client, "inconnu@example.com").status_code
            for _ in range(MAX_LOGIN_ATTEMPTS_PER_HOUR + 5)
        }

        assert codes == {401}

    def test_the_lock_survives_a_failed_attempt_on_another_account(
        self, client, verified, other_verified
    ):
        exhaust(client, verified)

        assert right(client, other_verified).status_code == 200
        assert right(client, verified).status_code == 401
