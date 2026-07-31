from fastapi import Request
from slowapi import Limiter

from core.config import REDIS_URL, TRUSTED_PROXY_COUNT
from core.security import Security

FALLBACK_IP = "127.0.0.1"


def client_ip(request: Request) -> str:
    if TRUSTED_PROXY_COUNT > 0:
        forwarded = request.headers.get("x-forwarded-for", "")
        chain = [part.strip() for part in forwarded.split(",") if part.strip()]
        if chain:
            return chain[max(0, len(chain) - TRUSTED_PROXY_COUNT)]
    if request.client and request.client.host:
        return request.client.host
    return FALLBACK_IP


def ip_key(request: Request) -> str:
    return client_ip(request)


def get_user_id_from_jwt(request: Request) -> str:
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        payload = Security.decode_token(auth_header[7:])
        if payload and payload.get("sub"):
            return payload["sub"]
    return client_ip(request)


limiter = Limiter(
    key_func=get_user_id_from_jwt,
    storage_uri=REDIS_URL or "memory://",
    in_memory_fallback_enabled=bool(REDIS_URL),
)
