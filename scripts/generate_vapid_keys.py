import argparse
import base64

from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_private_key,
)
from py_vapid import Vapid01


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def encode(private_key) -> tuple[str, str]:
    # Le format brut, pas le PEM : py_vapid signe a partir de from_raw, qui
    # refuse un PEM sur une erreur de courbe illisible. La commande `vapid`
    # livree avec la bibliotheque ecrit pourtant des .pem, et c'est le piege.
    public = private_key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    private = private_key.private_numbers().private_value.to_bytes(32, "big")
    return b64(public), b64(private)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print a VAPID pair in the raw url-safe base64 form the API expects."
    )
    parser.add_argument(
        "--from-pem",
        metavar="FILE",
        help="convert an existing private key instead of generating a new one",
    )
    args = parser.parse_args()

    if args.from_pem:
        with open(args.from_pem, "rb") as handle:
            key = load_pem_private_key(handle.read(), password=None)
    else:
        vapid = Vapid01()
        vapid.generate_keys()
        key = vapid.private_key

    public, private = encode(key)

    print(f"VAPID_PUBLIC_KEY={public}")
    print(f"VAPID_PRIVATE_KEY={private}")
    print()
    print("Set both on every Railway service, API and cron alike.")
    print("Back the private key up like JWT_SECRET_KEY: losing it silently")
    print("invalidates every subscription browsers have already stored.")


if __name__ == "__main__":
    main()
