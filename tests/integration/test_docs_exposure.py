import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.middlewares.security_headers import DOCS_PATHS
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
