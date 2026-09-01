import hashlib
import secrets
import uuid

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError
from jose import jwt, JWTError
from starlette.concurrency import run_in_threadpool
from datetime import datetime, timedelta, timezone
from core.config import JWT_SECRET_KEY, JWT_ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES, REFRESH_TOKEN_EXPIRE_DAYS

# Argon2 hache ce qu'on lui donne, aussi long soit-il : borner l'entrée évite
# qu'une requête de quelques mégaoctets occupe un fil de calcul.
MAX_PASSWORD_LENGTH = 128

ph = PasswordHasher()

_DUMMY_HASH = ph.hash("dummy-password-for-constant-time-login")


class Security:

    @staticmethod
    def hash_password(password: str) -> str:
        return ph.hash(password)

    @staticmethod
    def verify_password(hashed: str | None, plain: str) -> bool:
        if not hashed:
            Security.dummy_verify()
            return False
        try:
            return ph.verify(hashed, plain)
        except (Argon2Error, InvalidHashError):
            return False

    @staticmethod
    def dummy_verify() -> None:
        try:
            ph.verify(_DUMMY_HASH, "wrong")
        except (Argon2Error, InvalidHashError):
            pass

    @staticmethod
    async def hash_password_async(password: str) -> str:
        return await run_in_threadpool(Security.hash_password, password)

    @staticmethod
    async def verify_password_async(hashed: str | None, plain: str) -> bool:
        return await run_in_threadpool(Security.verify_password, hashed, plain)

    @staticmethod
    async def dummy_verify_async() -> None:
        await run_in_threadpool(Security.dummy_verify)

    @staticmethod
    def needs_rehash(hashed: str) -> bool:
        try:
            return ph.check_needs_rehash(hashed)
        except (Argon2Error, InvalidHashError):
            return False

    @staticmethod
    def verify_otp(code: str, stored_hash: str | None) -> bool:
        if not stored_hash:
            return False
        return secrets.compare_digest(Security.hash_token(code), stored_hash)

    @staticmethod
    def create_access_token(user_id: str, role: str = "user") -> str:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        payload = {"sub": user_id, "exp": expire, "type": "access", "role": role}
        return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    
    @staticmethod
    def create_refresh_token(user_id: str) -> str:
        expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        payload = {"sub": user_id, "exp": expire, "type": "refresh", "jti": str(uuid.uuid4())}
        return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    
    
    @staticmethod
    def decode_token(token: str) -> dict:
        try:
            payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
            return payload
        except JWTError:
            return None

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def generate_otp_code() -> str:
        return str(secrets.randbelow(900000) + 100000)