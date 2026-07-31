import time
from urllib.parse import urlencode

import httpx
from jose import jwt

from core.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI
from core.exceptions import (
    AccountDisabled,
    DisposableEmailNotAllowed,
    GoogleAuthFailed,
    GoogleAuthUnavailable,
    GoogleEmailNotVerified,
)
from core.validators import is_disposable_email, normalize_email
from repositories.auth_repository import AuthRepository
from repositories.refresh_token_repository import RefreshTokenRepository
from services.authentication.tokens import issue_tokens

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPES = "openid email profile"
ACCEPTED_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
CLOCK_SKEW_SECONDS = 60
EXCHANGE_TIMEOUT_SECONDS = 10


def is_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI)


def authorize_url(*, state: str, code_challenge: str) -> str:
    query = urlencode(
        {
            "client_id": GOOGLE_CLIENT_ID,
            "redirect_uri": GOOGLE_REDIRECT_URI,
            "response_type": "code",
            "scope": GOOGLE_SCOPES,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "access_type": "online",
            "prompt": "select_account",
        }
    )
    return f"{GOOGLE_AUTH_ENDPOINT}?{query}"


class GoogleTokenClient:
    async def exchange(self, *, code: str, code_verifier: str) -> dict:
        async with httpx.AsyncClient(timeout=EXCHANGE_TIMEOUT_SECONDS) as client:
            response = await client.post(
                GOOGLE_TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": GOOGLE_REDIRECT_URI,
                    "grant_type": "authorization_code",
                    "code_verifier": code_verifier,
                },
            )

        if response.status_code != 200:
            raise GoogleAuthFailed()

        return response.json()


class GoogleAuth:
    def __init__(
        self,
        auth_repo: AuthRepository,
        refresh_token_repo: RefreshTokenRepository,
        token_client: GoogleTokenClient,
    ):
        self.repo = auth_repo
        self.rt_repo = refresh_token_repo
        self.token_client = token_client

    def _claims(self, payload: dict) -> dict:
        id_token = payload.get("id_token")
        if not id_token:
            raise GoogleAuthFailed()

        try:
            claims = jwt.get_unverified_claims(id_token)
        except Exception as error:
            raise GoogleAuthFailed() from error

        if claims.get("aud") != GOOGLE_CLIENT_ID:
            raise GoogleAuthFailed()

        if claims.get("iss") not in ACCEPTED_ISSUERS:
            raise GoogleAuthFailed()

        expires_at = claims.get("exp")
        if not isinstance(expires_at, (int, float)):
            raise GoogleAuthFailed()
        if expires_at + CLOCK_SKEW_SECONDS < time.time():
            raise GoogleAuthFailed()

        if not claims.get("sub") or not claims.get("email"):
            raise GoogleAuthFailed()

        if claims.get("email_verified") not in (True, "true"):
            raise GoogleEmailNotVerified()

        return claims

    async def _link_or_create(self, claims: dict):
        google_sub = claims["sub"]
        email = normalize_email(claims["email"])

        db_user = await self.repo.get_user_by_google_sub(google_sub)
        if db_user:
            if not db_user.is_active:
                raise AccountDisabled()
            return db_user

        db_user = await self.repo.get_user_by_email(email)
        if db_user:
            if not db_user.is_active:
                raise AccountDisabled()

            updates = {"google_sub": google_sub}
            if not db_user.is_verified:
                updates.update(
                    {
                        "is_verified": True,
                        "verification_code_hash": None,
                        "verification_code_expires_at": None,
                        "verification_attempts": 0,
                    }
                )
            if not db_user.avatar_url and claims.get("picture"):
                updates["avatar_url"] = claims["picture"]

            return await self.repo.update_user(str(db_user.id), updates)

        if is_disposable_email(email):
            raise DisposableEmailNotAllowed()

        return await self.repo.create_user(
            {
                "first_name": claims.get("given_name") or email.split("@")[0],
                "last_name": claims.get("family_name"),
                "email": email,
                "password_hash": None,
                "is_verified": True,
                "google_sub": google_sub,
                "avatar_url": claims.get("picture"),
            }
        )

    async def sign_in(self, *, code: str, code_verifier: str) -> dict:
        if not is_configured():
            raise GoogleAuthUnavailable()

        payload = await self.token_client.exchange(code=code, code_verifier=code_verifier)
        claims = self._claims(payload)
        db_user = await self._link_or_create(claims)

        return await issue_tokens(self.rt_repo, db_user)
