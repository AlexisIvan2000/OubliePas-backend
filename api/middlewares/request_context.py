import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware

from core.observability import bind, new_request_id
from core.rate_limit import client_ip
from core.security import Security

logger = logging.getLogger("api.access")

HEADER = "X-Request-ID"

# Frappée toutes les quinze secondes par la plateforme.
QUIET_PATHS = {"/health"}


def caller_of(request) -> str:
    header = request.headers.get("authorization", "")
    if header.startswith("Bearer "):
        payload = Security.decode_token(header[7:])
        if payload and payload.get("sub"):
            return payload["sub"]
    return f"ip:{client_ip(request)}"


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request_id = new_request_id(request.headers.get(HEADER))
        bind(request_id, caller_of(request))

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # La trace complète revient au middleware d'enveloppe.
            logger.warning(
                "%s %s -> exception in %.0fms",
                request.method,
                request.url.path,
                (time.perf_counter() - started) * 1000,
            )
            raise

        if request.url.path not in QUIET_PATHS:
            logger.info(
                "%s %s -> %s in %.0fms",
                request.method,
                request.url.path,
                response.status_code,
                (time.perf_counter() - started) * 1000,
            )

        response.headers[HEADER] = request_id
        return response
