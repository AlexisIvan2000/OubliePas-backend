import base64
import json
import os

import http_ece
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import core.config as config
import services.pushing.push_sender as sender_module
from services.pushing.push_sender import PushSender

ENDPOINT = "https://fcm.googleapis.com/fcm/send/appareil"


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class Navigateur:
    # Le destinataire tel qu'il existe vraiment : une paire que le navigateur
    # garde pour lui, dont il ne publie que la moitie publique et un secret
    # d'authentification de seize octets.
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.secret = os.urandom(16)
        self.endpoint = ENDPOINT
        # Les deux champs voyagent en base64url, et c'est sous cette forme que
        # la table les garde : le test doit presenter ce que lit la production.
        self.auth = b64(self.secret)
        self.p256dh = b64(
            self.key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        )

    def lire(self, corps: bytes) -> dict:
        clair = http_ece.decrypt(
            corps, private_key=self.key, auth_secret=self.secret, version="aes128gcm"
        )
        return json.loads(clair)


class Reponse:
    def __init__(self, status_code):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"statut {self.status_code}")


class Transport:
    # Seul le reseau est feint. Le chiffrement et la signature VAPID tournent
    # pour de vrai : c'est precisement ce que la fixture pushbox, qui remplace
    # send en entier, ne pouvait pas verifier.
    def __init__(self, status_code=201):
        self.status_code = status_code
        self.url = None
        self.content = None
        self.headers = None

    async def post(self, url, *, content, headers):
        self.url = url
        self.content = content
        self.headers = headers
        return Reponse(self.status_code)


@pytest.fixture
def vapid(monkeypatch):
    vapid_key = ec.generate_private_key(ec.SECP256R1())
    brute = b64(vapid_key.private_numbers().private_value.to_bytes(32, "big"))
    publique = b64(
        vapid_key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    )
    monkeypatch.setattr(config, "VAPID_PUBLIC_KEY", publique)
    monkeypatch.setattr(config, "VAPID_PRIVATE_KEY", brute)
    monkeypatch.setattr(sender_module, "VAPID_PRIVATE_KEY", brute)
    return publique


class TestTheEncryptedPayload:
    async def test_the_device_can_read_what_we_sent_it(self, vapid):
        # Le test qui manquait. Le defaut vivait ici : http_ece etait appele
        # sans paire ephemere et levait avant toute connexion, donc en sept
        # millisecondes et sans qu'aucun test ne passe par ce chemin.
        navigateur = Navigateur()
        transport = Transport()

        resultat = await PushSender(transport).send(
            navigateur, title="Oublie pas !", body="Netflix dans 3 jours", url="/rappels"
        )

        assert resultat == "sent"
        assert navigateur.lire(transport.content) == {
            "title": "Oublie pas !",
            "body": "Netflix dans 3 jours",
            "url": "/rappels",
        }

    async def test_two_sends_never_reuse_the_same_ephemeral_key(self, vapid):
        # Rejouer la meme paire pour deux messages rendrait le second dechiffrable
        # a qui a garde le premier.
        navigateur = Navigateur()
        premier, second = Transport(), Transport()

        await PushSender(premier).send(navigateur, title="a", body="b", url="c")
        await PushSender(second).send(navigateur, title="a", body="b", url="c")

        assert premier.content[21:86] != second.content[21:86]

    async def test_a_test_notification_leads_back_to_the_reminders_page(self, vapid):
        navigateur = Navigateur()
        transport = Transport()

        await PushSender(transport).send_test(navigateur, locale="fr")

        assert navigateur.lire(transport.content)["url"].endswith("/rappels")


class TestTheHeaders:
    async def test_they_carry_the_vapid_token_and_the_encoding(self, vapid):
        navigateur = Navigateur()
        transport = Transport()

        await PushSender(transport).send(navigateur, title="a", body="b", url="c")

        assert transport.url == ENDPOINT
        assert transport.headers["Content-Encoding"] == "aes128gcm"
        assert transport.headers["Authorization"].startswith("vapid t=")
        assert transport.headers["Content-Length"] == str(len(transport.content))


class TestADeadAddress:
    @pytest.mark.parametrize("status_code", [404, 410])
    async def test_it_is_reported_rather_than_raised(self, vapid, status_code):
        navigateur = Navigateur()

        resultat = await PushSender(Transport(status_code)).send(
            navigateur, title="a", body="b", url="c"
        )

        assert resultat == "gone"


class TestWithoutAPair:
    async def test_it_refuses_before_touching_the_network(self, monkeypatch):
        monkeypatch.setattr(config, "VAPID_PUBLIC_KEY", None)
        monkeypatch.setattr(config, "VAPID_PRIVATE_KEY", None)
        transport = Transport()

        with pytest.raises(RuntimeError, match="VAPID"):
            await PushSender(transport).send(Navigateur(), title="a", body="b", url="c")

        assert transport.content is None
