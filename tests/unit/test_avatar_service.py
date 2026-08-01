import asyncio

import pytest

from core.exceptions import AvatarTooLarge, UnsupportedAvatarType
from services.storage.object_storage import is_stored_key
from services.user_profile.avatar_service import (
    MAX_AVATAR_BYTES,
    detect_image,
    read_within_limit,
)

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
GIF = b"GIF89a" + b"0" * 32


class FakeUpload:
    def __init__(self, data: bytes, content_type: str | None = None):
        self.data = data
        self.content_type = content_type
        self.offset = 0

    async def read(self, size: int) -> bytes:
        chunk = self.data[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


def run(coro):
    return asyncio.run(coro)


class TestDetectImage:
    def test_recognises_jpeg(self):
        assert detect_image(JPEG) == ("image/jpeg", "jpg")

    def test_recognises_png(self):
        assert detect_image(PNG) == ("image/png", "png")

    def test_rejects_gif(self):
        with pytest.raises(UnsupportedAvatarType):
            detect_image(GIF)

    def test_rejects_html_disguised_as_image(self):
        with pytest.raises(UnsupportedAvatarType):
            detect_image(b"<html><script>alert(1)</script></html>")

    def test_rejects_empty(self):
        with pytest.raises(UnsupportedAvatarType):
            detect_image(b"")

    def test_rejects_magic_not_at_the_start(self):
        with pytest.raises(UnsupportedAvatarType):
            detect_image(b"AA" + PNG)


class TestReadWithinLimit:
    def test_reads_a_small_file(self):
        assert run(read_within_limit(FakeUpload(JPEG))) == JPEG

    def test_reads_a_file_spanning_several_chunks(self):
        payload = JPEG + b"x" * (200 * 1024)
        assert run(read_within_limit(FakeUpload(payload))) == payload

    def test_accepts_exactly_the_limit(self):
        payload = JPEG + b"x" * (MAX_AVATAR_BYTES - len(JPEG))
        assert len(run(read_within_limit(FakeUpload(payload)))) == MAX_AVATAR_BYTES

    def test_rejects_one_byte_over_the_limit(self):
        payload = JPEG + b"x" * (MAX_AVATAR_BYTES - len(JPEG) + 1)
        with pytest.raises(AvatarTooLarge):
            run(read_within_limit(FakeUpload(payload)))

    def test_rejects_an_empty_upload(self):
        with pytest.raises(UnsupportedAvatarType):
            run(read_within_limit(FakeUpload(b"")))


class TestIsStoredKey:
    def test_none_is_not_a_key(self):
        assert is_stored_key(None) is False

    def test_empty_is_not_a_key(self):
        assert is_stored_key("") is False

    def test_google_url_is_not_a_key(self):
        assert is_stored_key("https://lh3.googleusercontent.com/a/portrait.jpg") is False

    def test_plain_http_is_not_a_key(self):
        assert is_stored_key("http://cdn.example.com/a.png") is False

    def test_object_key_is_a_key(self):
        assert is_stored_key("avatars/42/abcdef.jpg") is True
