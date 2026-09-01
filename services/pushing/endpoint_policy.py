import ipaddress
from urllib.parse import urlsplit

# Peu nombreux et stables : une liste nommée coûte moins qu'une heuristique,
# et elle refuse par défaut.
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

# Le plancher tient même si la liste s'ouvre un jour : il ne connaît pas les
# services, il connaît les adresses qu'une API ne doit jamais appeler.
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

    # hostname et non netloc : « https://fcm.googleapis.com@interne/ » a pour
    # netloc l'hôte autorisé et pour hostname la vraie cible. hostname rend
    # aussi la casse normalisée, d'où l'absence de .lower() ici.
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

    # Le point du suffixe porte la garde : sans lui, « evilnotify.windows.com »
    # passerait pour un hôte de Microsoft.
    if host in ALLOWED_HOSTS or host.endswith(ALLOWED_SUFFIXES):
        return None

    return "not-allowed"


def refused_host(endpoint: str) -> str:
    """L'hote seul, pour le journal : l'adresse complete est un porteur d'autorite."""
    try:
        return urlsplit(endpoint).hostname or "?"
    except ValueError:
        return "?"
