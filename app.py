from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

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
from core.rate_limit import client_ip, limiter


@asynccontextmanager
async def lifespan(app: FastAPI):
    await run_migrations()
    yield
    await dispose_engine()


def docs_urls(debug: bool) -> dict:
    # Le schema decrit toute la surface de l'API pour un service qui n'a qu'un
    # client, deja au courant. Rien ne compense de le publier.
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
# L'ordre est inverse : le dernier ajoute enveloppe les precedents. Le
# rattrapage des 500 doit donc etre ajoute en premier pour finir au plus
# profond, sous les en-tetes de securite et sous le CORS.
app.add_middleware(ServerErrorEnvelopeMiddleware)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.to_dict()})


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
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
    # Dernier recours : ne couvre que ce qui echoue au-dessus du middleware,
    # dans le CORS ou les en-tetes eux-memes. Sans en-tetes, mais au moins
    # avec la meme enveloppe que le reste de l'API.
    return JSONResponse(status_code=500, content=INTERNAL_ERROR)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
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
