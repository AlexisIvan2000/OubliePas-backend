import pytest

pytestmark = pytest.mark.integration


def test_correct_code_returns_tokens(client, registered):
    response = client.post(
        "/v1/auth/verify-email",
        json={"email": registered["email"], "code": registered["code"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["token_type"] == "bearer"


def test_correct_code_marks_the_account_verified(client, registered, db):
    client.post("/v1/auth/verify-email", json={"email": registered["email"], "code": registered["code"]})
    rows = db(
        "select is_verified, verification_code_hash, verification_attempts from users where email = :e",
        e=registered["email"],
    )
    assert rows == [(True, None, 0)]


def test_wrong_code_is_rejected(client, registered):
    response = client.post("/v1/auth/verify-email", json={"email": registered["email"], "code": "000000"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_VERIFICATION_CODE"


def test_failed_attempts_are_persisted(client, registered, db):
    for expected in (1, 2, 3):
        client.post("/v1/auth/verify-email", json={"email": registered["email"], "code": "000000"})
        rows = db("select verification_attempts from users where email = :e", e=registered["email"])
        assert rows == [(expected,)]


def test_account_is_locked_after_five_failures(client, registered):
    for _ in range(5):
        client.post("/v1/auth/verify-email", json={"email": registered["email"], "code": "000000"})

    response = client.post("/v1/auth/verify-email", json={"email": registered["email"], "code": "000000"})
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "TOO_MANY_VERIFICATION_ATTEMPTS"


def test_correct_code_is_refused_once_locked(client, registered):
    for _ in range(5):
        client.post("/v1/auth/verify-email", json={"email": registered["email"], "code": "000000"})

    response = client.post(
        "/v1/auth/verify-email",
        json={"email": registered["email"], "code": registered["code"]},
    )
    assert response.status_code == 429


def test_expired_code_is_rejected(client, registered, db):
    db(
        "update users set verification_code_expires_at = now() - interval '1 minute' where email = :e",
        e=registered["email"],
    )
    response = client.post(
        "/v1/auth/verify-email",
        json={"email": registered["email"], "code": registered["code"]},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VERIFICATION_CODE_EXPIRED"


def test_unknown_email_is_rejected(client):
    response = client.post("/v1/auth/verify-email", json={"email": "inconnu@example.com", "code": "123456"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_VERIFICATION_REQUEST"


def test_already_verified_account_is_rejected(client, verified):
    response = client.post(
        "/v1/auth/verify-email",
        json={"email": verified["email"], "code": verified["code"]},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_VERIFICATION_REQUEST"


def test_disabled_account_cannot_verify(client, registered, db):
    db("update users set is_active = false where email = :e", e=registered["email"])
    response = client.post(
        "/v1/auth/verify-email",
        json={"email": registered["email"], "code": registered["code"]},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ACCOUNT_DISABLED"


class TestResendVerification:
    def test_sends_a_new_code(self, client, registered, mailbox):
        response = client.post("/v1/auth/resend-verification", json={"email": registered["email"]})
        assert response.status_code == 200
        assert len(mailbox) == 2
        assert mailbox[-1]["code"] != mailbox[0]["code"]

    def test_previous_code_stops_working(self, client, registered, mailbox):
        client.post("/v1/auth/resend-verification", json={"email": registered["email"]})
        response = client.post(
            "/v1/auth/verify-email",
            json={"email": registered["email"], "code": registered["code"]},
        )
        assert response.status_code == 400

    def test_new_code_works(self, client, registered, mailbox):
        client.post("/v1/auth/resend-verification", json={"email": registered["email"]})
        response = client.post(
            "/v1/auth/verify-email",
            json={"email": registered["email"], "code": mailbox[-1]["code"]},
        )
        assert response.status_code == 200

    def test_unknown_email_gives_the_same_answer(self, client, mailbox):
        response = client.post("/v1/auth/resend-verification", json={"email": "inconnu@example.com"})
        assert response.status_code == 200
        assert mailbox == []

    def test_verified_account_receives_nothing(self, client, verified, mailbox):
        before = len(mailbox)
        response = client.post("/v1/auth/resend-verification", json={"email": verified["email"]})
        assert response.status_code == 200
        assert len(mailbox) == before

    def test_sixth_resend_within_an_hour_is_refused(self, client, registered):
        for _ in range(4):
            assert client.post(
                "/v1/auth/resend-verification", json={"email": registered["email"]}
            ).status_code == 200

        response = client.post("/v1/auth/resend-verification", json={"email": registered["email"]})
        assert response.status_code == 429
        assert response.json()["detail"]["code"] == "TOO_MANY_CODE_REQUESTS"
