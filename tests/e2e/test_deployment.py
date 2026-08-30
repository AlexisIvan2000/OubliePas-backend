import json
import re

import pytest

pytestmark = pytest.mark.e2e

SECURITY_HEADERS = (
    "strict-transport-security",
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
    "content-security-policy",
    "permissions-policy",
)


class TestTheApiAnswers:
    def test_health_says_ok(self, api):
        response = api.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_answering_at_all_proves_alembic_reached_head(self, api):
        # Ce n'est pas une tautologie. Le lifespan joue les migrations avant de
        # servir la moindre requete, et un echec arrete le conteneur au lieu de
        # le laisser repondre. Une reponse ici prouve donc que le schema est a
        # jour — la seule verification de ce genre qu'on puisse faire de
        # l'exterieur, et celle que la suite d'integration ne peut pas faire du
        # tout puisqu'elle batit son schema avec create_all.
        assert api.get("/health").status_code == 200

    def test_an_unknown_route_is_a_plain_404(self, api):
        assert api.get("/v1/cette-route-n-existe-pas").status_code == 404


class TestTheErrorEnvelope:
    def test_a_refusal_carries_a_code_and_a_message(self, api):
        # Le front choisit son texte sur « code » : une enveloppe d'une autre
        # forme lui ferait afficher le message generique a la place du bon.
        response = api.get("/v1/push/key")

        assert response.status_code == 401
        detail = response.json()["detail"]
        assert detail["code"] == "INVALID_ACCESS_TOKEN"
        assert detail["message"]


class TestTheProductionSwitches:
    @pytest.mark.parametrize("route", ["/docs", "/redoc", "/openapi.json"])
    def test_the_schema_is_not_published(self, api, route):
        # DEBUG faux ferme ces trois routes. Ouvertes, elles decriraient toute
        # la surface de l'API a un service qui n'a qu'un client, deja au
        # courant.
        assert api.get(route).status_code == 404

    @pytest.mark.parametrize("header", SECURITY_HEADERS)
    def test_every_response_carries_its_security_header(self, api, header):
        assert header in {name.lower() for name in api.get("/health").headers}

    def test_the_transport_is_pinned_for_two_years(self, api):
        # Le cookie de rafraichissement est Secure : sans HSTS, une premiere
        # visite en clair pourrait encore etre detournee.
        valeur = api.get("/health").headers["strict-transport-security"]

        assert "includeSubDomains" in valeur
        assert int(re.search(r"max-age=(\d+)", valeur).group(1)) >= 31536000


class TestCors:
    def test_the_site_may_call_the_api_with_its_cookie(self, api, site):
        origine = str(site.base_url).rstrip("/")
        response = api.request(
            "OPTIONS",
            "/v1/push/key",
            headers={"Origin": origine, "Access-Control-Request-Method": "GET"},
        )

        assert response.headers.get("access-control-allow-origin") == origine
        # Sans cette ligne le navigateur enverrait la requete sans le cookie de
        # session, et toute l'application repondrait 401.
        assert response.headers.get("access-control-allow-credentials") == "true"

    def test_a_stranger_gets_no_permission(self, api):
        # L'en-tete absent suffit : c'est le navigateur qui bloque, pas le
        # serveur. Un « * » ici, avec les identifiants autorises, serait refuse
        # par le navigateur et casserait la session au lieu de la proteger.
        response = api.request(
            "OPTIONS",
            "/v1/push/key",
            headers={
                "Origin": "https://voisin-indiscret.example",
                "Access-Control-Request-Method": "GET",
            },
        )

        assert "access-control-allow-origin" not in response.headers


class TestTheServiceWorker:
    def test_it_is_served_from_the_root(self, site):
        # La portee d'un service worker vient du chemin de son fichier : servi
        # ailleurs qu'a la racine, il ne verrait pas les pages qu'il doit
        # reveiller, et aucune notification n'arriverait.
        response = site.get("/sw.js")

        assert response.status_code == 200
        assert "javascript" in response.headers["content-type"].lower()
        assert "notificationclick" in response.text

    def test_the_manifest_claims_the_whole_site(self, site):
        response = site.get("/manifest.webmanifest")

        assert response.status_code == 200
        manifest = json.loads(response.text)
        assert manifest["scope"] == "/"
        assert manifest["start_url"] == "/"
        # Sans « standalone », l'ajout a l'ecran d'accueil sur iPhone n'ouvre
        # pas une application mais un onglet, et Safari n'y expose pas
        # PushManager.
        assert manifest["display"] == "standalone"


class TestTheBundle:
    def test_it_calls_the_deployed_api_and_not_a_laptop(self, site, api):
        # Un VITE_API_URL oublie a localhost produit un site qui s'affiche
        # parfaitement et ne peut joindre personne. Rien dans le build ne le
        # signale.
        index = site.get("/")
        assert index.status_code == 200

        sources = re.findall(r'src="(/assets/[^"]+\.js)"', index.text)
        assert sources, "aucun script de module trouve dans index.html"

        bundle = site.get(sources[0]).text
        hote = str(api.base_url).rstrip("/")

        assert hote in bundle
        assert "localhost:8000" not in bundle
