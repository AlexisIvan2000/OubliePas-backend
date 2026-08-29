import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from core.database import engine

logger = logging.getLogger(__name__)


LOCK_KEY = 8142027

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _upgrade(connection) -> None:
    config = Config(str(ALEMBIC_INI))
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


async def run_migrations() -> None:
    async with engine.begin() as connection:
        await connection.execute(text("select pg_advisory_lock(:key)"), {"key": LOCK_KEY})
        try:
            await connection.run_sync(_upgrade)
        finally:
            await connection.execute(
                text("select pg_advisory_unlock(:key)"), {"key": LOCK_KEY}
            )

    logger.info("Migrations appliquees")
