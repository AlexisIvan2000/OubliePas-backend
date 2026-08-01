import pytest

from services.storage import object_storage
from services.user_profile.avatar_service import MAX_AVATAR_BYTES

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
GIF = b"GIF89a" + b"0" * 64

AVATAR_URL = "/v1/users/me/avatar"


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

    def presign(key):
        return f"https://r2.test/{key}?signature=abc"

    monkeypatch.setattr(object_storage.storage, "put", put)
    monkeypatch.setattr(object_storage.storage, "delete", delete)
    monkeypatch.setattr(object_storage.storage, "presign", presign)
    monkeypatch.setattr(object_storage, "is_configured", lambda: True)
    return store


def upload(client, tokens, data, filename="photo.jpg", content_type="image/jpeg"):
    return client.post(
        AVATAR_URL,
        headers=auth(tokens),
        files={"file": (filename, data, content_type)},
    )


class TestUpload:
    def test_accepts_a_jpeg(self, client, verified, bucket, db):
        response = upload(client, verified["tokens"], JPEG)

        assert response.status_code == 200
        body = response.json()
        assert body["avatar_url"].startswith("https://r2.test/avatars/")
        assert body["avatar_url"].endswith(".jpg?signature=abc")

        stored = db("SELECT avatar_url FROM users WHERE email = :e", e=verified["email"])[0][0]
        assert stored.startswith("avatars/")
        assert stored.endswith(".jpg")
        assert bucket[stored] == (JPEG, "image/jpeg")

    def test_accepts_a_png(self, client, verified, bucket, db):
        response = upload(client, verified["tokens"], PNG, "photo.png", "image/png")

        assert response.status_code == 200
        stored = db("SELECT avatar_url FROM users WHERE email = :e", e=verified["email"])[0][0]
        assert stored.endswith(".png")
        assert bucket[stored] == (PNG, "image/png")

    def test_the_stored_key_never_reuses_the_client_filename(self, client, verified, bucket, db):
        upload(client, verified["tokens"], JPEG, "../../etc/passwd.jpg")

        stored = db("SELECT avatar_url FROM users WHERE email = :e", e=verified["email"])[0][0]
        assert ".." not in stored
        assert "passwd" not in stored

    def test_rejects_a_gif(self, client, verified, bucket):
        response = upload(client, verified["tokens"], GIF, "photo.gif", "image/gif")

        assert response.status_code == 415
        assert response.json()["detail"]["code"] == "UNSUPPORTED_AVATAR_TYPE"
        assert bucket == {}

    def test_rejects_a_file_lying_about_its_type(self, client, verified, bucket):
        response = upload(client, verified["tokens"], b"<script>alert(1)</script>")

        assert response.status_code == 415
        assert bucket == {}

    def test_rejects_an_empty_file(self, client, verified, bucket):
        response = upload(client, verified["tokens"], b"")

        assert response.status_code == 415
        assert bucket == {}

    def test_rejects_a_file_over_five_megabytes(self, client, verified, bucket):
        oversized = JPEG + b"x" * (MAX_AVATAR_BYTES - len(JPEG) + 1)
        response = upload(client, verified["tokens"], oversized)

        assert response.status_code == 413
        assert response.json()["detail"]["code"] == "AVATAR_TOO_LARGE"
        assert bucket == {}

    def test_accepts_a_file_at_exactly_five_megabytes(self, client, verified, bucket):
        limit = JPEG + b"x" * (MAX_AVATAR_BYTES - len(JPEG))
        response = upload(client, verified["tokens"], limit)

        assert response.status_code == 200
        assert len(next(iter(bucket.values()))[0]) == MAX_AVATAR_BYTES

    def test_requires_authentication(self, client, bucket):
        response = client.post(AVATAR_URL, files={"file": ("a.jpg", JPEG, "image/jpeg")})

        assert response.status_code == 401
        assert bucket == {}

    def test_replacing_removes_the_previous_object(self, client, verified, bucket, db):
        upload(client, verified["tokens"], JPEG)
        first = db("SELECT avatar_url FROM users WHERE email = :e", e=verified["email"])[0][0]

        upload(client, verified["tokens"], PNG, "photo.png", "image/png")
        second = db("SELECT avatar_url FROM users WHERE email = :e", e=verified["email"])[0][0]

        assert first != second
        assert first not in bucket
        assert second in bucket
        assert len(bucket) == 1


class TestDelete:
    def test_removes_the_object_and_clears_the_column(self, client, verified, bucket, db):
        upload(client, verified["tokens"], JPEG)
        key = db("SELECT avatar_url FROM users WHERE email = :e", e=verified["email"])[0][0]

        response = client.delete(AVATAR_URL, headers=auth(verified["tokens"]))

        assert response.status_code == 200
        assert response.json()["avatar_url"] is None
        assert db("SELECT avatar_url FROM users WHERE email = :e", e=verified["email"]) == [(None,)]
        assert key not in bucket

    def test_is_idempotent_when_there_is_no_photo(self, client, verified, bucket):
        response = client.delete(AVATAR_URL, headers=auth(verified["tokens"]))

        assert response.status_code == 200
        assert response.json()["avatar_url"] is None

    def test_requires_authentication(self, client):
        assert client.delete(AVATAR_URL).status_code == 401


class TestExternalUrls:
    def test_a_google_photo_is_returned_untouched(self, client, verified, bucket):
        google = "https://lh3.googleusercontent.com/a/portrait.jpg"
        client.patch("/v1/users/me", headers=auth(verified["tokens"]), json={"avatar_url": google})

        response = client.get("/v1/users/me", headers=auth(verified["tokens"]))

        assert response.json()["avatar_url"] == google

    def test_deleting_a_google_photo_touches_no_object(self, client, verified, bucket):
        google = "https://lh3.googleusercontent.com/a/portrait.jpg"
        client.patch("/v1/users/me", headers=auth(verified["tokens"]), json={"avatar_url": google})
        bucket["sentinel"] = (b"", "")

        response = client.delete(AVATAR_URL, headers=auth(verified["tokens"]))

        assert response.status_code == 200
        assert response.json()["avatar_url"] is None
        assert "sentinel" in bucket

    def test_auth_me_signs_the_key_the_same_way(self, client, verified, bucket, db):
        upload(client, verified["tokens"], JPEG)

        from_users = client.get("/v1/users/me", headers=auth(verified["tokens"])).json()
        from_auth = client.get("/v1/auth/me", headers=auth(verified["tokens"])).json()

        assert from_auth["avatar_url"] == from_users["avatar_url"]
        assert from_auth["avatar_url"].startswith("https://r2.test/")


class TestStorageNotConfigured:
    def test_the_key_is_hidden_rather_than_leaked(self, client, verified, bucket, monkeypatch, db):
        upload(client, verified["tokens"], JPEG)
        monkeypatch.setattr(object_storage, "is_configured", lambda: False)

        response = client.get("/v1/users/me", headers=auth(verified["tokens"]))

        assert response.status_code == 200
        assert response.json()["avatar_url"] is None
