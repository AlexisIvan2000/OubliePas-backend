import uuid

from starlette.concurrency import run_in_threadpool

from core import config as settings
from core.exceptions import AvatarTooLarge, UnsupportedAvatarType, UserNotFound
from repositories.auth_repository import AuthRepository
from services.storage.object_storage import ObjectStorage, is_stored_key
from services.user_profile.image_sanitizer import sanitize_avatar

MAX_AVATAR_BYTES = 9 * 1024 * 1024
CHUNK_SIZE = 64 * 1024

AVATAR_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
)

ALLOWED_CONTENT_TYPES = frozenset({"image/jpeg", "image/jpg", "image/png"})


def detect_image(data: bytes) -> tuple[str, str]:
    for magic, content_type, extension in AVATAR_SIGNATURES:
        if data.startswith(magic):
            return content_type, extension
    raise UnsupportedAvatarType()


async def read_within_limit(upload) -> bytes:
    chunks: list[bytes] = []
    total = 0

    while True:
        chunk = await upload.read(CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_AVATAR_BYTES:
            raise AvatarTooLarge()
        chunks.append(chunk)

    if total == 0:
        raise UnsupportedAvatarType()

    return b"".join(chunks)


class AvatarService:
    def __init__(self, auth_repo: AuthRepository, storage: ObjectStorage):
        self.repo = auth_repo
        self.storage = storage

    async def _get_user(self, user_id: str):
        db_user = await self.repo.get_user_by_id(user_id)
        if not db_user:
            raise UserNotFound()
        return db_user

    async def replace(self, user_id: str, upload):
        db_user = await self._get_user(user_id)

        declared = (upload.content_type or "").split(";")[0].strip().lower()
        if declared and declared not in ALLOWED_CONTENT_TYPES:
            raise UnsupportedAvatarType()

        raw = await read_within_limit(upload)
        detect_image(raw)
        data, content_type, extension = await run_in_threadpool(sanitize_avatar, raw)

        key = f"{settings.FOLDER_NAME}/{user_id}/{uuid.uuid4().hex}.{extension}"
        await self.storage.put(key, data, content_type)

        previous = db_user.avatar_key
        updated = await self.repo.update_user(user_id, {"avatar_key": key})

        if is_stored_key(previous) and previous != key:
            await self.storage.delete(previous)

        return updated

    async def remove(self, user_id: str):
        db_user = await self._get_user(user_id)
        previous = db_user.avatar_key

        if previous is None:
            return db_user

        updated = await self.repo.update_user(user_id, {"avatar_key": None})

        if is_stored_key(previous):
            await self.storage.delete(previous)

        return updated
