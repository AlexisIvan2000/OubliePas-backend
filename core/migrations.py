import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from core.database import engine

logger = logging.getLogger(__name__)

# Distinct de celui du cron : les deux peuvent tourner en même temps, et se
# bloquer mutuellement ferait attendre un démarrage pour rien.
LOCK_KEY = 8142027

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _upgrade(connection) -> None:
    config = Config(str(ALEMBIC_INI))
    # La connexion plutôt qu'une URL : le moteur est déjà ouvert, et en ouvrir
    # un second doublerait les connexions au démarrage.
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


async def run_migrations() -> None:
    # Bloquant et non opportuniste : une réplique qui renoncerait servirait des
    # requêtes sur un schéma incomplet. Elle attend, puis ne trouve rien à faire.
    async with engine.begin() as connection:
        await connection.execute(text("select pg_advisory_lock(:key)"), {"key": LOCK_KEY})
        try:
            await connection.run_sync(_upgrade)
        finally:
            await connection.execute(
                text("select pg_advisory_unlock(:key)"), {"key": LOCK_KEY}
            )

    logger.info("Migrations appliquees")
