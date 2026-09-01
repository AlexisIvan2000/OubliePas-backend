import ipaddress
from urllib.parse import urlsplit

# Les services de push sont peu nombreux et changent rarement : une liste
# nommee coute moins qu'une heuristique, et elle refuse par defaut.
ALLOWED_HOSTS = frozenset(
    {
        "fcm.googleapis.com",
        "android.googleapis.com",
        "web.push.apple.com",
    }
)

ALLOWED_SUFFIXES = (
    ".push.services.mozilla.com",
    ".notify.windows.com",
    ".notify.live.net",
)

# Le plancher tient meme si la liste s'ouvre un jour : il ne connait pas les
# services, il connait les adresses qu'une API ne doit jamais appeler.
INTERNAL_SUFFIXES = (".internal", ".local", ".localdomain", ".home.arpa")
INTERNAL_NAMES = frozenset({"localhost", "metadata", "metadata.google.internal"})

ALLOWED_PORTS = (None, 443)


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def refusal_reason(endpoint: str) -> str | None:
    """Rend un motif court, ou None quand l'adresse est acceptable."""
    try:
        parts = urlsplit(endpoint)
    except ValueError:
        return "unparsable"

    if parts.scheme != "https":
        return "scheme"

    # hostname et non netloc, pour deux raisons : « https://fcm.googleapis.com
    # @interne/ » a pour netloc l'hote autorise et pour hostname la vraie
    # cible, et hostname rend deja la casse normalisee — un .lower() de plus
    # serait une ligne qu'aucun test ne pourrait faire tomber.
    host = (parts.hostname or "").rstrip(".")
    if not host:
        return "no-host"

    if _is_ip_literal(host):
        return "ip-literal"

    if host in INTERNAL_NAMES or host.endswith(INTERNAL_SUFFIXES):
        return "internal"

    try:
        port = parts.port
    except ValueError:
        return "port"
    if port not in ALLOWED_PORTS:
        return "port"

    # Le point du suffixe est porteur : sans lui, « evilnotify.windows.com »
    # passerait pour un hote de Microsoft.
    if host in ALLOWED_HOSTS or host.endswith(ALLOWED_SUFFIXES):
        return None

    return "not-allowed"


def refused_host(endpoint: str) -> str:
    """L'hote seul, pour le journal : l'adresse complete est un porteur d'autorite."""
    try:
        return urlsplit(endpoint).hostname or "?"
    except ValueError:
        return "?"
