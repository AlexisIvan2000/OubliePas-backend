import pytest

from models.schemas.user_schema import UpdateProfile

pytestmark = pytest.mark.integration


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def token(verified):
    return verified["tokens"]["access_token"]


class TestGetProfile:
    def test_returns_the_current_user(self, client, verified, token):
        response = client.get("/v1/users/me", headers=auth(token))
        assert response.status_code == 200
        body = response.json()
        assert body["email"] == verified["email"]
        assert body["first_name"] == verified["first_name"]
        assert body["currency"] == "CAD"

    def test_never_exposes_sensitive_fields(self, client, token):
        body = client.get("/v1/users/me", headers=auth(token)).json()
        for field in ("password_hash", "reset_code_hash", "email_change_code_hash", "admin_notes"):
            assert field not in body

    def test_requires_authentication(self, client):
        assert client.get("/v1/users/me").status_code == 401


class TestReminderSwitch:
    def test_is_on_for_a_new_account(self, client, token):
        body = client.get("/v1/users/me", headers=auth(token)).json()
        assert body["reminder_email_enabled"] is True

    def test_can_be_switched_off(self, client, token, db, verified):
        response = client.patch(
            "/v1/users/me",
            json={"reminder_email_enabled": False},
            headers=auth(token),
        )
        assert response.status_code == 200

        body = client.get("/v1/users/me", headers=auth(token)).json()
        assert body["reminder_email_enabled"] is False

        rows = db(
            "SELECT reminder_email_enabled FROM users WHERE email = :email",
            email=verified["email"],
        )
        assert rows[0][0] is False

    def test_can_be_switched_back_on(self, client, token):
        client.patch(
            "/v1/users/me", json={"reminder_email_enabled": False}, headers=auth(token)
        )
        client.patch(
            "/v1/users/me", json={"reminder_email_enabled": True}, headers=auth(token)
        )

        body = client.get("/v1/users/me", headers=auth(token)).json()
        assert body["reminder_email_enabled"] is True

    def test_switching_it_off_leaves_the_rest_alone(self, client, token, verified):
        client.patch(
            "/v1/users/me", json={"reminder_email_enabled": False}, headers=auth(token)
        )

        body = client.get("/v1/users/me", headers=auth(token)).json()
        assert body["first_name"] == verified["first_name"]
        assert body["currency"] == "CAD"

    def test_another_account_is_untouched(self, client, token, db, verified):
        client.patch(
            "/v1/users/me", json={"reminder_email_enabled": False}, headers=auth(token)
        )

        rows = db(
            "SELECT count(*) FROM users WHERE reminder_email_enabled IS false"
        )
        assert rows[0][0] == 1


class TestDefaultReminderDays:
    def test_starts_at_three_days(self, client, token):
        body = client.get("/v1/users/me", headers=auth(token)).json()
        assert body["default_reminder_days"] == 3

    def test_can_be_changed(self, client, token):
        assert (
            client.patch(
                "/v1/users/me", json={"default_reminder_days": 7}, headers=auth(token)
            ).status_code
            == 200
        )
        body = client.get("/v1/users/me", headers=auth(token)).json()
        assert body["default_reminder_days"] == 7

    def test_same_day_is_allowed(self, client, token):
        client.patch("/v1/users/me", json={"default_reminder_days": 0}, headers=auth(token))
        body = client.get("/v1/users/me", headers=auth(token)).json()
        assert body["default_reminder_days"] == 0

    def test_refuses_more_than_a_month(self, client, token):
        response = client.patch(
            "/v1/users/me", json={"default_reminder_days": 31}, headers=auth(token)
        )
        assert response.status_code == 422

    def test_refuses_a_negative_delay(self, client, token):
        response = client.patch(
            "/v1/users/me", json={"default_reminder_days": -1}, headers=auth(token)
        )
        assert response.status_code == 422


class TestUpdateProfile:
    def test_updates_the_given_fields(self, client, token, db, verified):
        response = client.patch(
            "/v1/users/me", headers=auth(token), json={"last_name": "Kombou", "currency": "usd"}
        )
        assert response.status_code == 200
        assert db("select last_name, currency from users where email = :e", e=verified["email"]) == [
            ("Kombou", "USD")
        ]

    def test_leaves_untouched_fields_alone(self, client, token, verified):
        client.patch("/v1/users/me", headers=auth(token), json={"last_name": "Kombou"})
        body = client.get("/v1/users/me", headers=auth(token)).json()
        assert body["first_name"] == verified["first_name"]
        assert body["currency"] == "CAD"

    def test_explicit_null_clears_a_nullable_field(self, client, token, db, verified):
        client.patch("/v1/users/me", headers=auth(token), json={"last_name": "Kombou"})
        response = client.patch("/v1/users/me", headers=auth(token), json={"last_name": None})
        assert response.status_code == 200
        assert db("select last_name from users where email = :e", e=verified["email"]) == [(None,)]

    def test_null_on_a_required_field_is_ignored(self, client, token, verified):
        response = client.patch("/v1/users/me", headers=auth(token), json={"first_name": None})
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "NO_FIELDS_TO_UPDATE"
        assert client.get("/v1/users/me", headers=auth(token)).json()["first_name"] == verified["first_name"]

    def test_empty_payload_is_rejected(self, client, token):
        response = client.patch("/v1/users/me", headers=auth(token), json={})
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "NO_FIELDS_TO_UPDATE"

    def test_cannot_change_email_through_this_route(self, client, token, verified):
        client.patch("/v1/users/me", headers=auth(token), json={"email": "pirate@example.com"})
        assert client.get("/v1/users/me", headers=auth(token)).json()["email"] == verified["email"]

    def test_cannot_escalate_role(self, client, token, db, verified):
        client.patch("/v1/users/me", headers=auth(token), json={"role": "admin"})
        assert db("select role from users where email = :e", e=verified["email"]) == [("user",)]

    def test_cannot_flip_is_verified(self, client, token, db, verified):
        client.patch("/v1/users/me", headers=auth(token), json={"is_verified": False})
        assert db("select is_verified from users where email = :e", e=verified["email"]) == [(True,)]

    @pytest.mark.parametrize("currency", ["EU", "EURO", "12A", ""])
    def test_invalid_currency_is_rejected(self, client, token, currency):
        response = client.patch("/v1/users/me", headers=auth(token), json={"currency": currency})
        assert response.status_code == 422


