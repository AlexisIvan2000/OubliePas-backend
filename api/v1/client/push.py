import logging

from fastapi import APIRouter, Request, status

from api.dependencies import CurrentUserDep, PushRepoDep, PushSenderDep
from core.config import VAPID_PUBLIC_KEY, push_configured
from core.exceptions import (
    PushEndpointRefused,
    PushNotConfigured,
    PushSubscriptionGone,
)
from core.rate_limit import READ_LIMIT, limiter
from models.schemas.auth_schema import MessageResponse
from models.schemas.push_schema import (
    PushKeyResponse,
    PushSubscriptionIn,
    PushSubscriptionResponse,
    PushTest,
    PushUnsubscribe,
)
from services.pushing.endpoint_policy import refusal_reason, refused_host

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/push", tags=["push"])


@router.get("/key", response_model=PushKeyResponse)
@limiter.limit(READ_LIMIT)
async def public_key(request: Request, user: CurrentUserDep):
    # Nulle plutôt qu'absente : le client distingue ainsi « pas installé ici »
    # d'une panne.
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
    motif = refusal_reason(payload.endpoint)
    if motif is not None:
        # L'hôte seul, jamais l'adresse, qui vaut porteur d'autorité. Ce
        # journal est la piste d'un compte qui sonde le réseau interne.
        logger.warning(
            "user %s offered a push address we refuse (%s, host %s)",
            user.id,
            motif,
            refused_host(payload.endpoint),
        )
        raise PushEndpointRefused()

    saved = await repo.save(
        str(user.id),
        endpoint=payload.endpoint,
        p256dh=payload.p256dh,
        auth=payload.auth,
        user_agent=payload.user_agent,
    )
    return PushSubscriptionResponse(endpoint=saved.endpoint, enabled=user.reminder_push_enabled)


@router.post("/test", response_model=MessageResponse)
@limiter.limit("10/hour")
async def send_test(
    request: Request,
    payload: PushTest,
    user: CurrentUserDep,
    repo: PushRepoDep,
    sender: PushSenderDep,
):
    if not push_configured():
        raise PushNotConfigured()

    subscription = await repo.find(str(user.id), payload.endpoint)
    if subscription is None:
        raise PushSubscriptionGone()

    # La preuve vient du service de push, jamais du navigateur : une
    # notification fabriquée ici s'afficherait même le chemin coupé.
    if await sender.send_test(subscription, locale=user.locale) == "gone":
        await repo.forget(payload.endpoint)
        raise PushSubscriptionGone()

    return MessageResponse(message="Test notification sent")


@router.delete("/subscriptions", response_model=MessageResponse)
@limiter.limit("30/hour")
async def unsubscribe(
    request: Request, payload: PushUnsubscribe, user: CurrentUserDep, repo: PushRepoDep
):
    # Silencieuse quand rien ne correspond : se désabonner deux fois est le cas
    # normal, pas une erreur à signaler.
    await repo.remove(str(user.id), payload.endpoint)
    return MessageResponse(message="Subscription removed")
