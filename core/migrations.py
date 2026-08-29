import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from core.database import engine

logger = logging.getLogger(__name__)

# Un verrou distinct de celui du cron : les deux peuvent tourner en meme temps,
# et se bloquer mutuellement ferait attendre un demarrage pour rien.
LOCK_KEY = 8142027

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _upgrade(connection) -> None:
    config = Config(str(ALEMBIC_INI))
    # La connexion est passee a env.py plutot qu'un URL : le moteur de
    # l'application est deja ouvert, et en ouvrir un second doublerait le
    # nombre de connexions au demarrage.
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


async def run_migrations() -> None:
    # Le verrou est bloquant, pas opportuniste : une replique qui renoncerait
    # servirait des requetes sur un schema encore incomplet. Elle attend, puis
    # trouve la base a jour et ne fait rien.
    async with engine.begin() as connection:
        await connection.execute(text("select pg_advisory_lock(:key)"), {"key": LOCK_KEY})
        try:
            await connection.run_sync(_upgrade)
        finally:
            await connection.execute(
                text("select pg_advisory_unlock(:key)"), {"key": LOCK_KEY}
            )

    logger.info("Migrations appliquees")
