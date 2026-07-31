import pytest

pytestmark = pytest.mark.integration


class TestRefresh:
    def test_returns_a_new_pair(self, client, verified):
        old = verified["tokens"]["refresh_token"]
        response = client.post("/v1/auth/refresh", json={"refresh_token": old})
        assert response.status_code == 200
        assert response.json()["refresh_token"] != old

    def test_revokes_the_consumed_token(self, client, verified, db):
        client.post("/v1/auth/refresh", json={"refresh_token": verified["tokens"]["refresh_token"]})
        rows = db("select revoked from refresh_tokens order by created_at")
        assert rows[0] == (True,)
        assert rows[-1] == (False,)

    def test_new_token_can_be_used_again(self, client, verified):
        first = client.post("/v1/auth/refresh", json={"refresh_token": verified["tokens"]["refresh_token"]})
        second = client.post("/v1/auth/refresh", json={"refresh_token": first.json()["refresh_token"]})
        assert second.status_code == 200

    def test_replaying_a_consumed_token_is_detected(self, client, verified):
        old = verified["tokens"]["refresh_token"]
        client.post("/v1/auth/refresh", json={"refresh_token": old})
        response = client.post("/v1/auth/refresh", json={"refresh_token": old})
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "TOKEN_REUSE_DETECTED"

    def test_replay_revokes_the_whole_family(self, client, verified):
        old = verified["tokens"]["refresh_token"]
        fresh = client.post("/v1/auth/refresh", json={"refresh_token": old}).json()["refresh_token"]
        client.post("/v1/auth/refresh", json={"refresh_token": old})

        response = client.post("/v1/auth/refresh", json={"refresh_token": fresh})
        assert response.status_code == 401

    def test_replay_revocation_is_committed(self, client, verified, db):
        old = verified["tokens"]["refresh_token"]
        client.post("/v1/auth/refresh", json={"refresh_token": old})
        client.post("/v1/auth/refresh", json={"refresh_token": old})
        assert db("select count(*) from refresh_tokens where revoked = false") == [(0,)]

    def test_access_token_is_not_accepted_as_refresh(self, client, verified):
        response = client.post(
            "/v1/auth/refresh", json={"refresh_token": verified["tokens"]["access_token"]}
        )
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "INVALID_REFRESH_TOKEN"

    @pytest.mark.parametrize("token", ["", "garbage", "a.b.c"])
    def test_malformed_token_is_rejected(self, client, token):
        response = client.post("/v1/auth/refresh", json={"refresh_token": token})
        assert response.status_code == 401

    def test_unknown_but_well_formed_token_is_rejected(self, client, verified):
        from core.security import Security

        orphan = Security.create_refresh_token("00000000-0000-0000-0000-000000000000")
        response = client.post("/v1/auth/refresh", json={"refresh_token": orphan})
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "INVALID_REFRESH_TOKEN"

    def test_expired_stored_token_is_rejected(self, client, verified, db):
        db("update refresh_tokens set expires_at = now() - interval '1 day'")
        response = client.post(
            "/v1/auth/refresh", json={"refresh_token": verified["tokens"]["refresh_token"]}
        )
        assert response.status_code == 401

    def test_disabled_account_cannot_refresh(self, client, verified, db):
        db("update users set is_active = false where email = :e", e=verified["email"])
        response = client.post(
            "/v1/auth/refresh", json={"refresh_token": verified["tokens"]["refresh_token"]}
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "ACCOUNT_DISABLED"

    def test_disabling_an_account_kills_its_sessions(self, client, verified, db):
        db("update users set is_active = false where email = :e", e=verified["email"])
        client.post("/v1/auth/refresh", json={"refresh_token": verified["tokens"]["refresh_token"]})
        assert db("select count(*) from refresh_tokens where revoked = false") == [(0,)]


class TestLogout:
    def test_returns_200(self, client, verified):
        response = client.post(
            "/v1/auth/logout", json={"refresh_token": verified["tokens"]["refresh_token"]}
        )
        assert response.status_code == 200

    def test_revokes_the_token(self, client, verified, db):
        client.post("/v1/auth/logout", json={"refresh_token": verified["tokens"]["refresh_token"]})
        assert db("select count(*) from refresh_tokens where revoked = false") == [(0,)]

    def test_token_cannot_be_refreshed_afterwards(self, client, verified):
        client.post("/v1/auth/logout", json={"refresh_token": verified["tokens"]["refresh_token"]})
        response = client.post(
            "/v1/auth/refresh", json={"refresh_token": verified["tokens"]["refresh_token"]}
        )
        assert response.status_code == 401

    def test_logging_out_twice_is_harmless(self, client, verified):
        payload = {"refresh_token": verified["tokens"]["refresh_token"]}
        client.post("/v1/auth/logout", json=payload)
        assert client.post("/v1/auth/logout", json=payload).status_code == 200

    def test_unknown_token_is_accepted_silently(self, client):
        response = client.post("/v1/auth/logout", json={"refresh_token": "garbage"})
        assert response.status_code == 200
