import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import AsyncSessionLocal, dispose_engine
from repositories.refresh_token_repository import PURGE_GRACE_DAYS, RefreshTokenRepository


async def purge(grace_days: int) -> int:
    async with AsyncSessionLocal() as session:
        deleted = await RefreshTokenRepository(session).purge_expired(grace_days)
        await session.commit()
    await dispose_engine()
    return deleted


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Supprime les refresh tokens expires depuis plus de N jours. "
            "Les tokens revoques mais non expires sont conserves : la detection "
            "de rejeu en depend."
        )
    )
    parser.add_argument("--grace-days", type=int, default=PURGE_GRACE_DAYS)
    args = parser.parse_args()

    deleted = asyncio.run(purge(args.grace_days))
    print(f"{deleted} refresh token(s) supprime(s) (expires depuis plus de {args.grace_days} jours)")


if __name__ == "__main__":
    main()
