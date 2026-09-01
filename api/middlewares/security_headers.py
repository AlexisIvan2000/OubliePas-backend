from starlette.middleware.base import BaseHTTPMiddleware

from core.config import DEBUG

DOCS_PATHS = ("/docs", "/redoc", "/docs/oauth2-redirect")

API_CSP = "; ".join(
    [
        "default-src 'none'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "form-action 'none'",
    ]
)

DOCS_CSP = "; ".join(
    [
        "default-src 'none'",
        # Swagger s'amorce par un script en ligne : sans cette tolérance, le
        # bundle se charge mais rien ne l'initialise. Uniquement en DEBUG.
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
        # ReDoc tire ses polices de Google.
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com",
        "img-src 'self' https://fastapi.tiangolo.com data:",
        "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
    ]
)

BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
}

HSTS = "max-age=63072000; includeSubDomains"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)

        for header, value in BASE_HEADERS.items():
            response.headers.setdefault(header, value)

        is_docs = request.url.path in DOCS_PATHS
        response.headers.setdefault(
            "Content-Security-Policy", DOCS_CSP if is_docs else API_CSP
        )

        if not DEBUG:
            response.headers.setdefault("Strict-Transport-Security", HSTS)

        return response
