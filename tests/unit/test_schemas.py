import pytest
from pydantic import ValidationError

from models.schemas.auth_schema import UserCreate, UserLogin, VerifyEmailRequest

pytestmark = pytest.mark.unit


class TestUserCreate:
    def test_valid_payload_is_accepted(self):
        user = UserCreate(first_name="Alexis", email="alexis@example.com", password="MotDePasse1!")
        assert user.email == "alexis@example.com"

    @pytest.mark.parametrize(
        ("password", "reason"),
        [
            ("Court1!", "moins de 8 caracteres"),
            ("motdepasse1!", "pas de majuscule"),
            ("MOTDEPASSE1!", "pas de minuscule"),
            ("MotDePasse11", "pas de caractere special"),
        ],
    )
    def test_weak_passwords_are_rejected(self, password, reason):
        with pytest.raises(ValidationError):
            UserCreate(first_name="Alexis", email="alexis@example.com", password=password)

    @pytest.mark.parametrize("email", ["pas-un-email", "a@", "@example.com", ""])
    def test_invalid_emails_are_rejected(self, email):
        with pytest.raises(ValidationError):
            UserCreate(first_name="Alexis", email=email, password="MotDePasse1!")

    def test_empty_first_name_is_rejected(self):
        with pytest.raises(ValidationError):
            UserCreate(first_name="", email="alexis@example.com", password="MotDePasse1!")

    def test_eight_characters_is_the_lower_bound(self):
        UserCreate(first_name="A", email="a@example.com", password="MotDeP1!")
        with pytest.raises(ValidationError):
            UserCreate(first_name="A", email="a@example.com", password="MotDe1!")


class TestUserLogin:
    def test_valid_payload_is_accepted(self):
        assert UserLogin(email="alexis@example.com", password="peu-importe").password == "peu-importe"

    def test_login_does_not_enforce_password_policy(self):
        UserLogin(email="alexis@example.com", password="a")


class TestVerifyEmailRequest:
    def test_six_digit_code_is_accepted(self):
        assert VerifyEmailRequest(email="a@example.com", code="123456").code == "123456"

    @pytest.mark.parametrize("code", ["12345", "1234567", "abcdef", "12345a", ""])
    def test_malformed_codes_are_rejected(self, code):
        with pytest.raises(ValidationError):
            VerifyEmailRequest(email="a@example.com", code=code)
