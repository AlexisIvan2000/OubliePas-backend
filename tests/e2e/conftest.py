import os

import httpx
import pytest


API_URL = os.getenv("E2E_API_URL")
FRONTEND_URL = os.getenv("E2E_FRONTEND_URL")

ABSENT = (
    "adresse absente : lancer avec "
    "E2E_API_URL=https://api.oubliepas.com E2E_FRONTEND_URL=https://oubliepas.com"
)


@pytest.fixture(scope="session")
def api():
    if not API_URL:
        pytest.skip(ABSENT)
    with httpx.Client(base_url=API_URL, timeout=20.0, follow_redirects=False) as client:
        yield client


@pytest.fixture(scope="session")
def site():
    if not FRONTEND_URL:
        pytest.skip(ABSENT)
    with httpx.Client(base_url=FRONTEND_URL, timeout=20.0, follow_redirects=True) as client:
        yield client
