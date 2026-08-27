from datetime import date
from io import BytesIO

import pytest
from PIL import Image

from services.storage import object_storage

DELETE_URL = "/v1/users/me/delete"
AVATAR_URL = "/v1/users/me/avatar"


def encode() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (80, 80), (10, 90, 200)).save(buffer, format="JPEG")
    return buffer.getvalue()


JPEG = encode()


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture
def bucket(monkeypatch):
    store: dict[str, tuple[bytes, str]] = {}

    async def put(key, body, content_type):
        store[key] = (body, content_type)

    async def delete(key):
        store.pop(key, None)
        return True

    monkeypatch.setattr(object_storage.storage, "put", put)
    monkeypatch.setattr(object_storage.storage, "delete", delete)
    monkeypatch.setattr(object_storage.storage, "presign", lambda key: f"https://r2.test/{key}")
    monkeypatch.setattr(object_storage, "is_configured", lambda: True)
    return store


def confirm(client, tokens, **payload):
    return client.post(DELETE_URL, headers=auth(tokens), json=payload)


def strip_password(db, email):
    db("UPDATE users SET password_hash = NULL, google_sub = :g WHERE email = :e", e=email, g="g-1")


def exists(db, email):
    return db("SELECT id FROM users WHERE email = :e", e=email) != []


class TestAccountWithPassword:
    def test_the_right_password_deletes(self, client, verified, db):
        response = confirm(client, verified["tokens"], password=verified["password"])

        assert response.status_code == 200
        assert not exists(db, verified["email"])

    def test_a_wrong_password_is_refused(self, client, verified, db):
        response = confirm(client, verified["tokens"], password="MauvaisMotDePasse1!")

        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "INCORRECT_PASSWORD"
        assert exists(db, verified["email"])

    def test_a_missing_password_is_refused(self, client, verified, db):
        response = confirm(client, verified["tokens"])

        assert response.status_code == 401
        assert exists(db, verified["email"])

    def test_the_email_alone_is_not_enough(self, client, verified, db):
        response = confirm(client, verified["tokens"], confirmation=verified["email"])

        assert response.status_code == 401
        assert exists(db, verified["email"])

    def test_the_email_is_never_a_substitute(self, client, verified, db):
        response = confirm(
            client, verified["tokens"], confirmation=verified["email"], password=""
        )

        assert response.status_code == 401
        assert exists(db, verified["email"])

    def test_it_requires_authentication(self, client):
        assert client.post(DELETE_URL, json={"password": "x"}).status_code == 401


class TestGoogleAccount:
    def test_the_email_works(self, client, verified, db):
        strip_password(db, verified["email"])

        response = confirm(client, verified["tokens"], confirmation=verified["email"])

        assert response.status_code == 200
        assert not exists(db, verified["email"])

    def test_the_email_is_case_insensitive(self, client, verified, db):
        strip_password(db, verified["email"])

        response = confirm(client, verified["tokens"], confirmation=verified["email"].upper())

        assert response.status_code == 200
        assert not exists(db, verified["email"])

    def test_surrounding_spaces_are_tolerated(self, client, verified, db):
        strip_password(db, verified["email"])

        response = confirm(client, verified["tokens"], confirmation=f"  {verified['email']}  ")

        assert response.status_code == 200
        assert not exists(db, verified["email"])

    def test_the_word_supprimer_is_refused(self, client, verified, db):
        strip_password(db, verified["email"])

        response = confirm(client, verified["tokens"], confirmation="SUPPRIMER")

        assert response.status_code == 400
        assert exists(db, verified["email"])

    def test_the_word_delete_is_refused(self, client, verified, db):
        strip_password(db, verified["email"])

        response = confirm(client, verified["tokens"], confirmation="DELETE")

        assert response.status_code == 400
        assert exists(db, verified["email"])

    def test_a_random_word_is_refused(self, client, verified, db):
        strip_password(db, verified["email"])

        response = confirm(client, verified["tokens"], confirmation="oui")

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "INVALID_DELETION_CONFIRMATION"
        assert exists(db, verified["email"])

    def test_someone_elses_email_is_refused(self, client, verified, db):
        strip_password(db, verified["email"])

        response = confirm(client, verified["tokens"], confirmation="autre@example.com")

        assert response.status_code == 400
        assert exists(db, verified["email"])

    def test_an_empty_body_is_refused(self, client, verified, db):
        strip_password(db, verified["email"])

        response = confirm(client, verified["tokens"])

        assert response.status_code == 400
        assert exists(db, verified["email"])


class TestHasPasswordFlag:
    def test_true_for_a_classic_account(self, client, verified):
        response = client.get("/v1/users/me", headers=auth(verified["tokens"]))

        assert response.json()["has_password"] is True

    def test_false_for_a_google_account(self, client, verified, db):
        strip_password(db, verified["email"])

        response = client.get("/v1/users/me", headers=auth(verified["tokens"]))

        assert response.json()["has_password"] is False


