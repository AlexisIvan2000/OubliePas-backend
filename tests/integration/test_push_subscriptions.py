import pytest

pytestmark = pytest.mark.integration

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def token_of(user):
    return user["tokens"]["access_token"]


def subscription(endpoint=ENDPOINT, **overrides):
    return {"endpoint": endpoint, "p256dh": "cle-publique", "auth": "secret", **overrides}


@pytest.fixture
def other_token(client, mailbox):
    payload = {"first_name": "Sophie", "email": "sophie@example.com", "password": "MotDePasse1!"}
    assert client.post("/v1/auth/register", json=payload).status_code == 201
    response = client.post(
        "/v1/auth/verify-email", json={"email": payload["email"], "code": mailbox[-1]["code"]}
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def subscribe(client, user, **overrides):
    return client.post(
        "/v1/push/subscriptions", json=subscription(**overrides), headers=auth(token_of(user))
    )


class TestPublicKey:
    def test_it_is_null_when_the_server_has_no_pair(self, client, verified):
        # Nul plutot qu'absent : le client distingue « le push n'est pas
        # installe ici » d'une panne.
        response = client.get("/v1/push/key", headers=auth(token_of(verified)))

        assert response.status_code == 200
        assert response.json() == {"public_key": None}

    def test_it_carries_the_key_once_configured(self, client, verified, monkeypatch):
        import api.v1.client.push as route

        monkeypatch.setattr(route, "VAPID_PUBLIC_KEY", "BFakePublicKey")
        monkeypatch.setattr(route, "push_configured", lambda: True)

        body = client.get("/v1/push/key", headers=auth(token_of(verified))).json()

        assert body["public_key"] == "BFakePublicKey"

    def test_it_needs_an_account(self, client):
        assert client.get("/v1/push/key").status_code == 401


class TestSubscribe:
    def test_it_records_the_subscription(self, client, verified, db):
        response = subscribe(client, verified)

        assert response.status_code == 201
        assert response.json()["endpoint"] == ENDPOINT
        rows = db("select endpoint, p256dh, auth from push_subscriptions")
        assert [tuple(row) for row in rows] == [(ENDPOINT, "cle-publique", "secret")]

    def test_the_same_device_never_doubles(self, client, verified, db):
        # Un navigateur reconduit la meme adresse a chaque reabonnement.
        subscribe(client, verified)
        subscribe(client, verified, p256dh="cle-renouvelee")

        rows = db("select endpoint, p256dh from push_subscriptions")
        assert [tuple(row) for row in rows] == [(ENDPOINT, "cle-renouvelee")]

    def test_a_second_device_is_a_second_line(self, client, verified, db):
        subscribe(client, verified)
        subscribe(client, verified, endpoint=ENDPOINT + "-tablette")

        assert len(db("select id from push_subscriptions")) == 2

    def test_a_device_can_change_hands(self, client, verified, other_token, db):
        # Deux comptes sur le meme telephone : le second abonnement remplace le
        # premier, sinon les rappels partiraient a l'ancien proprietaire.
        subscribe(client, verified)
        premier = db("select user_id from push_subscriptions")[0][0]

        client.post(
            "/v1/push/subscriptions",
            json=subscription(),
            headers={"Authorization": f"Bearer {other_token}"},
        )

        rows = db("select user_id from push_subscriptions")
        assert len(rows) == 1
        assert rows[0][0] != premier

    def test_it_refuses_an_unknown_field(self, client, verified):
        response = client.post(
            "/v1/push/subscriptions",
            json={**subscription(), "expirationTime": None},
            headers=auth(token_of(verified)),
        )

        assert response.status_code == 422

    @pytest.mark.parametrize("missing", ["endpoint", "p256dh", "auth"])
    def test_every_part_is_required(self, client, verified, missing):
        payload = subscription()
        payload.pop(missing)

        response = client.post(
            "/v1/push/subscriptions", json=payload, headers=auth(token_of(verified))
        )

        assert response.status_code == 422

    def test_it_needs_an_account(self, client):
        assert client.post("/v1/push/subscriptions", json=subscription()).status_code == 401


class TestAnAddressWeWillNeverCall:
    @pytest.mark.parametrize(
        "endpoint",
        [
            "https://169.254.169.254/latest/meta-data/",
            "https://redis.railway.internal/",
            "https://fcm.googleapis.com:6379/",
            "http://fcm.googleapis.com/fcm/send/x",
            "https://exemple.test/collecteur",
            "https://fcm.googleapis.com@redis.railway.internal/",
        ],
    )
    def test_it_is_refused_before_reaching_the_table(self, client, verified, db, endpoint):
        # Sans ce refus, /push/test faisait poster le serveur a l'adresse
        # donnee, et l'etat de la reponse revenait a l'appelant : de quoi
        # sonder un reseau prive depuis un compte ordinaire.
        response = subscribe(client, verified, endpoint=endpoint)

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "PUSH_ENDPOINT_REFUSED"
        assert db("select id from push_subscriptions") == []

    def test_the_refusal_names_the_host_and_never_the_address(self, client, verified, caplog):
        import logging

        endpoint = "https://exemple.test/collecteur?jeton=secret-de-la-victime"

        with caplog.at_level(logging.WARNING):
            subscribe(client, verified, endpoint=endpoint)

        journal = " ".join(record.getMessage() for record in caplog.records)
        assert "exemple.test" in journal
        assert "secret-de-la-victime" not in journal

    def test_a_real_push_service_still_goes_through(self, client, verified):
        assert subscribe(client, verified).status_code == 201


class TestUnsubscribe:
    def test_it_removes_the_line(self, client, verified, db):
        subscribe(client, verified)

        response = client.request(
            "DELETE",
            "/v1/push/subscriptions",
            json={"endpoint": ENDPOINT},
            headers=auth(token_of(verified)),
        )

        assert response.status_code == 200
        assert db("select id from push_subscriptions") == []

    def test_unsubscribing_twice_is_not_an_error(self, client, verified):
        # Le second appel obtient deja ce qu'il voulait : signaler une erreur
        # ferait echouer un client qui a raison.
        subscribe(client, verified)
        payload = {"endpoint": ENDPOINT}
        headers = auth(token_of(verified))

        client.request("DELETE", "/v1/push/subscriptions", json=payload, headers=headers)
        again = client.request("DELETE", "/v1/push/subscriptions", json=payload, headers=headers)

        assert again.status_code == 200

    def test_it_never_removes_someone_elses(self, client, verified, other_token, db):
        subscribe(client, verified)

        client.request(
            "DELETE",
            "/v1/push/subscriptions",
            json={"endpoint": ENDPOINT},
            headers=auth(other_token),
        )

        assert len(db("select id from push_subscriptions")) == 1


class TestTheTestNotification:
    @pytest.fixture
    def configured(self, monkeypatch):
        import api.v1.client.push as route

        monkeypatch.setattr(route, "push_configured", lambda: True)

    def test_it_goes_through_the_push_service(self, client, verified, pushbox, configured):
        # Fabriquer la notification dans le navigateur la ferait apparaitre meme
        # si rien n'etait joignable : l'interrupteur passerait au vert sans
        # qu'aucun rappel ne puisse jamais arriver.
        subscribe(client, verified)

        response = client.post(
            "/v1/push/test", json={"endpoint": ENDPOINT}, headers=auth(token_of(verified))
        )

        assert response.status_code == 200
        assert [entry["endpoint"] for entry in pushbox] == [ENDPOINT]

    def test_it_leads_back_to_the_reminders_page(self, client, verified, pushbox, configured):
        subscribe(client, verified)

        client.post(
            "/v1/push/test", json={"endpoint": ENDPOINT}, headers=auth(token_of(verified))
        )

        assert pushbox[0]["url"].endswith("/rappels")

    def test_it_refuses_when_the_server_has_no_pair(self, client, verified, pushbox):
        subscribe(client, verified)

        response = client.post(
            "/v1/push/test", json={"endpoint": ENDPOINT}, headers=auth(token_of(verified))
        )

        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "PUSH_NOT_CONFIGURED"
        assert pushbox == []

    def test_it_never_reaches_someone_elses_device(
        self, client, verified, other_token, pushbox, configured
    ):
        subscribe(client, verified)

        response = client.post(
            "/v1/push/test", json={"endpoint": ENDPOINT}, headers=auth(other_token)
        )

        assert response.status_code == 410
        assert pushbox == []

    def test_a_dead_device_is_forgotten(self, client, verified, pushbox, configured, db):
        subscribe(client, verified)
        pushbox.result = "gone"

        response = client.post(
            "/v1/push/test", json={"endpoint": ENDPOINT}, headers=auth(token_of(verified))
        )

        # L'erreur remonte, mais l'effacement doit persister : sinon chaque
        # essai suivant repartirait vers la meme adresse morte.
        assert response.json()["detail"]["code"] == "PUSH_SUBSCRIPTION_GONE"
        assert db("select id from push_subscriptions") == []

    def test_it_needs_an_account(self, client):
        assert client.post("/v1/push/test", json={"endpoint": ENDPOINT}).status_code == 401


class TestTheSwitch:
    def test_it_is_off_until_asked(self, client, verified):
        body = client.get("/v1/auth/me", headers=auth(token_of(verified))).json()

        assert body["reminder_push_enabled"] is False

    def test_it_can_be_turned_on(self, client, verified):
        client.patch(
            "/v1/users/me",
            json={"reminder_push_enabled": True},
            headers=auth(token_of(verified)),
        )

        body = client.get("/v1/auth/me", headers=auth(token_of(verified))).json()
        assert body["reminder_push_enabled"] is True

    def test_it_is_independent_from_email(self, client, verified):
        # Couper les notifications ne doit pas couper les rappels, et
        # inversement : ce sont deux canaux, pas deux reglages du meme.
        headers = auth(token_of(verified))
        client.patch(
            "/v1/users/me",
            json={"reminder_push_enabled": True, "reminder_email_enabled": False},
            headers=headers,
        )

        body = client.get("/v1/auth/me", headers=headers).json()
        assert body["reminder_push_enabled"] is True
        assert body["reminder_email_enabled"] is False
