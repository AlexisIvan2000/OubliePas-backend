import pytest

pytestmark = pytest.mark.integration


def essais(db, email, kind):
    rows = db(
        "select a.count from verification_attempts a"
        " join users u on u.id = a.user_id"
        " where u.email = :e and a.kind = :k",
        e=email,
        k=kind,
    )
    return rows[0][0] if rows else 0


NEW_EMAIL = "nouvelle@example.com"


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def token(verified):
    return verified["tokens"]["access_token"]


@pytest.fixture
def requested(client, verified, token, mailbox):
    response = client.post(
        "/v1/users/me/change-email",
        headers=auth(token),
        json={"new_email": NEW_EMAIL, "password": verified["password"]},
    )
    assert response.status_code == 200
    return mailbox[-1]["code"]


class TestRequestEmailChange:
    def test_sends_a_code_to_the_new_address(self, client, verified, token, mailbox):
        response = client.post(
            "/v1/users/me/change-email",
            headers=auth(token),
            json={"new_email": NEW_EMAIL, "password": verified["password"]},
        )
        assert response.status_code == 200
        assert mailbox[-1]["kind"] == "email_change"
        assert mailbox[-1]["to"] == NEW_EMAIL

    def test_stores_the_pending_email(self, client, verified, token, requested, db):
        assert db("select pending_email from users where email = :e", e=verified["email"]) == [
            (NEW_EMAIL,)
        ]

    def test_current_email_is_untouched_until_confirmation(self, client, verified, token, requested):
        assert client.get("/v1/users/me", headers=auth(token)).json()["email"] == verified["email"]

    def test_new_email_is_normalized(self, client, verified, token, mailbox, db):
        client.post(
            "/v1/users/me/change-email",
            headers=auth(token),
            json={"new_email": "Nouvelle@Example.COM", "password": verified["password"]},
        )
        assert db("select pending_email from users where email = :e", e=verified["email"]) == [
            (NEW_EMAIL,)
        ]

    def test_wrong_password_is_rejected(self, client, token):
        response = client.post(
            "/v1/users/me/change-email",
            headers=auth(token),
            json={"new_email": NEW_EMAIL, "password": "MauvaisPass1!"},
        )
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "INCORRECT_PASSWORD"

    def test_wrong_password_leaves_no_pending_email(self, client, verified, token, db, mailbox):
        before = len(mailbox)
        client.post(
            "/v1/users/me/change-email",
            headers=auth(token),
            json={"new_email": NEW_EMAIL, "password": "MauvaisPass1!"},
        )
        assert db("select pending_email from users where email = :e", e=verified["email"]) == [(None,)]
        assert len(mailbox) == before

    def test_own_email_is_rejected(self, client, verified, token):
        response = client.post(
            "/v1/users/me/change-email",
            headers=auth(token),
            json={"new_email": verified["email"], "password": verified["password"]},
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "SAME_EMAIL_AS_CURRENT"

    def test_disposable_email_is_rejected(self, client, verified, token):
        response = client.post(
            "/v1/users/me/change-email",
            headers=auth(token),
            json={"new_email": "jetable@yopmail.com", "password": verified["password"]},
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "DISPOSABLE_EMAIL_NOT_ALLOWED"

    def test_email_taken_by_someone_else_is_rejected(self, client, verified, token, db):
        db(
            "insert into users (id, first_name, email, currency,"
            " code_resend_count, is_verified, is_active, role, created_at, updated_at)"
            " values (gen_random_uuid(), 'Autre', :e, 'CAD', 0, true, true, 'user', now(), now())",
            e=NEW_EMAIL,
        )
        response = client.post(
            "/v1/users/me/change-email",
            headers=auth(token),
            json={"new_email": NEW_EMAIL, "password": verified["password"]},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "EMAIL_ALREADY_IN_USE"

    def test_google_account_is_rejected(self, client, verified, token, db):
        db("update users set password_hash = null where email = :e", e=verified["email"])
        response = client.post(
            "/v1/users/me/change-email",
            headers=auth(token),
            json={"new_email": NEW_EMAIL, "password": verified["password"]},
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "GOOGLE_ONLY_ACCOUNT"

    def test_malformed_email_is_rejected(self, client, verified, token):
        response = client.post(
            "/v1/users/me/change-email",
            headers=auth(token),
            json={"new_email": "pas-un-email", "password": verified["password"]},
        )
        assert response.status_code == 422

    def test_requires_authentication(self, client, verified):
        response = client.post(
            "/v1/users/me/change-email",
            json={"new_email": NEW_EMAIL, "password": verified["password"]},
        )
        assert response.status_code == 401


class TestConfirmEmailChange:
    def test_replaces_the_email(self, client, token, requested):
        response = client.post(
            "/v1/users/me/confirm-email-change", headers=auth(token), json={"code": requested}
        )
        assert response.status_code == 200
        assert client.get("/v1/users/me", headers=auth(token)).json()["email"] == NEW_EMAIL

    def test_clears_the_pending_state(self, client, token, requested, db):
        client.post(
            "/v1/users/me/confirm-email-change", headers=auth(token), json={"code": requested}
        )
        assert db(
            "select pending_email, email_change_code_hash from users where email = :e",
            e=NEW_EMAIL,
        ) == [(None, None)]
        assert essais(db, NEW_EMAIL, "email_change") == 0

    def test_login_works_with_the_new_email(self, client, verified, token, requested):
        client.post(
            "/v1/users/me/confirm-email-change", headers=auth(token), json={"code": requested}
        )
        response = client.post(
            "/v1/auth/login", json={"email": NEW_EMAIL, "password": verified["password"]}
        )
        assert response.status_code == 200

    def test_login_fails_with_the_old_email(self, client, verified, token, requested):
        client.post(
            "/v1/users/me/confirm-email-change", headers=auth(token), json={"code": requested}
        )
        response = client.post(
            "/v1/auth/login", json={"email": verified["email"], "password": verified["password"]}
        )
        assert response.status_code == 401

    def test_confirming_without_a_request_is_rejected(self, client, token):
        response = client.post(
            "/v1/users/me/confirm-email-change", headers=auth(token), json={"code": "123456"}
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "NO_PENDING_EMAIL_CHANGE"

    def test_wrong_code_is_rejected(self, client, token, requested):
        response = client.post(
            "/v1/users/me/confirm-email-change", headers=auth(token), json={"code": "000000"}
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "INVALID_VERIFICATION_CODE"

    def test_wrong_code_increments_the_attempt_counter(self, client, verified, token, requested, db):
        client.post(
            "/v1/users/me/confirm-email-change", headers=auth(token), json={"code": "000000"}
        )
        assert essais(db, verified["email"], "email_change") == 1

    def test_locks_after_five_failures(self, client, token, requested):
        for _ in range(5):
            client.post(
                "/v1/users/me/confirm-email-change", headers=auth(token), json={"code": "000000"}
            )
        response = client.post(
            "/v1/users/me/confirm-email-change", headers=auth(token), json={"code": requested}
        )
        assert response.status_code == 429
        assert response.json()["detail"]["code"] == "TOO_MANY_VERIFICATION_ATTEMPTS"

    def test_locked_request_keeps_the_old_email(self, client, verified, token, requested):
        for _ in range(6):
            client.post(
                "/v1/users/me/confirm-email-change", headers=auth(token), json={"code": "000000"}
            )
        assert client.get("/v1/users/me", headers=auth(token)).json()["email"] == verified["email"]

    def test_expired_code_is_rejected(self, client, token, requested, db):
        db("update users set email_change_code_expires_at = now() - interval '1 minute'")
        response = client.post(
            "/v1/users/me/confirm-email-change", headers=auth(token), json={"code": requested}
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "VERIFICATION_CODE_EXPIRED"

    def test_code_cannot_be_replayed(self, client, token, requested):
        client.post(
            "/v1/users/me/confirm-email-change", headers=auth(token), json={"code": requested}
        )
        response = client.post(
            "/v1/users/me/confirm-email-change", headers=auth(token), json={"code": requested}
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "NO_PENDING_EMAIL_CHANGE"

    def test_address_taken_between_request_and_confirmation_is_rejected(
        self, client, token, requested, db
    ):
        db(
            "insert into users (id, first_name, email, currency,"
            " code_resend_count, is_verified, is_active, role, created_at, updated_at)"
            " values (gen_random_uuid(), 'Autre', :e, 'CAD', 0, true, true, 'user', now(), now())",
            e=NEW_EMAIL,
        )
        response = client.post(
            "/v1/users/me/confirm-email-change", headers=auth(token), json={"code": requested}
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "EMAIL_ALREADY_IN_USE"

    @pytest.mark.parametrize("code", ["12345", "1234567", "abcdef", ""])
    def test_malformed_code_is_rejected(self, client, token, requested, code):
        response = client.post(
            "/v1/users/me/confirm-email-change", headers=auth(token), json={"code": code}
        )
        assert response.status_code == 422

    def test_requires_authentication(self, client, requested):
        response = client.post("/v1/users/me/confirm-email-change", json={"code": requested})
        assert response.status_code == 401


class TestResendEmailChange:
    def test_sends_a_new_code(self, client, token, requested, mailbox):
        response = client.post("/v1/users/me/resend-email-change", headers=auth(token))
        assert response.status_code == 200
        assert mailbox[-1]["kind"] == "email_change"
        assert mailbox[-1]["to"] == NEW_EMAIL

    def test_the_new_code_works(self, client, token, requested, mailbox):
        client.post("/v1/users/me/resend-email-change", headers=auth(token))
        response = client.post(
            "/v1/users/me/confirm-email-change",
            headers=auth(token),
            json={"code": mailbox[-1]["code"]},
        )
        assert response.status_code == 200

    def test_the_previous_code_is_invalidated(self, client, token, requested, mailbox):
        client.post("/v1/users/me/resend-email-change", headers=auth(token))
        response = client.post(
            "/v1/users/me/confirm-email-change", headers=auth(token), json={"code": requested}
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "INVALID_VERIFICATION_CODE"

    def test_resend_without_a_pending_change_is_rejected(self, client, token):
        response = client.post("/v1/users/me/resend-email-change", headers=auth(token))
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "NO_PENDING_EMAIL_CHANGE"

    def test_stops_after_five_codes_in_an_hour(self, client, token, requested, db):
        db("update users set code_resend_count = 1, last_code_sent_at = now()")
        for _ in range(4):
            assert client.post("/v1/users/me/resend-email-change", headers=auth(token)).status_code == 200
        response = client.post("/v1/users/me/resend-email-change", headers=auth(token))
        assert response.status_code == 429
        assert response.json()["detail"]["code"] == "TOO_MANY_CODE_REQUESTS"

    def test_the_counter_window_expires(self, client, token, requested, db):
        db("update users set code_resend_count = 5, last_code_sent_at = now() - interval '2 hours'")
        assert client.post("/v1/users/me/resend-email-change", headers=auth(token)).status_code == 200
        assert db("select code_resend_count from users") == [(1,)]

    def test_requires_authentication(self, client):
        assert client.post("/v1/users/me/resend-email-change").status_code == 401


class TestAttemptsAreScopedToTheirFlow:
    def rate(self, client, email, code="000000"):
        return client.post(
            "/v1/auth/reset-password",
            json={"email": email, "code": code, "new_password": "AutreMotDePasse1!"},
        )

    def test_failing_the_reset_does_not_lock_the_email_change(
        self, client, verified, token, requested, db
    ):
        # Avant la table par flux, cinq codes de reinitialisation faux
        # renvoyaient un 429 sur la confirmation d'adresse, code correct en main.
        assert client.post("/v1/auth/forgot-password", json={"email": verified["email"]}).status_code == 200
        for _ in range(5):
            self.rate(client, verified["email"])

        confirme = client.post(
            "/v1/users/me/confirm-email-change", headers=auth(token), json={"code": requested}
        )

        assert confirme.status_code == 200, confirme.text
        # Le compteur suit le compte, pas l'adresse : elle vient de changer.
        assert essais(db, NEW_EMAIL, "reset") == 5

    def test_asking_a_new_code_does_not_unlock_another_flow(self, client, verified, token, db):
        assert client.post("/v1/auth/forgot-password", json={"email": verified["email"]}).status_code == 200
        for _ in range(5):
            self.rate(client, verified["email"])
        assert essais(db, verified["email"], "reset") == 5

        client.post(
            "/v1/users/me/change-email",
            headers=auth(token),
            json={"new_email": NEW_EMAIL, "password": verified["password"]},
        )

        assert essais(db, verified["email"], "reset") == 5
        assert self.rate(client, verified["email"]).status_code == 429

    def test_a_new_code_of_the_same_flow_gives_its_attempts_back(self, client, verified, token, db):
        client.post(
            "/v1/users/me/change-email",
            headers=auth(token),
            json={"new_email": NEW_EMAIL, "password": verified["password"]},
        )
        for _ in range(3):
            client.post(
                "/v1/users/me/confirm-email-change", headers=auth(token), json={"code": "000000"}
            )
        assert essais(db, verified["email"], "email_change") == 3

        client.post(
            "/v1/users/me/change-email",
            headers=auth(token),
            json={"new_email": "encore@example.com", "password": verified["password"]},
        )

        assert essais(db, verified["email"], "email_change") == 0

    def test_the_three_counters_live_side_by_side(self, client, verified, token, requested, db):
        assert client.post("/v1/auth/forgot-password", json={"email": verified["email"]}).status_code == 200
        self.rate(client, verified["email"])
        self.rate(client, verified["email"])
        client.post(
            "/v1/users/me/confirm-email-change", headers=auth(token), json={"code": "000000"}
        )

        assert essais(db, verified["email"], "reset") == 2
        assert essais(db, verified["email"], "email_change") == 1
        assert essais(db, verified["email"], "verification") == 0
