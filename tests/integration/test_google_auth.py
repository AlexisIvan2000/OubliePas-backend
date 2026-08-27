import time
from urllib.parse import parse_qs, urlparse

import pytest
from jose import jwt

from core.config import GOOGLE_CLIENT_ID, REFRESH_COOKIE_NAME, REFRESH_COOKIE_PATH
from services.authentication.google_auth import GoogleTokenClient

pytestmark = pytest.mark.integration

SUB = "108127410394857392847"
EMAIL = "camille@gmail.com"


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def id_token(**overrides):
    claims = {
        "iss": "https://accounts.google.com",
        "aud": GOOGLE_CLIENT_ID,
        "sub": SUB,
        "email": EMAIL,
        "email_verified": True,
        "given_name": "Camille",
        "family_name": "Laurent",
        "picture": "https://lh3.googleusercontent.com/a/portrait.jpg",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        **overrides,
    }
    return jwt.encode(claims, "peu-importe-la-cle", algorithm="HS256")


@pytest.fixture
def google(monkeypatch):
    box = {"payload": {"id_token": id_token()}, "calls": []}

    async def fake_exchange(self, *, code, code_verifier):
        box["calls"].append({"code": code, "code_verifier": code_verifier})
        if isinstance(box["payload"], Exception):
            raise box["payload"]
        return box["payload"]

    monkeypatch.setattr(GoogleTokenClient, "exchange", fake_exchange)

    def responds(**overrides):
        box["payload"] = {"id_token": id_token(**overrides)}

    box["responds"] = responds
    return box


def sign_in(client, code="code-google", verifier="v" * 43):
    return client.post("/v1/auth/google", json={"code": code, "code_verifier": verifier})


class TestAuthorizationUrl:
    def test_builds_a_google_url_with_pkce(self, client):
        response = client.post(
            "/v1/auth/google/start",
            json={"state": "s" * 32, "code_challenge": "c" * 43},
        )
        assert response.status_code == 200

        url = urlparse(response.json()["authorization_url"])
        params = parse_qs(url.query)

        assert url.netloc == "accounts.google.com"
        assert url.path == "/o/oauth2/v2/auth"
        assert params["response_type"] == ["code"]
        assert params["code_challenge_method"] == ["S256"]
        assert params["code_challenge"] == ["c" * 43]
        assert params["state"] == ["s" * 32]
        assert params["client_id"] == [GOOGLE_CLIENT_ID]
        assert set(params["scope"][0].split()) == {"openid", "email", "profile"}

    @pytest.mark.parametrize(
        "payload",
        [
            {"state": "court", "code_challenge": "c" * 43},
            {"state": "s" * 32, "code_challenge": "court"},
            {"state": "s" * 32},
            {"code_challenge": "c" * 43},
        ],
    )
    def test_rejects_a_malformed_request(self, client, payload):
        assert client.post("/v1/auth/google/start", json=payload).status_code == 422


class TestFirstSignIn:
    def test_creates_a_verified_account(self, client, google, db):
        response = sign_in(client)
        assert response.status_code == 200, response.text

        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["role"] == "user"

        rows = db(
            "SELECT email, is_verified, password_hash, google_sub FROM users WHERE email = :e",
            e=EMAIL,
        )
        assert rows == [(EMAIL, True, None, SUB)]

    def test_carries_the_google_profile(self, client, google):
        token = sign_in(client).json()["access_token"]
        me = client.get("/v1/users/me", headers=auth(token)).json()

        assert me["first_name"] == "Camille"
        assert me["last_name"] == "Laurent"
        assert me["email"] == EMAIL
        assert me["is_verified"] is True
        assert me["avatar_url"] == "https://lh3.googleusercontent.com/a/portrait.jpg"
        assert me["currency"] == "CAD"

    def test_forwards_the_code_and_verifier(self, client, google):
        sign_in(client, code="abc123", verifier="x" * 50)
        assert google["calls"] == [{"code": "abc123", "code_verifier": "x" * 50}]

    def test_normalizes_the_google_email(self, client, google, db):
        google["responds"](email="CAMILLE@GMAIL.COM")
        assert sign_in(client).status_code == 200
        assert db("SELECT count(*) FROM users WHERE email = :e", e=EMAIL) == [(1,)]

    def test_falls_back_on_the_local_part_without_a_given_name(self, client, google):
        google["responds"](given_name=None, family_name=None)
        token = sign_in(client).json()["access_token"]
        me = client.get("/v1/users/me", headers=auth(token)).json()
        assert me["first_name"] == "camille"
        assert me["last_name"] is None


class TestReturningUser:
    def test_reuses_the_same_account(self, client, google, db):
        first = sign_in(client).json()
        second = sign_in(client).json()

        assert db("SELECT count(*) FROM users") == [(1,)]
        assert first["access_token"] and second["access_token"]
        assert db("SELECT count(*) FROM refresh_tokens") == [(2,)]

    def test_both_sessions_stay_usable(self, client, google):
        sign_in(client)
        first = client.cookies.get(REFRESH_COOKIE_NAME)
        sign_in(client)
        second = client.cookies.get(REFRESH_COOKIE_NAME)
        assert first != second

        for token in (first, second):
            client.cookies.clear()
            client.cookies.set(REFRESH_COOKIE_NAME, token, path=REFRESH_COOKIE_PATH)
            assert client.post("/v1/auth/refresh").status_code == 200

    def test_follows_the_subject_when_the_google_email_changes(self, client, google, db):
        sign_in(client)
        google["responds"](email="camille.laurent@gmail.com")
        assert sign_in(client).status_code == 200

        assert db("SELECT count(*) FROM users") == [(1,)]
        assert db("SELECT email FROM users") == [(EMAIL,)]


