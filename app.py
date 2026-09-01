import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from api.middlewares.request_context import RequestContextMiddleware
from api.middlewares.security_headers import SecurityHeadersMiddleware
from api.middlewares.server_error import (
    INTERNAL_ERROR,
    ServerErrorEnvelopeMiddleware,
)
from api.v1.router import api_router
from core.config import CORS_ORIGIN_REGEX, CORS_ORIGINS, DEBUG, TRUSTED_PROXY_COUNT
from core.database import dispose_engine
from core.exceptions import AppException
from core.migrations import run_migrations
from core.observability import ContextFilter
from core.rate_limit import client_ip, limiter

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    # stderr et non stdout : hors terminal, stdout est bufferisé par blocs et
    # la trace attend 8 Ko pendant que la plateforme n'affiche rien.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(request_id)s %(caller)s] %(name)s %(message)s",
        stream=sys.stderr,
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(ContextFilter())


configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await run_migrations()
    yield
    await dispose_engine()


def docs_urls(debug: bool) -> dict:
    # Un seul client, déjà au courant : publier le schéma n'apporte rien.
    if debug:
        return {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}
    return {"docs_url": None, "redoc_url": None, "openapi_url": None}


app = FastAPI(
    title="OubliePas API",
    version="0.1.0",
    lifespan=lifespan,
    **docs_urls(DEBUG),
)

app.state.limiter = limiter
# L'ordre est inversé : le dernier ajouté enveloppe les précédents.
app.add_middleware(ServerErrorEnvelopeMiddleware)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

# Le plus à l'extérieur : le contexte doit exister avant toute journalisation.
app.add_middleware(RequestContextMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Le client rafraîchit seul : journaliser chaque 401 noierait les incidents.
QUIET_CODES = {"INVALID_ACCESS_TOKEN"}


def _severity(exc: AppException) -> int:
    if exc.code in QUIET_CODES:
        return logging.DEBUG
    if exc.status_code >= 500:
        return logging.ERROR
    if exc.status_code == 429:
        return logging.WARNING
    return logging.INFO


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    logger.log(
        _severity(exc),
        "%s on %s %s -> %s",
        exc.code,
        request.method,
        request.url.path,
        exc.status_code,
    )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.to_dict()})


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    logger.warning("rate limit hit on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=429,
        content={
            "detail": {
                "code": "RATE_LIMIT_EXCEEDED",
                "message": "Too many requests. Please try again later",
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    logger.exception("unhandled error above the envelope on %s %s", request.method, request.url.path)
    # Ne couvre que ce qui échoue au-dessus du middleware : sans en-têtes,
    # mais avec la même enveloppe que le reste de l'API.
    return JSONResponse(status_code=500, content=INTERNAL_ERROR)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    # Les noms de champs seulement : les valeurs portent mots de passe et codes.
    logger.info(
        "validation refused on %s %s: %s",
        request.method,
        request.url.path,
        ", ".join(sorted({".".join(str(p) for p in e["loc"][1:]) for e in exc.errors()})),
    )
    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "code": "VALIDATION_ERROR",
                "message": "Invalid request payload",
                "errors": [
                    {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
                    for e in exc.errors()
                ],
            }
        },
    )


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok"}


if DEBUG:

    @app.get("/debug/client-ip", tags=["debug"])
    async def debug_client_ip(request: Request):
        return {
            "resolved_key": client_ip(request),
            "socket_peer": request.client.host if request.client else None,
            "x_forwarded_for": request.headers.get("x-forwarded-for"),
            "trusted_proxy_count": TRUSTED_PROXY_COUNT,
        }


app.include_router(api_router)
