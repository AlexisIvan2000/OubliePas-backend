import logging
from datetime import date, datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "UTC"
MAX_TIMEZONE_LENGTH = 64


@lru_cache(maxsize=1)
def known_timezones() -> frozenset[str]:
    # available_timezones parcourt la base tz a chaque appel : sans ce cache,
    # valider un profil couterait une lecture de disque.
    return frozenset(available_timezones())


def is_known_timezone(name: str | None) -> bool:
    return bool(name) and name in known_timezones()


@lru_cache(maxsize=512)
def _zone(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def zone_named(name: str | None) -> ZoneInfo:
    name = name or DEFAULT_TIMEZONE
    try:
        return _zone(name)
    except Exception:
        # Le nom est valide a l'ecriture, mais une base tz peut retirer une zone
        # d'une version a l'autre. Un tableau de bord qui rend 500 pour cela
        # serait pire que le decalage d'un fuseau : on retombe sur UTC, et on le
        # dit dans le journal plutot que de le taire.
        logger.warning("unknown time zone %r stored for a user, falling back to UTC", name)
        return _zone(DEFAULT_TIMEZONE)


def zone_of(user) -> ZoneInfo:
    return zone_named(getattr(user, "timezone", None))


def _now() -> datetime:
    # L'unique lecture d'horloge du projet. Tout le reste en derive, ce qui rend
    # une date gelable en un seul point pour les tests, et garantit qu'un meme
    # passage ne voit pas deux instants differents.
    return datetime.now(timezone.utc)


def now_for(user) -> datetime:
    return _now().astimezone(zone_of(user))


def date_at(moment: datetime, name: str | None) -> date:
    """Le jour qu'il est dans ce fuseau a cet instant.

    Un instant est la verite, une date n'en est qu'une projection : le cron
    part d'un moment unique et chacun le lit dans son propre calendrier. Sans
    cela, une passe rejouee pour une date passee relirait l'horloge du jour.
    """
    return moment.astimezone(zone_named(name)).date()


def today_in(name: str | None) -> date:
    """Le jour dans ce fuseau, pour les boucles qui tiennent le nom sans la personne."""
    return date_at(_now(), name)


def today_for(user) -> date:
    """Le jour tel que cette personne le voit : seule definition de « aujourd'hui »."""
    return now_for(user).date()


def today_utc() -> date:
    # Ce qui n'appartient a personne : les purges, les journaux, le verrou du
    # cron. Tout calcul qu'un utilisateur verra passe par today_for.
    return _now().date()
