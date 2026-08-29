from fastapi import APIRouter, Request, status

from api.dependencies import CurrentUserDep, PushRepoDep
from core.config import VAPID_PUBLIC_KEY, push_configured
from core.rate_limit import READ_LIMIT, limiter
from models.schemas.auth_schema import MessageResponse
from models.schemas.push_schema import (
    PushKeyResponse,
    PushSubscriptionIn,
    PushSubscriptionResponse,
    PushUnsubscribe,
)

router = APIRouter(prefix="/push", tags=["push"])


@router.get("/key", response_model=PushKeyResponse)
@limiter.limit(READ_LIMIT)
async def public_key(request: Request, user: CurrentUserDep):
    # Rendue nulle plutot qu'absente quand le serveur n'a pas de paire : le
    # client distingue ainsi « le push n'est pas installe ici » d'une panne.
    return PushKeyResponse(public_key=VAPID_PUBLIC_KEY if push_configured() else None)


@router.post(
    "/subscriptions",
    response_model=PushSubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("30/hour")
async def subscribe(
    request: Request, payload: PushSubscriptionIn, user: CurrentUserDep, repo: PushRepoDep
):
    saved = await repo.save(
        str(user.id),
        endpoint=payload.endpoint,
        p256dh=payload.p256dh,
        auth=payload.auth,
        user_agent=payload.user_agent,
    )
    return PushSubscriptionResponse(endpoint=saved.endpoint, enabled=user.reminder_push_enabled)


@router.delete("/subscriptions", response_model=MessageResponse)
@limiter.limit("30/hour")
async def unsubscribe(
    request: Request, payload: PushUnsubscribe, user: CurrentUserDep, repo: PushRepoDep
):
    # Silencieuse quand rien ne correspond : desabonner deux fois est le cas
    # normal, pas une erreur a signaler a quelqu'un qui a deja obtenu ce qu'il
    # voulait.
    await repo.remove(str(user.id), payload.endpoint)
    return MessageResponse(message="Subscription removed")
