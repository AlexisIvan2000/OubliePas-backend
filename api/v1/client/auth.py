from fastapi import APIRouter, Request, Response, status

from api.dependencies import CurrentUserDep, EmailPasswordAuthDep, GoogleAuthDep, UserProfileDep
from api.responses import user_response
from core.cookies import clear_refresh_cookie, issue_session, read_refresh_cookie
from core.exceptions import GoogleAuthUnavailable, InvalidRefreshToken
from core.rate_limit import READ_LIMIT, ip_key, limiter
from models.schemas.auth_schema import (
    GoogleAuthRequest,
    GoogleStartRequest,
    GoogleStartResponse,
    MessageResponse,
    ResendVerificationRequest,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
    VerifyEmailRequest,
)
from services.authentication.google_auth import authorize_url, is_configured
from models.schemas.user_schema import ForgotPassword, ResetPassword

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/hour", key_func=ip_key)
async def register(request: Request, payload: UserCreate, service: EmailPasswordAuthDep):
    return await service.register_user(payload)


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute", key_func=ip_key)
async def login(
    request: Request, response: Response, payload: UserLogin, service: EmailPasswordAuthDep
):
    return issue_session(response, await service.login_user(payload))


@router.post("/google/start", response_model=GoogleStartResponse)
@limiter.limit("20/minute", key_func=ip_key)
async def google_start(request: Request, payload: GoogleStartRequest):
    if not is_configured():
        raise GoogleAuthUnavailable()

    return GoogleStartResponse(
        authorization_url=authorize_url(
            state=payload.state, code_challenge=payload.code_challenge
        )
    )


@router.post("/google", response_model=TokenResponse)
@limiter.limit("10/minute", key_func=ip_key)
async def google_sign_in(
    request: Request, response: Response, payload: GoogleAuthRequest, service: GoogleAuthDep
):
    tokens = await service.sign_in(code=payload.code, code_verifier=payload.code_verifier)
    return issue_session(response, tokens)


@router.post("/verify-email", response_model=TokenResponse)
@limiter.limit("10/minute", key_func=ip_key)
async def verify_email(
    request: Request,
    response: Response,
    payload: VerifyEmailRequest,
    service: EmailPasswordAuthDep,
):
    tokens = await service.verify_email(payload.email, payload.code)
    return issue_session(response, tokens)


@router.post("/resend-verification", response_model=MessageResponse)
@limiter.limit("5/hour", key_func=ip_key)
async def resend_verification(
    request: Request, payload: ResendVerificationRequest, service: EmailPasswordAuthDep
):
    return await service.resend_verification_email(payload.email)


@router.post("/forgot-password", response_model=MessageResponse)
@limiter.limit("5/hour", key_func=ip_key)
async def forgot_password(request: Request, payload: ForgotPassword, service: UserProfileDep):
    return await service.forgot_password(payload)


@router.post("/reset-password", response_model=MessageResponse)
@limiter.limit("10/minute", key_func=ip_key)
async def reset_password(request: Request, payload: ResetPassword, service: UserProfileDep):
    return await service.reset_password(payload)


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("30/minute", key_func=ip_key)
async def refresh(request: Request, response: Response, service: EmailPasswordAuthDep):
    token = read_refresh_cookie(request)
    if not token:
        raise InvalidRefreshToken()
    return issue_session(response, await service.refresh_access_token(token))


@router.post("/logout", response_model=MessageResponse)
@limiter.limit("30/minute")
async def logout(request: Request, response: Response, service: EmailPasswordAuthDep):
    token = read_refresh_cookie(request)
    clear_refresh_cookie(response)
    if not token:
        return {"message": "User logged out successfully"}
    return await service.logout_user(token)


@router.get("/me", response_model=UserResponse)
@limiter.limit(READ_LIMIT)
async def me(request: Request, user: CurrentUserDep):
    return user_response(user)
