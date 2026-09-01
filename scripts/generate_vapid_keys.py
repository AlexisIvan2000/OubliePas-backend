import argparse
import base64

from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_private_key,
)
from py_vapid import Vapid01

NAMES = ("VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY")


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def encode(private_key) -> tuple[str, str]:
    # Le format brut, pas le PEM : py_vapid signe depuis from_raw, qui refuse un
    # PEM sur une erreur de courbe illisible. La commande livree avec la
    # bibliothèque écrit pourtant des .pem, et c'est le piège.
    public = private_key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    private = private_key.private_numbers().private_value.to_bytes(32, "big")
    return b64(public), b64(private)


def write_env(path: str, values: dict) -> None:
    # Ligne à ligne plutôt qu'un rendu complet : .env porte des commentaires et
    # un ordre qui appartiennent à celui qui l'a écrit.
    with open(path, "rb") as handle:
        raw = handle.read()

    crlf = raw.count(b"\r\n") > 0
    lines = raw.decode("utf-8").replace("\r\n", "\n").split("\n")

    seen = set()
    for index, line in enumerate(lines):
        for name in NAMES:
            if line.startswith(name + "="):
                lines[index] = name + "=" + values[name]
                seen.add(name)

    absent = [name for name in NAMES if name not in seen]
    if absent:
        raise SystemExit(path + " : aucune ligne " + " ni ".join(absent) + " a remplacer.")

    text = "\n".join(lines)
    with open(path, "wb") as handle:
        handle.write((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print a VAPID pair in the raw url-safe base64 form the API expects."
    )
    parser.add_argument(
        "--from-pem",
        metavar="FILE",
        help="convert an existing private key instead of generating a new one",
    )
    parser.add_argument(
        "--write-env",
        metavar="FILE",
        help="write the pair into that env file instead of printing the private half",
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

    if args.write_env:
        write_env(args.write_env, {"VAPID_PUBLIC_KEY": public, "VAPID_PRIVATE_KEY": private})
        print(args.write_env + " updated. The private key was not displayed.")
        print("Public key: " + public)
        print()
        print("For Railway, copy both values out of that file.")
    else:
        print("VAPID_PUBLIC_KEY=" + public)
        print("VAPID_PRIVATE_KEY=" + private)
        print()

    print("Set both on every Railway service, API and cron alike.")
    print("Back the private key up like JWT_SECRET_KEY: losing it silently")
    print("invalidates every subscription browsers have already stored.")


if __name__ == "__main__":
    main()
