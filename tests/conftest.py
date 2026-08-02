import asyncio
import os

import pytest
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

load_dotenv()

TEST_DATABASE_URL = os.getenv("DB_URL_TEST")
if not TEST_DATABASE_URL:
    raise RuntimeError("DB_URL_TEST doit etre defini dans .env pour lancer les tests")

test_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
TestSessionLocal = async_sessionmaker(test_engine, expire_on_commit=False, autoflush=False)

import core.database as core_database

core_database.AsyncSessionLocal = TestSessionLocal

from app import app
from core.rate_limit import limiter
from models.db import Base
from services.emailing.email_sender import EmailSender


def _run(coro_factory):
    async def go():
        return await coro_factory()

    return asyncio.run(go())


@pytest.fixture(scope="session", autouse=True)
def _schema():
    async def create():
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    _run(create)
    yield


@pytest.fixture(autouse=True)
def _clean_tables():
    async def wipe():
        async with test_engine.begin() as conn:
            await conn.execute(text("SET LOCAL lock_timeout = '5s'"))
            await conn.execute(text("DELETE FROM users"))
            await conn.execute(text("DELETE FROM refresh_tokens"))

    _run(wipe)
    yield


@pytest.fixture(autouse=True)
def _disable_rate_limit():
    limiter.reset()
    limiter.enabled = False
    yield
    limiter.enabled = False
    limiter.reset()


@pytest.fixture
def rate_limit_on():
    limiter.reset()
    limiter.enabled = True
    yield
    limiter.enabled = False
    limiter.reset()


@pytest.fixture(autouse=True)
def mailbox(monkeypatch):
    sent = []

    async def capture_verification(self, to, code):
        sent.append({"kind": "verification", "to": to, "code": code})
        return {"id": "test"}

    async def capture_reset(self, to, code):
        sent.append({"kind": "reset", "to": to, "code": code})
        return {"id": "test"}

    async def capture_change(self, to, code):
        sent.append({"kind": "email_change", "to": to, "code": code})
        return {"id": "test"}

    async def capture_admin(self, to, subject, body_text):
        sent.append({"kind": "admin", "to": to, "subject": subject})
        return {"id": "test"}

    async def capture_reminder(self, to, *, first_name, items, currency):
        sent.append(
            {
                "kind": "reminder",
                "to": to,
                "first_name": first_name,
                "items": items,
                "currency": currency,
            }
        )
        return {"id": "test"}

    monkeypatch.setattr(EmailSender, "send_reminder_email", capture_reminder)
    monkeypatch.setattr(EmailSender, "send_verification_email", capture_verification)
    monkeypatch.setattr(EmailSender, "send_reset_password_email", capture_reset)
    monkeypatch.setattr(EmailSender, "send_email_change_email", capture_change)
    monkeypatch.setattr(EmailSender, "send_admin_email", capture_admin)
    return sent


@pytest.fixture(scope="session")
def client(_schema):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    def query(sql: str, **params):
        async def go():
            async with test_engine.begin() as conn:
                result = await conn.execute(text(sql), params)
                if result.returns_rows:
                    return result.fetchall()
                return None

        return asyncio.run(go())

    return query


@pytest.fixture
def session_runner():
    def run(work):
        async def go():
            async with TestSessionLocal() as session:
                result = await work(session)
                await session.commit()
                return result

        return asyncio.run(go())

    return run


@pytest.fixture
def credentials():
    return {"first_name": "Alexis", "email": "alexis@example.com", "password": "MotDePasse1!"}


@pytest.fixture
def registered(client, credentials, mailbox):
    response = client.post("/v1/auth/register", json=credentials)
    assert response.status_code == 201
    return {**credentials, "code": mailbox[-1]["code"]}


@pytest.fixture
def verified(client, registered):
    response = client.post(
        "/v1/auth/verify-email",
        json={"email": registered["email"], "code": registered["code"]},
    )
    assert response.status_code == 200
    return {
        **registered,
        "tokens": response.json(),
        "set_cookie": response.headers.get("set-cookie", ""),
    }
