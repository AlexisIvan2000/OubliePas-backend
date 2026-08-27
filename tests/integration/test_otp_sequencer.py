import pytest

pytestmark = pytest.mark.integration

FLOWS = ("verification", "reset", "email_change")


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def essais(db, email, kind):
    rows = db(
        "select a.count from verification_attempts a"
        " join users u on u.id = a.user_id"
        " where u.email = :e and a.kind = :k",
        e=email,
        k=kind,
    )
    return rows[0][0] if rows else 0


def expire(db, email, column):
    db(
        f"update users set {column} = now() - interval '1 hour' where email = :e",
        e=email,
    )


@pytest.fixture
def token(verified):
    return verified["tokens"]["access_token"]


class TestTheAttemptIsCountedBeforeTheCodeIsRead:
    # C'est la propriete de securite du sequenceur. Compte apres coup, un essai
    # qui echoue ne couterait rien : get_session valide malgre l'exception, donc
    # l'increment survit, mais seulement s'il a eu lieu avant le refus.

    def test_a_wrong_verification_code_costs_an_attempt(self, client, registered, db):
        client.post(
            "/v1/auth/verify-email", json={"email": registered["email"], "code": "000000"}
        )

        assert essais(db, registered["email"], "verification") == 1

    def test_an_expired_verification_code_costs_an_attempt_too(self, client, registered, db):
        # Le cas qui distingue les deux ordres : la sortie par expiration est
        # anterieure a la comparaison, donc elle ne compterait rien si
        # l'increment venait apres.
        expire(db, registered["email"], "verification_code_expires_at")

        response = client.post(
            "/v1/auth/verify-email",
            json={"email": registered["email"], "code": registered["code"]},
        )

        assert response.json()["detail"]["code"] == "VERIFICATION_CODE_EXPIRED"
        assert essais(db, registered["email"], "verification") == 1

    def test_an_expired_reset_code_costs_an_attempt(self, client, verified, db, mailbox):
        assert client.post(
            "/v1/auth/forgot-password", json={"email": verified["email"]}
        ).status_code == 200
        code = mailbox[-1]["code"]
        expire(db, verified["email"], "reset_code_expires_at")

        response = client.post(
            "/v1/auth/reset-password",
            json={"email": verified["email"], "code": code, "new_password": "AutreMot1!"},
        )

        assert response.json()["detail"]["code"] == "RESET_CODE_EXPIRED"
        assert essais(db, verified["email"], "reset") == 1

    def test_an_expired_email_change_code_costs_an_attempt(
        self, client, verified, token, db, mailbox
    ):
        client.post(
            "/v1/users/me/change-email",
            headers=auth(token),
            json={"new_email": "ailleurs@example.com", "password": verified["password"]},
        )
        code = mailbox[-1]["code"]
        expire(db, verified["email"], "email_change_code_expires_at")

        response = client.post(
            "/v1/users/me/confirm-email-change", headers=auth(token), json={"code": code}
        )

        assert response.json()["detail"]["code"] == "VERIFICATION_CODE_EXPIRED"
        assert essais(db, verified["email"], "email_change") == 1


class TestEachFlowKeepsItsOwnErrors:
    # Le sequenceur recoit ses exceptions en parametres : le front s'appuie sur
    # ces codes pour choisir son message, ils ne doivent pas se confondre.

    def test_the_reset_says_reset(self, client, verified):
        client.post("/v1/auth/forgot-password", json={"email": verified["email"]})

        response = client.post(
            "/v1/auth/reset-password",
            json={"email": verified["email"], "code": "000000", "new_password": "AutreMot1!"},
        )

        assert response.json()["detail"]["code"] == "INVALID_RESET_CODE"

    def test_the_verification_says_verification(self, client, registered):
        response = client.post(
            "/v1/auth/verify-email", json={"email": registered["email"], "code": "000000"}
        )

        assert response.json()["detail"]["code"] == "INVALID_VERIFICATION_CODE"

    def test_the_email_change_says_verification_too(self, client, verified, token):
        client.post(
            "/v1/users/me/change-email",
            headers=auth(token),
            json={"new_email": "ailleurs@example.com", "password": verified["password"]},
        )

        response = client.post(
            "/v1/users/me/confirm-email-change", headers=auth(token), json={"code": "000000"}
        )

        assert response.json()["detail"]["code"] == "INVALID_VERIFICATION_CODE"


class TestTheSequencerKnowsThreeFlows:
    def test_the_columns_table_covers_them_all(self):
        from services.emailing.otp_service import CODE_COLUMNS

        assert sorted(CODE_COLUMNS) == sorted(FLOWS)

    def test_every_flow_names_an_existing_column(self, db):
        from services.emailing.otp_service import CODE_COLUMNS

        colonnes = {
            row[0]
            for row in db("select column_name from information_schema.columns where table_name = 'users'")
        }

        for hash_column, expires_column in CODE_COLUMNS.values():
            assert hash_column in colonnes
            assert expires_column in colonnes