class TestLinkingAnExistingAccount:
    def test_links_a_verified_password_account(self, client, google, verified, db):
        google["responds"](email=verified["email"])
        assert sign_in(client).status_code == 200

        rows = db(
            "SELECT google_sub, password_hash IS NOT NULL FROM users WHERE email = :e",
            e=verified["email"],
        )
        assert rows == [(SUB, True)]

    def test_the_password_still_works_after_linking(self, client, google, verified):
        google["responds"](email=verified["email"])
        sign_in(client)

        response = client.post(
            "/v1/auth/login",
            json={"email": verified["email"], "password": verified["password"]},
        )
        assert response.status_code == 200

    def test_verifies_an_account_that_never_confirmed_its_email(self, client, google, registered, db):
        google["responds"](email=registered["email"])
        assert sign_in(client).status_code == 200

        assert db(
            "SELECT is_verified, verification_code_hash FROM users WHERE email = :e",
            e=registered["email"],
        ) == [(True, None)]

    def test_keeps_an_avatar_the_user_already_chose(self, client, google, verified, db):
        # La photo est posee en base, comme le serveur la pose : le profil ne
        # laisse plus le client ecrire ce champ.
        db(
            "update users set avatar_url = 'https://cdn.x.com/a.png' where email = :e",
            e=verified["email"],
        )

        google["responds"](email=verified["email"])
        sign_in(client)

        assert db("SELECT avatar_url FROM users WHERE email = :e", e=verified["email"]) == [
            ("https://cdn.x.com/a.png",)
        ]


class TestRejections:
    def test_refuses_an_unverified_google_email(self, client, google, db):
        google["responds"](email_verified=False)
        response = sign_in(client)

        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "GOOGLE_EMAIL_NOT_VERIFIED"
        assert db("SELECT count(*) FROM users") == [(0,)]

    def test_refuses_a_token_minted_for_another_application(self, client, google):
        google["responds"](aud="123-autre.apps.googleusercontent.com")
        response = sign_in(client)

        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "GOOGLE_AUTH_FAILED"

    def test_refuses_a_foreign_issuer(self, client, google):
        google["responds"](iss="https://evil.example.com")
        assert sign_in(client).status_code == 401

    def test_refuses_an_expired_token(self, client, google):
        google["responds"](exp=int(time.time()) - 600)
        assert sign_in(client).status_code == 401

    def test_refuses_a_response_without_an_id_token(self, client, google):
        google["payload"] = {"access_token": "seulement-ca"}
        assert sign_in(client).status_code == 401

    def test_refuses_a_malformed_id_token(self, client, google):
        google["payload"] = {"id_token": "pas-un-jwt"}
        assert sign_in(client).status_code == 401

    def test_refuses_a_disabled_account(self, client, google, verified, db):
        db("UPDATE users SET is_active = false WHERE email = :e", e=verified["email"])
        google["responds"](email=verified["email"])

        response = sign_in(client)
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "ACCOUNT_DISABLED"

    @pytest.mark.parametrize(
        "payload",
        [
            {"code": "", "code_verifier": "v" * 43},
            {"code": "abc", "code_verifier": "trop-court"},
            {"code": "abc"},
            {"code_verifier": "v" * 43},
        ],
    )
    def test_rejects_a_malformed_request(self, client, google, payload):
        assert client.post("/v1/auth/google", json=payload).status_code == 422


class TestDisposableAddresses:
    def test_refuses_to_create_an_account_on_a_throwaway_domain(self, client, google, db):
        google["responds"](email="jetable@yopmail.com", sub="999")
        response = sign_in(client)

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "DISPOSABLE_EMAIL_NOT_ALLOWED"
        assert db("SELECT count(*) FROM users") == [(0,)]

    def test_an_existing_account_on_such_a_domain_can_still_sign_in(self, client, google, db):
        google["responds"]()
        assert sign_in(client).status_code == 200
        db("UPDATE users SET email = 'ancien@yopmail.com' WHERE google_sub = :s", s=SUB)

        assert sign_in(client).status_code == 200
        assert db("SELECT count(*) FROM users") == [(1,)]

    def test_google_never_sends_a_verification_code(self, client, google, mailbox):
        assert sign_in(client).status_code == 200
        assert mailbox == []


class TestPasswordlessAccount:
    def test_cannot_be_signed_into_with_a_password(self, client, google):
        sign_in(client)
        response = client.post(
            "/v1/auth/login", json={"email": EMAIL, "password": "NimporteQuoi1!"}
        )

        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "INVALID_CREDENTIALS"

    def test_can_set_a_password_afterwards(self, client, google):
        token = sign_in(client).json()["access_token"]

        response = client.post(
            "/v1/users/me/set-password", headers=auth(token), json={"new_password": "MotDePasse1!"}
        )
        assert response.status_code == 200

        login = client.post("/v1/auth/login", json={"email": EMAIL, "password": "MotDePasse1!"})
        assert login.status_code == 200

    def test_change_password_reports_the_google_only_case(self, client, google):
        token = sign_in(client).json()["access_token"]

        response = client.post(
            "/v1/users/me/change-password",
            headers=auth(token),
            json={"current_password": "peu-importe", "new_password": "MotDePasse1!"},
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "GOOGLE_ONLY_ACCOUNT"
