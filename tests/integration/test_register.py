import pytest

pytestmark = pytest.mark.integration


def test_register_returns_201_and_a_message(client, credentials):
    response = client.post("/v1/auth/register", json=credentials)
    assert response.status_code == 201
    assert "message" in response.json()


def test_register_persists_an_unverified_user(client, credentials, db):
    client.post("/v1/auth/register", json=credentials)
    rows = db("select email, is_verified, role from users where email = :e", e=credentials["email"])
    assert rows == [(credentials["email"], False, "user")]


def test_register_never_stores_the_password_in_clear(client, credentials, db):
    client.post("/v1/auth/register", json=credentials)
    [(stored,)] = db("select password_hash from users where email = :e", e=credentials["email"])
    assert credentials["password"] not in stored
    assert stored.startswith("$argon2")


def test_register_sends_a_six_digit_code_to_the_right_address(client, credentials, mailbox):
    client.post("/v1/auth/register", json=credentials)
    assert len(mailbox) == 1
    assert mailbox[0]["kind"] == "verification"
    assert mailbox[0]["to"] == credentials["email"]
    assert mailbox[0]["code"].isdigit() and len(mailbox[0]["code"]) == 6


def test_register_stores_the_code_hashed_not_in_clear(client, credentials, mailbox, db):
    client.post("/v1/auth/register", json=credentials)
    [(stored,)] = db("select verification_code_hash from users where email = :e", e=credentials["email"])
    assert stored != mailbox[0]["code"]
    assert len(stored) == 64


def test_duplicate_email_is_rejected(client, credentials):
    client.post("/v1/auth/register", json=credentials)
    response = client.post("/v1/auth/register", json=credentials)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "EMAIL_ALREADY_REGISTERED"


def test_duplicate_detection_ignores_case(client, credentials):
    client.post("/v1/auth/register", json=credentials)
    response = client.post("/v1/auth/register", json={**credentials, "email": "ALEXIS@EXAMPLE.COM"})
    assert response.status_code == 409


def test_email_is_normalized_before_storage(client, credentials, db):
    client.post("/v1/auth/register", json={**credentials, "email": "  Alexis@Example.COM "})
    rows = db("select email from users")
    assert rows == [("alexis@example.com",)]


def test_disposable_email_is_rejected(client, credentials):
    response = client.post("/v1/auth/register", json={**credentials, "email": "jetable@yopmail.com"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "DISPOSABLE_EMAIL_NOT_ALLOWED"


def test_disposable_email_creates_no_user(client, credentials, db):
    client.post("/v1/auth/register", json={**credentials, "email": "jetable@yopmail.com"})
    assert db("select count(*) from users") == [(0,)]


@pytest.mark.parametrize("password", ["court1!", "motdepasse1!", "MOTDEPASSE1!", "MotDePasse11"])
def test_weak_password_is_rejected(client, credentials, password):
    response = client.post("/v1/auth/register", json={**credentials, "password": password})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"


def test_invalid_email_is_rejected(client, credentials):
    response = client.post("/v1/auth/register", json={**credentials, "email": "pas-un-email"})
    assert response.status_code == 422


def test_missing_field_is_rejected(client):
    response = client.post("/v1/auth/register", json={"email": "a@example.com"})
    assert response.status_code == 422


def test_failed_registration_sends_no_email(client, credentials, mailbox):
    client.post("/v1/auth/register", json={**credentials, "password": "faible"})
    assert mailbox == []


class TestLocale:
    def test_defaults_to_french(self, client, db):
        payload = {"first_name": "Alexis", "email": "loc@example.com", "password": "MotDePasse1!"}
        assert client.post("/v1/auth/register", json=payload).status_code == 201

        rows = db("SELECT locale FROM users WHERE email = :email", email=payload["email"])
        assert rows[0][0] == "fr"

    def test_keeps_the_language_chosen_at_signup(self, client, db):
        payload = {
            "first_name": "Sophie",
            "email": "loc2@example.com",
            "password": "MotDePasse1!",
            "locale": "en",
        }
        assert client.post("/v1/auth/register", json=payload).status_code == 201

        rows = db("SELECT locale FROM users WHERE email = :email", email=payload["email"])
        assert rows[0][0] == "en"

    def test_refuses_an_unsupported_language(self, client):
        payload = {
            "first_name": "Marc",
            "email": "loc3@example.com",
            "password": "MotDePasse1!",
            "locale": "de",
        }
        assert client.post("/v1/auth/register", json=payload).status_code == 422


class TestRegistrationCurrency:
    def test_currency_defaults_to_cad_when_omitted(self, client, credentials, db):
        client.post("/v1/auth/register", json=credentials)
        assert db("select currency from users where email = :e", e=credentials["email"]) == [("CAD",)]

    def test_chosen_currency_is_persisted(self, client, credentials, db):
        client.post("/v1/auth/register", json={**credentials, "currency": "EUR"})
        assert db("select currency from users where email = :e", e=credentials["email"]) == [("EUR",)]

    def test_currency_is_upper_cased(self, client, credentials, db):
        client.post("/v1/auth/register", json={**credentials, "currency": "usd"})
        assert db("select currency from users where email = :e", e=credentials["email"]) == [("USD",)]

    @pytest.mark.parametrize("currency", ["EU", "EURO", "12A", "", "US$"])
    def test_invalid_currency_is_rejected(self, client, credentials, currency):
        response = client.post("/v1/auth/register", json={**credentials, "currency": currency})
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "VALIDATION_ERROR"

    def test_invalid_currency_creates_no_user(self, client, credentials, db):
        client.post("/v1/auth/register", json={**credentials, "currency": "EURO"})
        assert db("select count(*) from users") == [(0,)]

    def test_currency_is_returned_by_the_profile_route(self, client, credentials, mailbox):
        client.post("/v1/auth/register", json={**credentials, "currency": "chf"})
        tokens = client.post(
            "/v1/auth/verify-email",
            json={"email": credentials["email"], "code": mailbox[0]["code"]},
        ).json()
        me = client.get(
            "/v1/users/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
        assert me.json()["currency"] == "CHF"
