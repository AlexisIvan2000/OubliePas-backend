from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt

from core.config import JWT_ALGORITHM, JWT_SECRET_KEY
from core.security import Security

pytestmark = pytest.mark.unit


class TestPasswordHashing:
    def test_hash_is_not_plaintext(self):
        hashed = Security.hash_password("MotDePasse1!")
        assert hashed != "MotDePasse1!"
        assert hashed.startswith("$argon2")

    def test_same_password_gives_different_hashes(self):
        assert Security.hash_password("MotDePasse1!") != Security.hash_password("MotDePasse1!")

    def test_verify_accepts_correct_password(self):
        hashed = Security.hash_password("MotDePasse1!")
        assert Security.verify_password(hashed, "MotDePasse1!") is True

    def test_verify_rejects_wrong_password(self):
        hashed = Security.hash_password("MotDePasse1!")
        assert Security.verify_password(hashed, "Mauvais1!") is False

    @pytest.mark.parametrize("bad_hash", [None, "", "pas-un-hash", "$argon2id$tronque"])
    def test_verify_returns_false_instead_of_raising(self, bad_hash):
        assert Security.verify_password(bad_hash, "MotDePasse1!") is False

    def test_needs_rehash_is_false_for_fresh_hash(self):
        assert Security.needs_rehash(Security.hash_password("MotDePasse1!")) is False

    def test_needs_rehash_swallows_invalid_hash(self):
        assert Security.needs_rehash("pas-un-hash") is False


class TestAccessToken:
    def test_roundtrip_keeps_subject_and_role(self):
        token = Security.create_access_token("user-42", role="admin")
        payload = Security.decode_token(token)
        assert payload["sub"] == "user-42"
        assert payload["role"] == "admin"
        assert payload["type"] == "access"

    def test_default_role_is_user(self):
        payload = Security.decode_token(Security.create_access_token("user-42"))
        assert payload["role"] == "user"

    def test_expiry_is_in_the_future(self):
        payload = Security.decode_token(Security.create_access_token("user-42"))
        assert payload["exp"] > datetime.now(timezone.utc).timestamp()


class TestRefreshToken:
    def test_is_typed_as_refresh(self):
        payload = Security.decode_token(Security.create_refresh_token("user-42"))
        assert payload["type"] == "refresh"
        assert payload["sub"] == "user-42"

    def test_each_token_has_a_unique_jti(self):
        first = Security.decode_token(Security.create_refresh_token("user-42"))
        second = Security.decode_token(Security.create_refresh_token("user-42"))
        assert first["jti"] != second["jti"]

    def test_lives_longer_than_access_token(self):
        access = Security.decode_token(Security.create_access_token("user-42"))
        refresh = Security.decode_token(Security.create_refresh_token("user-42"))
        assert refresh["exp"] > access["exp"]


class TestDecodeToken:
    @pytest.mark.parametrize("token", ["", "garbage", "a.b.c"])
    def test_malformed_token_returns_none(self, token):
        assert Security.decode_token(token) is None

    def test_expired_token_returns_none(self):
        expired = jwt.encode(
            {"sub": "user-42", "exp": datetime.now(timezone.utc) - timedelta(minutes=1), "type": "access"},
            JWT_SECRET_KEY,
            algorithm=JWT_ALGORITHM,
        )
        assert Security.decode_token(expired) is None

    def test_token_signed_with_another_key_returns_none(self):
        forged = jwt.encode(
            {"sub": "attaquant", "exp": datetime.now(timezone.utc) + timedelta(hours=1), "type": "access"},
            "mauvaise-cle-secrete",
            algorithm=JWT_ALGORITHM,
        )
        assert Security.decode_token(forged) is None


class TestTokenHashing:
    def test_is_deterministic(self):
        assert Security.hash_token("abc") == Security.hash_token("abc")

    def test_produces_sha256_hex(self):
        digest = Security.hash_token("abc")
        assert len(digest) == 64
        assert set(digest) <= set("0123456789abcdef")

    def test_different_inputs_give_different_digests(self):
        assert Security.hash_token("abc") != Security.hash_token("abd")


class TestOtp:
    def test_code_is_six_digits(self):
        for _ in range(50):
            code = Security.generate_otp_code()
            assert isinstance(code, str)
            assert len(code) == 6
            assert code.isdigit()

    def test_code_never_starts_with_zero(self):
        for _ in range(50):
            assert 100000 <= int(Security.generate_otp_code()) <= 999999

    def test_codes_are_not_all_identical(self):
        assert len({Security.generate_otp_code() for _ in range(50)}) > 1

    def test_verify_otp_accepts_matching_code(self):
        assert Security.verify_otp("123456", Security.hash_token("123456")) is True

    def test_verify_otp_rejects_wrong_code(self):
        assert Security.verify_otp("000000", Security.hash_token("123456")) is False

    def test_verify_otp_rejects_missing_hash(self):
        assert Security.verify_otp("123456", None) is False
        assert Security.verify_otp("123456", "") is False