class TestAvatarUrlIsNotWritable:
    # Le champ portait la photo Google et restait ouvert au client : il
    # contournait le plafond de 9 Mo, le nettoyeur d'image et le stockage, et
    # faisait fuir l'adresse IP du visiteur vers le serveur de son choix. Le
    # 422 est desormais la specification, pour toute URL, sure ou non.
    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "file:///etc/passwd",
            "ftp://evil.com/a.png",
            "pas-une-url",
            "avatars/8f3a-portrait.png",
            "https://cdn.x.com/a.png",
            "https://cdn.x.com/" + "a" * 2100,
        ],
    )
    def test_no_url_can_be_posted_on_the_profile(self, client, token, url):
        response = client.patch("/v1/users/me", headers=auth(token), json={"avatar_url": url})

        assert response.status_code == 422

    def test_a_photo_written_by_the_server_survives_the_attempt(
        self, client, token, db, verified
    ):
        db(
            "update users set avatar_url = 'https://lh3.google.test/photo' where email = :e",
            e=verified["email"],
        )

        client.patch("/v1/users/me", headers=auth(token), json={"avatar_url": None})

        assert db("select avatar_url from users where email = :e", e=verified["email"]) == [
            ("https://lh3.google.test/photo",)
        ]

    def test_an_unknown_field_is_refused_rather_than_ignored(self, client, token):
        response = client.patch("/v1/users/me", headers=auth(token), json={"favorite_color": "bleu"})

        assert response.status_code == 422


class TestProfileFieldLimits:
    def test_too_long_first_name_is_rejected(self, client, token):
        response = client.patch("/v1/users/me", headers=auth(token), json={"first_name": "a" * 101})
        assert response.status_code == 422

    def test_empty_first_name_is_rejected(self, client, token):
        response = client.patch("/v1/users/me", headers=auth(token), json={"first_name": ""})
        assert response.status_code == 422

    def test_requires_authentication(self, client):
        assert client.patch("/v1/users/me", json={"last_name": "X"}).status_code == 401

    def test_disabled_account_is_rejected(self, client, token, db, verified):
        db("update users set is_active = false where email = :e", e=verified["email"])
        response = client.patch("/v1/users/me", headers=auth(token), json={"last_name": "X"})
        assert response.status_code == 403


class TestEveryPreferenceComesBack:
    """La reponse de profil est batie champ par champ dans api/responses.py.

    Ajouter une preference a la table et au schema sans l'ajouter la produit une
    reponse qui rend eternellement le defaut : l'ecriture passe, la relecture
    ment, et l'interrupteur retombe sous les doigts sans qu'aucune erreur ne
    s'affiche. C'est arrive avec reminder_weekly_enabled.
    """

    PREFERENCES = sorted(
        nom for nom in UpdateProfile.model_fields if nom.startswith("reminder_")
    )

    @pytest.mark.parametrize("champ", PREFERENCES)
    def test_what_was_written_is_what_is_read(self, client, token, champ):
        actuel = client.get("/v1/users/me", headers=auth(token)).json()[champ]

        client.patch("/v1/users/me", json={champ: not actuel}, headers=auth(token))

        relu = client.get("/v1/users/me", headers=auth(token)).json()[champ]
        assert relu is (not actuel), f"{champ} n'est pas rendu par api/responses.py"

    def test_the_list_is_not_empty(self):
        # Une regex qui ne trouve plus rien ferait passer la garde a vide.
        assert len(self.PREFERENCES) >= 5

    # Le prefixe « reminder_ » ne couvrait que les interrupteurs. Un champ
    # ajoute au schema et a la table, mais pas au constructeur de reponse, se
    # lit toujours a sa valeur par defaut, quel que soit son nom : la garde
    # regarde donc tout ce qu'on peut ecrire.
    ECRIVABLES = sorted(UpdateProfile.model_fields)

    @pytest.mark.parametrize("champ", ECRIVABLES)
    def test_every_writable_field_is_rendered(self, client, token, champ):
        rendu = client.get("/v1/users/me", headers=auth(token)).json()

        assert champ in rendu, (
            f"{champ} s'ecrit mais ne se relit pas : api/responses.py l'a oublie"
        )
