import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

INTERNAL_ERROR = {
    "detail": {
        "code": "INTERNAL_ERROR",
        "message": "An unexpected error occurred",
    }
}


class ServerErrorEnvelopeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        try:
            return await call_next(request)
        except Exception:
            logger.exception(
                "unhandled error on %s %s", request.method, request.url.path
            )
            return JSONResponse(status_code=500, content=INTERNAL_ERROR)
