import base64
import json
import logging
import os
import time
from urllib.parse import urlsplit

import http_ece
import httpx
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid02

from core.config import (
    FRONTEND_URL,
    VAPID_PRIVATE_KEY,
    VAPID_SUBJECT,
    push_configured,
)
from services.emailing import messages
from services.pushing.endpoint_policy import refusal_reason, refused_host

logger = logging.getLogger(__name__)

# Au-dela, un rappel du matin arriverait apres l'echeance.
TTL_SECONDS = 12 * 3600
TOKEN_LIFETIME_SECONDS = 24 * 3600
GONE = (404, 410)


def _b64(raw: str) -> bytes:
    # Souvent sans padding cote navigateur.
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


class PushSender:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    async def _post(self, url: str, *, content: bytes, headers: dict) -> httpx.Response:
        if self._client is not None:
            return await self._client.post(url, content=content, headers=headers)
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await client.post(url, content=content, headers=headers)

    def _headers(self, endpoint: str, payload: bytes) -> dict:
        origin = urlsplit(endpoint)
        vapid = Vapid02.from_raw(VAPID_PRIVATE_KEY.encode("utf-8"))
        # L'origine, pas l'adresse : un jeton signe pour une adresse precise
        # serait refuse par les autres.
        signed = vapid.sign(
            {
                "aud": f"{origin.scheme}://{origin.netloc}",
                "exp": int(time.time()) + TOKEN_LIFETIME_SECONDS,
                "sub": VAPID_SUBJECT,
            }
        )
        return {
            **signed,
            "TTL": str(TTL_SECONDS),
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(payload)),
        }

    async def send(self, subscription, *, title: str, body: str, url: str) -> str:
        """Rend 'sent', 'gone' quand l'abonnement est mort, ou leve."""
        if not push_configured():
            raise RuntimeError("VAPID keys are missing")

        # Deuxieme verrou, pour les lignes ecrites avant la liste blanche et
        # pour le cron, qui lit la table sans repasser par la route. Rendue
        # morte plutot que levee : une adresse qu'on ne composera jamais est
        # aussi inutile qu'une adresse que le service declare disparue, et
        # l'appelant l'efface deja dans ce cas. Lever la garderait pour
        # toujours et la ferait resonner a chaque passage.
        motif = refusal_reason(subscription.endpoint)
        if motif is not None:
            logger.warning(
                "refusing to post to a stored push address (%s, host %s)",
                motif,
                refused_host(subscription.endpoint),
            )
            return "gone"

        payload = json.dumps({"title": title, "body": body, "url": url}).encode("utf-8")
        # Une paire ephemere par envoi, exigee par aes128gcm : sans elle
        # http_ece leve avant meme d'ouvrir une connexion.
        encrypted = http_ece.encrypt(
            payload,
            salt=os.urandom(16),
            private_key=ec.generate_private_key(ec.SECP256R1()),
            dh=_b64(subscription.p256dh),
            auth_secret=_b64(subscription.auth),
            version="aes128gcm",
        )
        response = await self._post(
            subscription.endpoint,
            content=encrypted,
            headers=self._headers(subscription.endpoint, encrypted),
        )
        if response.status_code in GONE:
            # Personne d'autre ne nous dira que l'adresse est morte.
            return "gone"
        response.raise_for_status()
        return "sent"

    async def send_reminder(self, subscription, *, kind: str, items: list, locale: str) -> str:
        locale = messages.pick(locale)
        count = len(items)
        title = messages.text(locale, "push_title")
        # Jamais le montant : un ecran verrouille est un lieu public.
        if count == 1:
            body = messages.text(
                locale, f"push_{kind}_one", title=str(items[0]["title"]),
                days=items[0]["days_left"],
            )
        else:
            body = messages.text(locale, f"push_{kind}_many", count=count)
        return await self.send(
            subscription, title=title, body=body, url=f"{FRONTEND_URL}/calendrier"
        )

    async def send_test(self, subscription, *, locale: str) -> str:
        locale = messages.pick(locale)
        return await self.send(
            subscription,
            title=messages.text(locale, "push_title"),
            body=messages.text(locale, "push_test"),
            url=f"{FRONTEND_URL}/rappels",
        )
