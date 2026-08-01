from io import BytesIO

import pytest
from PIL import Image

from services.storage import object_storage
from services.user_profile.avatar_service import MAX_AVATAR_BYTES


def encode(fmt: str, size=(120, 90), exif=None) -> bytes:
    buffer = BytesIO()
    image = Image.new("RGB", size, (200, 120, 60))
    if exif is not None:
        image.save(buffer, format=fmt, exif=exif)
    else:
        image.save(buffer, format=fmt)
    return buffer.getvalue()


def gps_photo() -> bytes:
    exif = Image.Exif()
    exif[0x010F] = "OubliePhone"
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    gps[2] = (45.0, 30.0, 0.0)
    gps[3] = "W"
    gps[4] = (73.0, 34.0, 0.0)
    return encode("JPEG", exif=exif)


JPEG = encode("JPEG")
PNG = encode("PNG")
GIF = encode("GIF")

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

        stored = db("SELECT avatar_key FROM users WHERE email = :e", e=verified["email"])[0][0]
        assert stored.startswith("avatars/")
        assert stored.endswith(".jpg")

        body_bytes, content_type = bucket[stored]
        assert content_type == "image/jpeg"
        with Image.open(BytesIO(body_bytes)) as image:
            assert image.format == "JPEG"

    def test_accepts_a_png(self, client, verified, bucket, db):
        response = upload(client, verified["tokens"], PNG, "photo.png", "image/png")

        assert response.status_code == 200
        stored = db("SELECT avatar_key FROM users WHERE email = :e", e=verified["email"])[0][0]
        assert stored.endswith(".png")

        body_bytes, content_type = bucket[stored]
        assert content_type == "image/png"
        with Image.open(BytesIO(body_bytes)) as image:
            assert image.format == "PNG"

    def test_the_stored_key_never_reuses_the_client_filename(self, client, verified, bucket, db):
        upload(client, verified["tokens"], JPEG, "../../etc/passwd.jpg")

        stored = db("SELECT avatar_key FROM users WHERE email = :e", e=verified["email"])[0][0]
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
        oversized = JPEG + b"\x00" * (MAX_AVATAR_BYTES - len(JPEG) + 1)
        response = upload(client, verified["tokens"], oversized)

        assert response.status_code == 413
        assert response.json()["detail"]["code"] == "AVATAR_TOO_LARGE"
        assert bucket == {}

    def test_accepts_a_file_at_exactly_five_megabytes(self, client, verified, bucket):
        limit = JPEG + b"\x00" * (MAX_AVATAR_BYTES - len(JPEG))
        assert len(limit) == MAX_AVATAR_BYTES

        response = upload(client, verified["tokens"], limit)

        assert response.status_code == 200
        assert len(next(iter(bucket.values()))[0]) < MAX_AVATAR_BYTES

    def test_requires_authentication(self, client, bucket):
        response = client.post(AVATAR_URL, files={"file": ("a.jpg", JPEG, "image/jpeg")})

        assert response.status_code == 401
        assert bucket == {}

    def test_replacing_removes_the_previous_object(self, client, verified, bucket, db):
        upload(client, verified["tokens"], JPEG)
        first = db("SELECT avatar_key FROM users WHERE email = :e", e=verified["email"])[0][0]

        upload(client, verified["tokens"], PNG, "photo.png", "image/png")
        second = db("SELECT avatar_key FROM users WHERE email = :e", e=verified["email"])[0][0]

        assert first != second
        assert first not in bucket
        assert second in bucket
        assert len(bucket) == 1


class TestNoGpsReachesTheBucket:
    def test_the_uploaded_photo_really_carries_gps(self):
        with Image.open(BytesIO(gps_photo())) as image:
            assert image.getexif().get_ifd(0x8825)[1] == "N"

    def test_what_lands_in_the_bucket_has_none(self, client, verified, bucket, db):
        response = upload(client, verified["tokens"], gps_photo())
        assert response.status_code == 200

        stored = db("SELECT avatar_key FROM users WHERE email = :e", e=verified["email"])[0][0]
        body_bytes, _ = bucket[stored]

        with Image.open(BytesIO(body_bytes)) as image:
            assert dict(image.getexif()) == {}
            assert dict(image.getexif().get_ifd(0x8825)) == {}

        assert b"OubliePhone" not in body_bytes
        assert b"Exif\x00\x00" not in body_bytes

    def test_an_oversized_photo_is_shrunk_before_storage(self, client, verified, bucket, db):
        source = encode("JPEG", size=(2400, 1600))
        upload(client, verified["tokens"], source)

        stored = db("SELECT avatar_key FROM users WHERE email = :e", e=verified["email"])[0][0]
        body_bytes, _ = bucket[stored]

        with Image.open(BytesIO(body_bytes)) as image:
            assert max(image.size) == 512


