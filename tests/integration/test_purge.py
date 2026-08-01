import asyncio

import pytest

from core.config import REFRESH_COOKIE_NAME, REFRESH_COOKIE_PATH
from repositories.refresh_token_repository import RefreshTokenRepository
from tests.conftest import TestSessionLocal

pytestmark = pytest.mark.integration


def purge(grace_days=7):
    async def go():
        async with TestSessionLocal() as session:
            deleted = await RefreshTokenRepository(session).purge_expired(grace_days)
            await session.commit()
            return deleted

    return asyncio.run(go())


def count(db):
    return db("select count(*) from refresh_tokens")[0][0]


class TestPurgeExpired:
    def test_keeps_live_tokens(self, client, verified, db):
        assert purge() == 0
        assert count(db) == 1

    def test_deletes_tokens_expired_beyond_the_grace_period(self, client, verified, db):
        db("update refresh_tokens set expires_at = now() - interval '30 days'")
        assert purge() == 1
        assert count(db) == 0

    def test_keeps_tokens_inside_the_grace_period(self, client, verified, db):
        db("update refresh_tokens set expires_at = now() - interval '2 days'")
        assert purge() == 0
        assert count(db) == 1

    def test_keeps_revoked_but_unexpired_tokens(self, client, verified, db):
        client.post("/v1/auth/logout")
        assert db("select count(*) from refresh_tokens where revoked = true") == [(1,)]
        assert purge() == 0
        assert count(db) == 1

    def test_reuse_detection_survives_a_purge(self, client, verified):
        old = client.cookies.get(REFRESH_COOKIE_NAME)
        client.post("/v1/auth/refresh")
        fresh = client.cookies.get(REFRESH_COOKIE_NAME)

        purge()

        client.cookies.clear()
        client.cookies.set(REFRESH_COOKIE_NAME, old, path=REFRESH_COOKIE_PATH)
        replay = client.post("/v1/auth/refresh")
        assert replay.status_code == 401
        assert replay.json()["detail"]["code"] == "TOKEN_REUSE_DETECTED"

        client.cookies.clear()
        client.cookies.set(REFRESH_COOKIE_NAME, fresh, path=REFRESH_COOKIE_PATH)
        after = client.post("/v1/auth/refresh")
        assert after.status_code == 401

    def test_purging_twice_is_harmless(self, client, verified, db):
        db("update refresh_tokens set expires_at = now() - interval '30 days'")
        assert purge() == 1
        assert purge() == 0

    def test_grace_period_is_configurable(self, client, verified, db):
        db("update refresh_tokens set expires_at = now() - interval '2 days'")
        assert purge(grace_days=1) == 1

    def test_purge_on_an_empty_table(self):
        assert purge() == 0
