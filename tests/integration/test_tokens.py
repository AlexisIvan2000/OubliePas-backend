import pytest

from core.config import REFRESH_COOKIE_NAME, REFRESH_COOKIE_PATH

pytestmark = pytest.mark.integration


def current_token(client):
    return client.cookies.get(REFRESH_COOKIE_NAME)


def use_token(client, token):
    client.cookies.clear()
    if token is not None:
        client.cookies.set(REFRESH_COOKIE_NAME, token, path=REFRESH_COOKIE_PATH)


def refresh(client):
    return client.post("/v1/auth/refresh")


class TestCookie:
    def test_login_sets_an_httponly_cookie(self, client, verified):
        header = verified["set_cookie"]

        assert f"{REFRESH_COOKIE_NAME}=" in header
        assert "HttpOnly" in header
        assert f"Path={REFRESH_COOKIE_PATH}" in header
        assert "SameSite=lax" in header

    def test_the_body_never_carries_the_refresh_token(self, client, verified):
        assert "refresh_token" not in verified["tokens"]
        assert set(verified["tokens"]) == {"access_token", "token_type", "role"}

    def test_the_cookie_is_scoped_away_from_the_rest_of_the_api(self, client, verified):
        assert client.cookies.get(REFRESH_COOKIE_NAME) is not None

        response = client.get("/v1/users/me", headers={"Authorization": "Bearer x"})

        assert response.status_code == 401


class TestRefresh:
    def test_returns_a_new_pair(self, client, verified):
        old = current_token(client)

        response = refresh(client)

        assert response.status_code == 200
        assert "refresh_token" not in response.json()
        assert current_token(client) != old

    def test_revokes_the_consumed_token(self, client, verified, db):
        refresh(client)

        rows = db("select revoked from refresh_tokens order by created_at")
        assert rows[0] == (True,)
        assert rows[-1] == (False,)

    def test_new_token_can_be_used_again(self, client, verified):
        assert refresh(client).status_code == 200
        assert refresh(client).status_code == 200

    def test_replaying_a_consumed_token_is_detected(self, client, verified):
        old = current_token(client)
        refresh(client)

        use_token(client, old)
        response = refresh(client)

        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "TOKEN_REUSE_DETECTED"

    def test_replay_revokes_the_whole_family(self, client, verified):
        old = current_token(client)
        refresh(client)
        fresh = current_token(client)

        use_token(client, old)
        refresh(client)

        use_token(client, fresh)
        assert refresh(client).status_code == 401

    def test_replay_revocation_is_committed(self, client, verified, db):
        old = current_token(client)
        refresh(client)
        use_token(client, old)
        refresh(client)

        assert db("select count(*) from refresh_tokens where revoked = false") == [(0,)]

    def test_access_token_is_not_accepted_as_refresh(self, client, verified):
        use_token(client, verified["tokens"]["access_token"])

        response = refresh(client)

        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "INVALID_REFRESH_TOKEN"

    @pytest.mark.parametrize("token", ["garbage", "a.b.c"])
    def test_malformed_token_is_rejected(self, client, token):
        use_token(client, token)

        assert refresh(client).status_code == 401

    def test_a_missing_cookie_is_rejected(self, client):
        use_token(client, None)

        response = refresh(client)

        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "INVALID_REFRESH_TOKEN"

    def test_unknown_but_well_formed_token_is_rejected(self, client, verified):
        from core.security import Security

        use_token(client, Security.create_refresh_token("00000000-0000-0000-0000-000000000000"))

        response = refresh(client)

        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "INVALID_REFRESH_TOKEN"

    def test_expired_stored_token_is_rejected(self, client, verified, db):
        db("update refresh_tokens set expires_at = now() - interval '1 day'")

        assert refresh(client).status_code == 401

    def test_disabled_account_cannot_refresh(self, client, verified, db):
        db("update users set is_active = false where email = :e", e=verified["email"])

        response = refresh(client)

        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "ACCOUNT_DISABLED"

    def test_disabling_an_account_kills_its_sessions(self, client, verified, db):
        db("update users set is_active = false where email = :e", e=verified["email"])

        refresh(client)

        assert db("select count(*) from refresh_tokens where revoked = false") == [(0,)]


class TestLogout:
    def test_returns_200(self, client, verified):
        assert client.post("/v1/auth/logout").status_code == 200

    def test_revokes_the_token(self, client, verified, db):
        client.post("/v1/auth/logout")

        assert db("select count(*) from refresh_tokens where revoked = false") == [(0,)]

    def test_clears_the_cookie(self, client, verified):
        response = client.post("/v1/auth/logout")

        assert f'{REFRESH_COOKIE_NAME}=""' in response.headers.get("set-cookie", "")
        assert not client.cookies.get(REFRESH_COOKIE_NAME)

    def test_token_cannot_be_refreshed_afterwards(self, client, verified):
        stolen = current_token(client)
        client.post("/v1/auth/logout")

        use_token(client, stolen)
        assert refresh(client).status_code == 401

    def test_logging_out_twice_is_harmless(self, client, verified):
        assert client.post("/v1/auth/logout").status_code == 200
        assert client.post("/v1/auth/logout").status_code == 200

    def test_unknown_token_is_accepted_silently(self, client):
        use_token(client, "garbage")

        assert client.post("/v1/auth/logout").status_code == 200
