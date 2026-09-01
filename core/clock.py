import logging
from datetime import date, datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "UTC"
MAX_TIMEZONE_LENGTH = 64


@lru_cache(maxsize=1)
def known_timezones() -> frozenset[str]:
    return frozenset(available_timezones())


def is_known_timezone(name: str | None) -> bool:
    return bool(name) and name in known_timezones()


@lru_cache(maxsize=512)
def _zone(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def zone_of(user) -> ZoneInfo:
    name = getattr(user, "timezone", None) or DEFAULT_TIMEZONE
    try:
        return _zone(name)
    except Exception:
        logger.warning("unknown time zone %r stored for a user, falling back to UTC", name)
        return _zone(DEFAULT_TIMEZONE)


def now_for(user) -> datetime:
    return datetime.now(zone_of(user))


def today_for(user) -> date:
    return now_for(user).date()


def today_utc() -> date:
    return datetime.now(timezone.utc).date()
