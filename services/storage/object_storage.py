import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError
from starlette.concurrency import run_in_threadpool

from core import config as settings
from core.exceptions import AvatarUploadFailed, StorageUnavailable

SIGNATURE_VERSION = "s3v4"
REGION = "auto"
EXTERNAL_URL_PREFIXES = ("http://", "https://")


def is_configured() -> bool:
    return bool(settings.API_S3 and settings.S3_ACCESS_KEY_ID and settings.S3_SECRET_ACCESS_KEY)


def is_stored_key(value: str | None) -> bool:
    return bool(value) and not value.startswith(EXTERNAL_URL_PREFIXES)


class ObjectStorage:
    def __init__(self):
        self._client = None

    def _build_client(self):
        if not is_configured():
            raise StorageUnavailable()
        return boto3.client(
            "s3",
            endpoint_url=settings.API_S3,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
            region_name=REGION,
            config=Config(signature_version=SIGNATURE_VERSION),
        )

    @property
    def client(self):
        if self._client is None:
            self._client = self._build_client()
        return self._client

    async def put(self, key: str, body: bytes, content_type: str) -> None:
        try:
            await run_in_threadpool(
                self.client.put_object,
                Bucket=settings.S3_BUCKET_NAME,
                Key=key,
                Body=body,
                ContentType=content_type,
            )
        except (BotoCoreError, ClientError) as exc:
            raise AvatarUploadFailed() from exc

    async def delete(self, key: str) -> bool:
        try:
            await run_in_threadpool(
                self.client.delete_object,
                Bucket=settings.S3_BUCKET_NAME,
                Key=key,
            )
        except (BotoCoreError, ClientError, StorageUnavailable):
            return False
        return True

    def presign(self, key: str) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.S3_BUCKET_NAME, "Key": key},
            ExpiresIn=settings.AVATAR_URL_EXPIRE_SECONDS,
        )


storage = ObjectStorage()


def public_avatar_url(value: str | None) -> str | None:
    if not value:
        return None
    if not is_stored_key(value):
        return value
    if not is_configured():
        return None
    try:
        return storage.presign(value)
    except (BotoCoreError, ClientError, StorageUnavailable):
        return None
