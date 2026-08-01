import pytest


@pytest.fixture(autouse=True)
def clean_cookies(client):
    client.cookies.clear()
    yield
    client.cookies.clear()
