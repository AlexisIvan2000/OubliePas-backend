from io import BytesIO

import pytest
from PIL import Image

from core.exceptions import UnsupportedAvatarType
from services.user_profile.image_sanitizer import MAX_AVATAR_DIMENSION, sanitize_avatar

LATITUDE = (45.0, 30.0, 0.0)
LONGITUDE = (73.0, 34.0, 0.0)


def gps_exif(orientation: int | None = None) -> Image.Exif:
    exif = Image.Exif()
    exif[0x010F] = "OubliePhone"
    exif[0x0110] = "Model X"
    if orientation is not None:
        exif[0x0112] = orientation
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    gps[2] = LATITUDE
    gps[3] = "W"
    gps[4] = LONGITUDE
    return exif


def photo_with_gps(size=(120, 90), orientation: int | None = None, fmt="JPEG") -> bytes:
    image = Image.new("RGB", size, (200, 120, 60))
    buffer = BytesIO()
    image.save(buffer, format=fmt, exif=gps_exif(orientation))
    return buffer.getvalue()


def plain(size=(120, 90), fmt="JPEG", mode="RGB") -> bytes:
    buffer = BytesIO()
    Image.new(mode, size, (10, 20, 30) if mode == "RGB" else None).save(buffer, format=fmt)
    return buffer.getvalue()


class TestExifRemoval:
    def test_the_source_really_carries_gps(self):
        source = photo_with_gps()

        with Image.open(BytesIO(source)) as image:
            gps = image.getexif().get_ifd(0x8825)
            assert gps[1] == "N"
            assert tuple(float(part) for part in gps[2]) == LATITUDE

    def test_gps_is_gone_from_the_result(self):
        cleaned, _, _ = sanitize_avatar(photo_with_gps())

        with Image.open(BytesIO(cleaned)) as image:
            assert dict(image.getexif().get_ifd(0x8825)) == {}

    def test_no_exif_block_remains_at_all(self):
        cleaned, _, _ = sanitize_avatar(photo_with_gps())

        with Image.open(BytesIO(cleaned)) as image:
            assert dict(image.getexif()) == {}

    def test_the_camera_brand_is_gone_too(self):
        source = photo_with_gps()
        assert b"OubliePhone" in source

        cleaned, _, _ = sanitize_avatar(source)

        assert b"OubliePhone" not in cleaned
        assert b"Exif\x00\x00" not in cleaned

    def test_png_metadata_is_dropped(self):
        buffer = BytesIO()
        image = Image.new("RGB", (60, 60), (1, 2, 3))
        from PIL.PngImagePlugin import PngInfo

        info = PngInfo()
        info.add_text("Comment", "SecretLocation")
        image.save(buffer, format="PNG", pnginfo=info)
        source = buffer.getvalue()
        assert b"SecretLocation" in source

        cleaned, _, _ = sanitize_avatar(source)

        assert b"SecretLocation" not in cleaned


class TestOrientation:
    def test_a_portrait_photo_is_uprighted(self):
        cleaned, _, _ = sanitize_avatar(photo_with_gps(size=(120, 60), orientation=6))

        with Image.open(BytesIO(cleaned)) as image:
            assert image.size == (60, 120)

    def test_an_upright_photo_keeps_its_shape(self):
        cleaned, _, _ = sanitize_avatar(photo_with_gps(size=(120, 60), orientation=1))

        with Image.open(BytesIO(cleaned)) as image:
            assert image.size == (120, 60)


class TestFormat:
    def test_jpeg_stays_jpeg(self):
        cleaned, content_type, extension = sanitize_avatar(plain(fmt="JPEG"))

        assert (content_type, extension) == ("image/jpeg", "jpg")
        with Image.open(BytesIO(cleaned)) as image:
            assert image.format == "JPEG"

    def test_png_stays_png(self):
        cleaned, content_type, extension = sanitize_avatar(plain(fmt="PNG"))

        assert (content_type, extension) == ("image/png", "png")
        with Image.open(BytesIO(cleaned)) as image:
            assert image.format == "PNG"

    def test_png_transparency_survives(self):
        buffer = BytesIO()
        Image.new("RGBA", (40, 40), (255, 0, 0, 0)).save(buffer, format="PNG")

        cleaned, _, _ = sanitize_avatar(buffer.getvalue())

        with Image.open(BytesIO(cleaned)) as image:
            assert image.mode == "RGBA"

    def test_a_gif_is_rejected(self):
        buffer = BytesIO()
        Image.new("RGB", (30, 30)).save(buffer, format="GIF")

        with pytest.raises(UnsupportedAvatarType):
            sanitize_avatar(buffer.getvalue())

    def test_a_webp_is_rejected(self):
        buffer = BytesIO()
        Image.new("RGB", (30, 30)).save(buffer, format="WEBP")

        with pytest.raises(UnsupportedAvatarType):
            sanitize_avatar(buffer.getvalue())

    def test_a_truncated_file_is_rejected(self):
        with pytest.raises(UnsupportedAvatarType):
            sanitize_avatar(plain()[:40])

    def test_random_bytes_are_rejected(self):
        with pytest.raises(UnsupportedAvatarType):
            sanitize_avatar(b"\xff\xd8\xff" + b"garbage" * 20)


class TestResize:
    def test_a_large_photo_is_capped(self):
        cleaned, _, _ = sanitize_avatar(plain(size=(3000, 2000)))

        with Image.open(BytesIO(cleaned)) as image:
            assert max(image.size) == MAX_AVATAR_DIMENSION
            assert image.size == (MAX_AVATAR_DIMENSION, 341)

    def test_a_small_photo_is_left_alone(self):
        cleaned, _, _ = sanitize_avatar(plain(size=(64, 64)))

        with Image.open(BytesIO(cleaned)) as image:
            assert image.size == (64, 64)

    def test_a_large_photo_shrinks_a_lot(self):
        source = plain(size=(3000, 2000))
        cleaned, _, _ = sanitize_avatar(source)

        assert len(cleaned) < len(source)
