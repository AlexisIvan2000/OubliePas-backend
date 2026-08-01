from io import BytesIO
from PIL import Image, ImageOps, UnidentifiedImageError
from core.exceptions import UnsupportedAvatarType

MAX_AVATAR_DIMENSION = 512
MAX_DECODED_PIXELS = 40_000_000
JPEG_QUALITY = 88

SUPPORTED_FORMATS = {
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
}

TRANSPARENT_MODES = ("RGBA", "LA", "P")

Image.MAX_IMAGE_PIXELS = MAX_DECODED_PIXELS


def _flatten(image: Image.Image, target_format: str) -> Image.Image:
    if target_format == "PNG":
        return image.convert("RGBA") if image.mode in TRANSPARENT_MODES else image.convert("RGB")
    return image.convert("RGB")


def sanitize_avatar(data: bytes) -> tuple[bytes, str, str]:
    try:
        with Image.open(BytesIO(data)) as probe:
            image_format = probe.format
            if image_format not in SUPPORTED_FORMATS:
                raise UnsupportedAvatarType()
            image = ImageOps.exif_transpose(probe)
            image.load()
    except UnsupportedAvatarType:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        raise UnsupportedAvatarType() from exc

    content_type, extension = SUPPORTED_FORMATS[image_format]

    image.thumbnail((MAX_AVATAR_DIMENSION, MAX_AVATAR_DIMENSION), Image.LANCZOS)
    image = _flatten(image, image_format)

    buffer = BytesIO()
    if image_format == "JPEG":
        image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    else:
        image.save(buffer, format="PNG", optimize=True)

    return buffer.getvalue(), content_type, extension