class TestWhatGetsRemoved:
    def test_the_photo_leaves_the_bucket(self, client, verified, bucket, db):
        client.post(AVATAR_URL, headers=auth(verified["tokens"]), files={"file": ("p.jpg", JPEG, "image/jpeg")})
        key = db("SELECT avatar_key FROM users WHERE email = :e", e=verified["email"])[0][0]
        assert key in bucket

        response = confirm(client, verified["tokens"], password=verified["password"])

        assert response.status_code == 200
        assert bucket == {}

    def test_a_google_photo_needs_no_bucket_call(self, client, verified, bucket, db):
        google = "https://lh3.googleusercontent.com/a/portrait.jpg"
        client.patch("/v1/users/me", headers=auth(verified["tokens"]), json={"avatar_url": google})
        bucket["sentinel"] = (b"", "")

        assert confirm(client, verified["tokens"], password=verified["password"]).status_code == 200
        assert "sentinel" in bucket

    def test_the_commitments_go_with_it(self, client, verified, db):
        created = client.post(
            "/v1/commitments",
            headers=auth(verified["tokens"]),
            json={
                "title": "Netflix",
                "type": "subscription",
                "category": "entertainment",
                "amount": "18.99",
                "frequency": "monthly",
                "starts_on": date.today().isoformat(),
            },
        )
        assert created.status_code == 201
        user_id = db("SELECT id FROM users WHERE email = :e", e=verified["email"])[0][0]
        assert db("SELECT id FROM commitments WHERE user_id = :u", u=user_id) != []

        assert confirm(client, verified["tokens"], password=verified["password"]).status_code == 200

        assert db("SELECT id FROM commitments WHERE user_id = :u", u=user_id) == []
        assert db("SELECT id FROM commitment_occurrences WHERE user_id = :u", u=user_id) == []

    def test_the_refresh_tokens_go_with_it(self, client, verified, db):
        user_id = db("SELECT id FROM users WHERE email = :e", e=verified["email"])[0][0]
        assert db("SELECT id FROM refresh_tokens WHERE user_id = :u", u=user_id) != []

        assert confirm(client, verified["tokens"], password=verified["password"]).status_code == 200

        assert db("SELECT id FROM refresh_tokens WHERE user_id = :u", u=user_id) == []


class TestAfterwards:
    def test_the_access_token_no_longer_works(self, client, verified):
        confirm(client, verified["tokens"], password=verified["password"])

        response = client.get("/v1/users/me", headers=auth(verified["tokens"]))

        assert response.status_code == 401

    def test_the_refresh_token_no_longer_works(self, client, verified):
        confirm(client, verified["tokens"], password=verified["password"])

        response = client.post("/v1/auth/refresh")

        assert response.status_code == 401

    def test_signing_in_again_is_impossible(self, client, verified):
        confirm(client, verified["tokens"], password=verified["password"])

        response = client.post(
            "/v1/auth/login",
            json={"email": verified["email"], "password": verified["password"]},
        )

        assert response.status_code == 401

    def test_the_email_can_be_registered_again(self, client, verified, mailbox):
        confirm(client, verified["tokens"], password=verified["password"])

        response = client.post(
            "/v1/auth/register",
            json={"first_name": "Alexis", "email": verified["email"], "password": "MotDePasse1!"},
        )

        assert response.status_code == 201


class TestPasswordLengthIsNotAnOracle:
    def test_a_password_beyond_the_ceiling_answers_like_a_wrong_one(self, client, verified):
        # Avant, la longueur sortait en 422 "certains champs sont invalides" :
        # la reponse disait a l'appelant ce qui clochait, et ce n'etait pas le
        # mot de passe.
        response = client.post(
            "/v1/users/me/delete",
            headers={"Authorization": f"Bearer {verified['tokens']['access_token']}"},
            json={"password": "a" * 200},
        )

        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "INCORRECT_PASSWORD"

    def test_a_wrong_password_of_normal_length_answers_the_same(self, client, verified):
        response = client.post(
            "/v1/users/me/delete",
            headers={"Authorization": f"Bearer {verified['tokens']['access_token']}"},
            json={"password": "MauvaisMotDePasse1!"},
        )

        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "INCORRECT_PASSWORD"

    def test_the_account_is_still_there(self, client, verified, db):
        client.post(
            "/v1/users/me/delete",
            headers={"Authorization": f"Bearer {verified['tokens']['access_token']}"},
            json={"password": "a" * 200},
        )

        assert db("select count(*) from users where email = :e", e=verified["email"]) == [(1,)]