class TestDelete:
    def test_removes_the_object_from_the_bucket(self, client, verified, bucket, db):
        upload(client, verified["tokens"], JPEG)
        key = db("SELECT avatar_key FROM users WHERE email = :e", e=verified["email"])[0][0]
        assert key in bucket

        response = client.delete(AVATAR_URL, headers=auth(verified["tokens"]))

        assert response.status_code == 200
        assert response.json()["avatar_url"] is None
        assert db("SELECT avatar_key FROM users WHERE email = :e", e=verified["email"]) == [(None,)]
        assert key not in bucket
        assert bucket == {}

    def test_is_idempotent_when_there_is_no_photo(self, client, verified, bucket):
        response = client.delete(AVATAR_URL, headers=auth(verified["tokens"]))

        assert response.status_code == 200
        assert response.json()["avatar_url"] is None

    def test_requires_authentication(self, client):
        assert client.delete(AVATAR_URL).status_code == 401


class TestHasCustomAvatar:
    def test_false_for_a_fresh_account(self, client, verified, bucket):
        response = client.get("/v1/users/me", headers=auth(verified["tokens"]))

        assert response.json()["has_custom_avatar"] is False

    def test_false_when_only_a_google_photo_is_set(self, client, verified, bucket):
        google = "https://lh3.googleusercontent.com/a/portrait.jpg"
        client.patch("/v1/users/me", headers=auth(verified["tokens"]), json={"avatar_url": google})

        response = client.get("/v1/users/me", headers=auth(verified["tokens"]))

        assert response.json()["has_custom_avatar"] is False

    def test_true_after_an_upload(self, client, verified, bucket):
        assert upload(client, verified["tokens"], JPEG).json()["has_custom_avatar"] is True

    def test_false_again_after_deleting(self, client, verified, bucket):
        upload(client, verified["tokens"], JPEG)
        response = client.delete(AVATAR_URL, headers=auth(verified["tokens"]))

        assert response.json()["has_custom_avatar"] is False


class TestGooglePhotoSurvives:
    def test_uploading_hides_the_google_photo_without_erasing_it(
        self, client, verified, bucket, db
    ):
        google = "https://lh3.googleusercontent.com/a/portrait.jpg"
        client.patch("/v1/users/me", headers=auth(verified["tokens"]), json={"avatar_url": google})

        response = upload(client, verified["tokens"], JPEG)

        assert response.json()["avatar_url"].startswith("https://r2.test/")
        assert db("SELECT avatar_url FROM users WHERE email = :e", e=verified["email"]) == [
            (google,)
        ]

    def test_deleting_the_upload_brings_the_google_photo_back(self, client, verified, bucket):
        google = "https://lh3.googleusercontent.com/a/portrait.jpg"
        client.patch("/v1/users/me", headers=auth(verified["tokens"]), json={"avatar_url": google})
        upload(client, verified["tokens"], JPEG)

        response = client.delete(AVATAR_URL, headers=auth(verified["tokens"]))

        assert response.status_code == 200
        assert response.json()["avatar_url"] == google
        assert response.json()["has_custom_avatar"] is False
        assert bucket == {}

    def test_deleting_without_an_upload_leaves_the_google_photo_alone(
        self, client, verified, bucket
    ):
        google = "https://lh3.googleusercontent.com/a/portrait.jpg"
        client.patch("/v1/users/me", headers=auth(verified["tokens"]), json={"avatar_url": google})
        bucket["sentinel"] = (b"", "")

        response = client.delete(AVATAR_URL, headers=auth(verified["tokens"]))

        assert response.status_code == 200
        assert response.json()["avatar_url"] == google
        assert "sentinel" in bucket

    def test_an_account_without_google_falls_back_to_nothing(self, client, verified, bucket):
        upload(client, verified["tokens"], JPEG)

        response = client.delete(AVATAR_URL, headers=auth(verified["tokens"]))

        assert response.json()["avatar_url"] is None

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

    def test_it_falls_back_to_the_google_photo(self, client, verified, bucket, monkeypatch):
        google = "https://lh3.googleusercontent.com/a/portrait.jpg"
        client.patch("/v1/users/me", headers=auth(verified["tokens"]), json={"avatar_url": google})
        upload(client, verified["tokens"], JPEG)
        monkeypatch.setattr(object_storage, "is_configured", lambda: False)

        response = client.get("/v1/users/me", headers=auth(verified["tokens"]))

        assert response.json()["avatar_url"] == google
