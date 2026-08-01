import time

import pytest
from jose import jwt

from core.config import GOOGLE_CLIENT_ID
from services.authentication.google_auth import GoogleTokenClient

pytestmark = pytest.mark.integration

SUB = "108127410394857392847"
OTHER_SUB = "990000000000000000001"


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def id_token(email, sub=SUB, **overrides):
    claims = {
        "iss": "https://accounts.google.com",
        "aud": GOOGLE_CLIENT_ID,
        "sub": sub,
        "email": email,
        "email_verified": True,
        "given_name": "Alexis",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        **overrides,
    }
    return jwt.encode(claims, "peu-importe-la-cle", algorithm="HS256")


@pytest.fixture
def google(monkeypatch):
    box = {"payload": None}

    async def fake_exchange(self, *, code, code_verifier):
        return box["payload"]

    monkeypatch.setattr(GoogleTokenClient, "exchange", fake_exchange)

    def responds(email, sub=SUB):
        box["payload"] = {"id_token": id_token(email, sub)}

    box["responds"] = responds
    return box


def sign_in(client):
    return client.post("/v1/auth/google", json={"code": "c", "code_verifier": "v" * 43})


def change_email(client, tokens, new_email, password, mailbox):
    started = client.post(
        "/v1/users/me/change-email",
        headers=auth(tokens["access_token"]),
        json={"new_email": new_email, "password": password},
    )
    assert started.status_code == 200, started.text
    return client.post(
        "/v1/users/me/confirm-email-change",
        headers=auth(tokens["access_token"]),
        json={"code": mailbox[-1]["code"]},
    )


class TestGoogleLinkedToAPasswordAccount:
    def test_signing_in_with_google_links_the_existing_account(
        self, client, verified, google, db
    ):
        google["responds"](verified["email"])

        response = sign_in(client)

        assert response.status_code == 200
        row = db(
            "SELECT google_sub, password_hash IS NOT NULL FROM users WHERE email = :e",
            e=verified["email"],
        )
        assert row == [(SUB, True)]

    def test_changing_the_email_does_not_break_google_sign_in(
        self, client, verified, google, mailbox, db
    ):
        google["responds"](verified["email"])
        assert sign_in(client).status_code == 200

        changed = change_email(
            client, verified["tokens"], "nouvelle@example.com", verified["password"], mailbox
        )
        assert changed.status_code == 200

        google["responds"](verified["email"])
        again = sign_in(client)

        assert again.status_code == 200
        me = client.get("/v1/auth/me", headers=auth(again.json()["access_token"])).json()
        assert me["email"] == "nouvelle@example.com"
        assert db("SELECT id FROM users") != []
        assert len(db("SELECT id FROM users")) == 1

    def test_google_still_finds_it_by_sub_not_by_email(
        self, client, verified, google, mailbox, db
    ):
        google["responds"](verified["email"])
        sign_in(client)
        change_email(
            client, verified["tokens"], "nouvelle@example.com", verified["password"], mailbox
        )

        google["responds"](verified["email"])
        sign_in(client)

        assert len(db("SELECT id FROM users")) == 1
        assert db("SELECT email FROM users") == [("nouvelle@example.com",)]

    def test_the_password_still_works_after_linking(self, client, verified, google):
        google["responds"](verified["email"])
        sign_in(client)

        response = client.post(
            "/v1/auth/login",
            json={"email": verified["email"], "password": verified["password"]},
        )

        assert response.status_code == 200


class TestASecondGoogleAccount:
    def test_it_cannot_steal_the_link(self, client, verified, google, mailbox, db):
        google["responds"](verified["email"])
        sign_in(client)
        assert db("SELECT google_sub FROM users WHERE email = :e", e=verified["email"]) == [(SUB,)]

        change_email(
            client, verified["tokens"], "seconde@example.com", verified["password"], mailbox
        )

        google["responds"]("seconde@example.com", sub=OTHER_SUB)
        response = sign_in(client)

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "GOOGLE_ACCOUNT_ALREADY_LINKED"
        assert db("SELECT google_sub FROM users") == [(SUB,)]
        assert len(db("SELECT id FROM users")) == 1

    def test_the_original_google_account_keeps_working(
        self, client, verified, google, mailbox, db
    ):
        google["responds"](verified["email"])
        sign_in(client)
        change_email(
            client, verified["tokens"], "seconde@example.com", verified["password"], mailbox
        )

        google["responds"]("seconde@example.com", sub=OTHER_SUB)
        assert sign_in(client).status_code == 409

        google["responds"](verified["email"], sub=SUB)
        response = sign_in(client)

        assert response.status_code == 200
        assert len(db("SELECT id FROM users")) == 1
        me = client.get("/v1/auth/me", headers=auth(response.json()["access_token"])).json()
        assert me["email"] == "seconde@example.com"

    def test_signing_in_twice_with_the_same_account_is_fine(
        self, client, verified, google, mailbox, db
    ):
        google["responds"](verified["email"])
        sign_in(client)
        change_email(
            client, verified["tokens"], "seconde@example.com", verified["password"], mailbox
        )

        google["responds"]("seconde@example.com", sub=SUB)
        response = sign_in(client)

        assert response.status_code == 200
        assert len(db("SELECT id FROM users")) == 1

    def test_an_unlinked_account_still_links_normally(self, client, verified, google, db):
        google["responds"](verified["email"], sub=OTHER_SUB)

        response = sign_in(client)

        assert response.status_code == 200
        assert db("SELECT google_sub FROM users WHERE email = :e", e=verified["email"]) == [
            (OTHER_SUB,)
        ]
