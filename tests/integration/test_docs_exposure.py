import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.middlewares.security_headers import DOCS_PATHS, SecurityHeadersMiddleware
from app import docs_urls

pytestmark = pytest.mark.integration

DOCS_ROUTES = ("/docs", "/redoc", "/openapi.json")


class TestOutOfDebug:
    @pytest.fixture
    def closed(self):
        app = FastAPI(**docs_urls(False))
        return TestClient(app)

    @pytest.mark.parametrize("route", DOCS_ROUTES)
    def test_the_route_is_gone(self, closed, route):
        assert closed.get(route).status_code == 404

    def test_nothing_is_left_to_serve(self):
        assert set(docs_urls(False).values()) == {None}


class TestInDebug:
    @pytest.fixture
    def opened(self):
        app = FastAPI(**docs_urls(True))
        return TestClient(app)

    @pytest.mark.parametrize("route", DOCS_ROUTES)
    def test_the_route_answers(self, opened, route):
        assert opened.get(route).status_code == 200

    def test_the_relaxed_policy_still_has_a_consumer(self):
        # DOCS_CSP n'est pas mort : sans lui, default-src 'none' empeche Swagger UI
        # de charger ses scripts, et les docs locales rendent une page blanche.
        assert set(DOCS_PATHS) & set(docs_urls(True).values())


INLINE_SCRIPT = re.compile(r"<script(?![^>]*src=)[^>]*>(.*?)</script>", re.S)
EXTERNAL = re.compile(r"<(?:script|link)[^>]*(?:src|href)=\"(https?://[^/\"]+)")


def directive(policy, name):
    for part in policy.split(";"):
        if part.strip().startswith(name + " "):
            return part.strip()
    return ""


class TestThePolicyMatchesThePage:
    """La page des docs et sa politique doivent rester d'accord. Une CSP qui
    interdit ce que la page contient ne provoque pas d'erreur : elle affiche une
    page blanche, et c'est exactement ce qui s'etait produit."""

    @pytest.fixture
    def served(self):
        app = FastAPI(**docs_urls(True))
        app.add_middleware(SecurityHeadersMiddleware)
        client = TestClient(app)
        return {
            path: client.get(path)
            for path in DOCS_PATHS
            if client.get(path).status_code == 200
        }

    def test_some_page_is_actually_served(self, served):
        assert served

    def test_an_inline_bootstrap_is_permitted(self, served):
        for path, response in served.items():
            if not INLINE_SCRIPT.findall(response.text):
                continue
            policy = directive(response.headers["content-security-policy"], "script-src")
            assert "'unsafe-inline'" in policy, path

    def test_every_external_host_is_listed(self, served):
        for path, response in served.items():
            policy = response.headers["content-security-policy"]
            for host in set(EXTERNAL.findall(response.text)):
                assert host in policy, f"{path} charge {host}, absent de la politique"

    def test_the_api_policy_stays_closed(self, client):
        policy = client.get("/health").headers["content-security-policy"]

        assert "unsafe-inline" not in policy
        assert policy.startswith("default-src 'none'")
