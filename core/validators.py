from disposable_email_domains import blocklist

DISPOSABLE_EMAIL_DOMAINS = frozenset(blocklist)


def is_disposable_email(email: str) -> bool:
    if "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1].strip().lower()
    if not domain:
        return False
    labels = domain.split(".")
    return any(
        ".".join(labels[i:]) in DISPOSABLE_EMAIL_DOMAINS for i in range(len(labels) - 1)
    )


def normalize_email(email: str) -> str:
    return email.strip().lower()


def normalize_currency(value: str) -> str:
    if not value.isalpha():
        raise ValueError("Currency must be a 3-letter ISO code")
    return value.upper()
