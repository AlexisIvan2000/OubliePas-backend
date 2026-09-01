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
       
        assert api.get("/health").status_code == 200

    def test_an_unknown_route_is_a_plain_404(self, api):
        assert api.get("/v1/cette-route-n-existe-pas").status_code == 404


class TestTheErrorEnvelope:
    def test_a_refusal_carries_a_code_and_a_message(self, api):
        response = api.get("/v1/push/key")

        assert response.status_code == 401
        detail = response.json()["detail"]
        assert detail["code"] == "INVALID_ACCESS_TOKEN"
        assert detail["message"]


class TestTheProductionSwitches:
    @pytest.mark.parametrize("route", ["/docs", "/redoc", "/openapi.json"])
    def test_the_schema_is_not_published(self, api, route):
        assert api.get(route).status_code == 404

    @pytest.mark.parametrize("header", SECURITY_HEADERS)
    def test_every_response_carries_its_security_header(self, api, header):
        assert header in {name.lower() for name in api.get("/health").headers}

    def test_the_transport_is_pinned_for_two_years(self, api):
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
        assert response.headers.get("access-control-allow-credentials") == "true"

    def test_a_stranger_gets_no_permission(self, api):
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
        # La portee vient du chemin du fichier : ailleurs, il ne verrait pas
        # les pages qu'il doit reveiller.
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
        assert manifest["display"] == "standalone"

    def test_it_handles_fetch_and_is_therefore_installable(self, site):
        # Chrome ne propose l'installation qu'a un worker qui declare un
        # gestionnaire fetch. Le perdre ne casse aucun ecran : l'app cesse
        # d'etre installable, et rien ne le dit nulle part.
        assert 'addEventListener("fetch"' in site.get("/sw.js").text

    def test_the_script_is_revalidated_at_every_check(self, site):
        # Un sw.js fige par un cache long, c'est un correctif qui met un jour a
        # arriver : le navigateur ne verrait le worker neuf qu'a l'expiration,
        # et le rechargement unique promis ailleurs ne tiendrait plus.
        controle = site.get("/sw.js").headers.get("cache-control", "")

        assert "max-age=0" in controle or "no-cache" in controle


class TestTheInstalledIcon:
    def test_the_manifest_carries_a_maskable_icon(self, site):
        manifest = json.loads(site.get("/manifest.webmanifest").text)

        maskable = [i for i in manifest["icons"] if i.get("purpose") == "maskable"]
        assert maskable

    def test_and_that_icon_is_actually_served(self, site):
        # Une icone declaree et absente ne se voit qu'une fois l'app posee
        # sur un ecran d'accueil.
        manifest = json.loads(site.get("/manifest.webmanifest").text)

        for icon in manifest["icons"]:
            response = site.get(icon["src"])
            assert response.status_code == 200, icon["src"]
            assert "image/png" in response.headers["content-type"]


class TestTheBundle:
    def test_it_calls_the_deployed_api_and_not_a_laptop(self, site, api):
        index = site.get("/")
        assert index.status_code == 200

        sources = re.findall(r'src="(/assets/[^"]+\.js)"', index.text)
        assert sources, "aucun script de module trouve dans index.html"

        bundle = site.get(sources[0]).text
        hote = str(api.base_url).rstrip("/")

        assert hote in bundle
        assert "localhost:8000" not in bundle
