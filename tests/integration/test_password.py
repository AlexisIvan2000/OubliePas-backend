import pytest

pytestmark = pytest.mark.integration

NEW_PASSWORD = "NouveauPass1!"


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def token(verified):
    return verified["tokens"]["access_token"]


def login(client, email, password):
    return client.post("/v1/auth/login", json={"email": email, "password": password})


class TestChangePassword:
    def test_changes_the_password(self, client, verified, token):
        response = client.post(
            "/v1/users/me/change-password",
            headers=auth(token),
            json={"current_password": verified["password"], "new_password": NEW_PASSWORD},
        )
        assert response.status_code == 200
        assert login(client, verified["email"], NEW_PASSWORD).status_code == 200

    def test_old_password_stops_working(self, client, verified, token):
        client.post(
            "/v1/users/me/change-password",
            headers=auth(token),
            json={"current_password": verified["password"], "new_password": NEW_PASSWORD},
        )
        assert login(client, verified["email"], verified["password"]).status_code == 401

    def test_revokes_every_session(self, client, verified, token, db):
        client.post(
            "/v1/users/me/change-password",
            headers=auth(token),
            json={"current_password": verified["password"], "new_password": NEW_PASSWORD},
        )
        assert db("select count(*) from refresh_tokens where revoked = false") == [(0,)]
        response = client.post(
            "/v1/auth/refresh", json={"refresh_token": verified["tokens"]["refresh_token"]}
        )
        assert response.status_code == 401

    def test_wrong_current_password_is_rejected(self, client, token):
        response = client.post(
            "/v1/users/me/change-password",
            headers=auth(token),
            json={"current_password": "MauvaisPass1!", "new_password": NEW_PASSWORD},
        )
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "INCORRECT_CURRENT_PASSWORD"

    def test_wrong_current_password_changes_nothing(self, client, verified, token):
        client.post(
            "/v1/users/me/change-password",
            headers=auth(token),
            json={"current_password": "MauvaisPass1!", "new_password": NEW_PASSWORD},
        )
        assert login(client, verified["email"], verified["password"]).status_code == 200

    def test_reusing_the_same_password_is_rejected(self, client, verified, token):
        response = client.post(
            "/v1/users/me/change-password",
            headers=auth(token),
            json={"current_password": verified["password"], "new_password": verified["password"]},
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "SAME_PASSWORD_AS_BEFORE"

    @pytest.mark.parametrize("weak", ["court1!", "minuscule1!", "MAJUSCULE1!", "SansSpecial1"])
    def test_weak_new_password_is_rejected(self, client, verified, token, weak):
        response = client.post(
            "/v1/users/me/change-password",
            headers=auth(token),
            json={"current_password": verified["password"], "new_password": weak},
        )
        assert response.status_code == 422

    def test_google_account_is_rejected(self, client, token, db, verified):
        db("update users set password_hash = null where email = :e", e=verified["email"])
        response = client.post(
            "/v1/users/me/change-password",
            headers=auth(token),
            json={"current_password": verified["password"], "new_password": NEW_PASSWORD},
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "GOOGLE_ONLY_ACCOUNT"

    def test_requires_authentication(self, client, verified):
        response = client.post(
            "/v1/users/me/change-password",
            json={"current_password": verified["password"], "new_password": NEW_PASSWORD},
        )
        assert response.status_code == 401


class TestSetPassword:
    def test_sets_a_password_on_a_google_account(self, client, token, db, verified):
        db("update users set password_hash = null where email = :e", e=verified["email"])
        response = client.post(
            "/v1/users/me/set-password", headers=auth(token), json={"new_password": NEW_PASSWORD}
        )
        assert response.status_code == 200
        assert login(client, verified["email"], NEW_PASSWORD).status_code == 200

    def test_rejected_when_a_password_already_exists(self, client, token):
        response = client.post(
            "/v1/users/me/set-password", headers=auth(token), json={"new_password": NEW_PASSWORD}
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "PASSWORD_ALREADY_SET"

    def test_weak_password_is_rejected(self, client, token, db, verified):
        db("update users set password_hash = null where email = :e", e=verified["email"])
        response = client.post(
            "/v1/users/me/set-password", headers=auth(token), json={"new_password": "faible"}
        )
        assert response.status_code == 422

    def test_requires_authentication(self, client):
        response = client.post("/v1/users/me/set-password", json={"new_password": NEW_PASSWORD})
        assert response.status_code == 401


class TestForgotPassword:
    def test_sends_a_code_to_a_known_address(self, client, verified, mailbox):
        response = client.post("/v1/auth/forgot-password", json={"email": verified["email"]})
        assert response.status_code == 200
        assert mailbox[-1]["kind"] == "reset"

    def test_unknown_address_gets_the_same_answer(self, client, verified, mailbox):
        known = client.post("/v1/auth/forgot-password", json={"email": verified["email"]})
        before = len(mailbox)
        unknown = client.post("/v1/auth/forgot-password", json={"email": "inconnu@example.com"})
        assert unknown.status_code == known.status_code
        assert unknown.json() == known.json()
        assert len(mailbox) == before

    def test_google_account_gets_the_same_answer_without_email(self, client, verified, mailbox, db):
        db("update users set password_hash = null where email = :e", e=verified["email"])
        before = len(mailbox)
        response = client.post("/v1/auth/forgot-password", json={"email": verified["email"]})
        assert response.status_code == 200
        assert len(mailbox) == before

    def test_disabled_account_receives_nothing(self, client, verified, mailbox, db):
        db("update users set is_active = false where email = :e", e=verified["email"])
        before = len(mailbox)
        assert client.post("/v1/auth/forgot-password", json={"email": verified["email"]}).status_code == 200
        assert len(mailbox) == before

    def test_email_is_case_insensitive(self, client, verified, mailbox):
        client.post("/v1/auth/forgot-password", json={"email": verified["email"].upper()})
        assert mailbox[-1]["kind"] == "reset"

    def test_malformed_email_is_rejected(self, client):
        assert client.post("/v1/auth/forgot-password", json={"email": "pas-un-email"}).status_code == 422


class TestResetPassword:
    @pytest.fixture
    def reset_code(self, client, verified, mailbox):
        client.post("/v1/auth/forgot-password", json={"email": verified["email"]})
        return mailbox[-1]["code"]

    def payload(self, verified, code, password=NEW_PASSWORD):
        return {"email": verified["email"], "code": code, "new_password": password}

    def test_resets_the_password(self, client, verified, reset_code):
        response = client.post("/v1/auth/reset-password", json=self.payload(verified, reset_code))
        assert response.status_code == 200
        assert login(client, verified["email"], NEW_PASSWORD).status_code == 200

    def test_old_password_stops_working(self, client, verified, reset_code):
        client.post("/v1/auth/reset-password", json=self.payload(verified, reset_code))
        assert login(client, verified["email"], verified["password"]).status_code == 401

    def test_revokes_every_session(self, client, verified, reset_code, db):
        client.post("/v1/auth/reset-password", json=self.payload(verified, reset_code))
        assert db("select count(*) from refresh_tokens where revoked = false") == [(0,)]

    def test_code_cannot_be_replayed(self, client, verified, reset_code):
        client.post("/v1/auth/reset-password", json=self.payload(verified, reset_code))
        response = client.post(
            "/v1/auth/reset-password", json=self.payload(verified, reset_code, "Encore1!aa")
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "INVALID_OR_EXPIRED_RESET_CODE"

    def test_wrong_code_is_rejected(self, client, verified, reset_code):
        response = client.post("/v1/auth/reset-password", json=self.payload(verified, "000000"))
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "INVALID_RESET_CODE"

    def test_wrong_code_increments_the_attempt_counter(self, client, verified, reset_code, db):
        client.post("/v1/auth/reset-password", json=self.payload(verified, "000000"))
        assert db(
            "select verification_attempts from users where email = :e", e=verified["email"]
        ) == [(1,)]

    def test_locks_after_five_failures(self, client, verified, reset_code):
        for _ in range(5):
            client.post("/v1/auth/reset-password", json=self.payload(verified, "000000"))
        response = client.post("/v1/auth/reset-password", json=self.payload(verified, reset_code))
        assert response.status_code == 429
        assert response.json()["detail"]["code"] == "TOO_MANY_VERIFICATION_ATTEMPTS"

    def test_locked_account_keeps_its_old_password(self, client, verified, reset_code):
        for _ in range(6):
            client.post("/v1/auth/reset-password", json=self.payload(verified, "000000"))
        assert login(client, verified["email"], verified["password"]).status_code == 200

    def test_expired_code_is_rejected(self, client, verified, reset_code, db):
        db("update users set reset_code_expires_at = now() - interval '1 minute'")
        response = client.post("/v1/auth/reset-password", json=self.payload(verified, reset_code))
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "RESET_CODE_EXPIRED"

    def test_reset_without_requesting_a_code_is_rejected(self, client, verified):
        response = client.post("/v1/auth/reset-password", json=self.payload(verified, "123456"))
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "INVALID_OR_EXPIRED_RESET_CODE"

    def test_unknown_email_is_rejected(self, client):
        response = client.post(
            "/v1/auth/reset-password",
            json={"email": "inconnu@example.com", "code": "123456", "new_password": NEW_PASSWORD},
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "INVALID_OR_EXPIRED_RESET_CODE"

    def test_reusing_the_current_password_is_rejected(self, client, verified, reset_code):
        response = client.post(
            "/v1/auth/reset-password", json=self.payload(verified, reset_code, verified["password"])
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "SAME_PASSWORD_AS_BEFORE"

    def test_disabled_account_cannot_reset(self, client, verified, reset_code, db):
        db("update users set is_active = false where email = :e", e=verified["email"])
        response = client.post("/v1/auth/reset-password", json=self.payload(verified, reset_code))
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "ACCOUNT_DISABLED"

    @pytest.mark.parametrize("code", ["12345", "1234567", "abcdef", ""])
    def test_malformed_code_is_rejected(self, client, verified, code):
        response = client.post("/v1/auth/reset-password", json=self.payload(verified, code))
        assert response.status_code == 422

    def test_weak_password_is_rejected(self, client, verified, reset_code):
        response = client.post(
            "/v1/auth/reset-password", json=self.payload(verified, reset_code, "faible")
        )
        assert response.status_code == 422


class TestResetCodeQuota:
    @pytest.fixture
    def fresh_counters(self, verified, db):
        db("update users set code_resend_count = 0, last_code_sent_at = null")
        return verified

    def ask(self, client, email):
        return client.post("/v1/auth/forgot-password", json={"email": email})

    def guess(self, client, email):
        return client.post(
            "/v1/auth/reset-password",
            json={"email": email, "code": "000000", "new_password": NEW_PASSWORD},
        )

    def test_five_codes_per_hour_then_no_more_email(self, client, fresh_counters, mailbox):
        email = fresh_counters["email"]
        before = len(mailbox)
        for _ in range(5):
            assert self.ask(client, email).status_code == 200
        assert len(mailbox) - before == 5

        assert self.ask(client, email).status_code == 200
        assert len(mailbox) - before == 5

    def test_the_answer_stays_generic_once_capped(self, client, fresh_counters, mailbox):
        email = fresh_counters["email"]
        for _ in range(5):
            self.ask(client, email)

        capped = self.ask(client, email)
        unknown = self.ask(client, "inconnu@example.com")
        assert capped.status_code == unknown.status_code == 200
        assert capped.json() == unknown.json()

    def test_guesses_are_capped_cumulatively(self, client, fresh_counters, mailbox):
        email = fresh_counters["email"]
        guesses = 0
        for _ in range(8):
            self.ask(client, email)
            for _ in range(6):
                body = self.guess(client, email).json()
                if body["detail"]["code"] == "TOO_MANY_VERIFICATION_ATTEMPTS":
                    break
                guesses += 1
        assert guesses <= 25

    def test_the_window_expires(self, client, fresh_counters, mailbox, db):
        email = fresh_counters["email"]
        for _ in range(5):
            self.ask(client, email)
        before = len(mailbox)

        db("update users set last_code_sent_at = now() - interval '2 hours'")
        assert self.ask(client, email).status_code == 200
        assert len(mailbox) - before == 1
        assert db("select code_resend_count from users") == [(1,)]

    def test_a_legitimate_user_still_gets_a_working_code(self, client, fresh_counters, mailbox):
        email = fresh_counters["email"]
        self.ask(client, email)
        response = client.post(
            "/v1/auth/reset-password",
            json={"email": email, "code": mailbox[-1]["code"], "new_password": NEW_PASSWORD},
        )
        assert response.status_code == 200


class TestAuthRateLimitKey:
    def test_login_limit_is_not_bypassed_by_attaching_a_token(self, client, verified, rate_limit_on):
        token = verified["tokens"]["access_token"]
        payload = {"email": verified["email"], "password": "MauvaisPass1!"}

        codes = []
        for i in range(12):
            headers = {"Authorization": f"Bearer {token}"} if i % 2 else {}
            codes.append(client.post("/v1/auth/login", json=payload, headers=headers).status_code)

        assert 429 in codes
        assert codes.index(429) == 10

    def test_authenticated_routes_stay_keyed_per_user(self, client, verified, rate_limit_on):
        headers = {"Authorization": f"Bearer {verified['tokens']['access_token']}"}
        codes = [
            client.patch("/v1/users/me", headers=headers, json={"last_name": "K"}).status_code
            for _ in range(32)
        ]
        assert codes.index(429) == 30
