from datetime import date, datetime, timezone

import pytest

import core.clock as horloge

pytestmark = pytest.mark.integration

MONCTON = "America/Moncton"
TOKYO = "Asia/Tokyo"

# Les deux instants des captures du 31 aout, dits en UTC. A 22 h 10 en
# Atlantique il est deja 1 h 10 le lendemain a Greenwich : c'est tout le defaut.
LE_31_AOUT_22H10_A_MONCTON = datetime(2026, 9, 1, 1, 10, tzinfo=timezone.utc)
LE_1ER_SEPTEMBRE_8H_A_MONCTON = datetime(2026, 9, 1, 11, 0, tzinfo=timezone.utc)

# Le symetrique : a 8 h le 1er septembre a Tokyo, le serveur est encore en aout.
LE_1ER_SEPTEMBRE_8H_A_TOKYO = datetime(2026, 8, 31, 23, 0, tzinfo=timezone.utc)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def fige(monkeypatch):
    """Gele l'unique lecture d'horloge du projet."""

    def a(instant):
        monkeypatch.setattr(horloge, "_now", lambda: instant)
        return instant

    return a


@pytest.fixture
def compte(client, mailbox):
    def creer(fuseau, email="fuseau@example.com"):
        payload = {
            "first_name": "Alexis",
            "email": email,
            "password": "MotDePasse1!",
            "timezone": fuseau,
        }
        assert client.post("/v1/auth/register", json=payload).status_code == 201
        reponse = client.post(
            "/v1/auth/verify-email",
            json={"email": email, "code": mailbox[-1]["code"]},
        )
        assert reponse.status_code == 200
        return reponse.json()["access_token"]

    return creer


def facture(client, token, titre, jour, montant="100.00", categorie="energy"):
    reponse = client.post(
        "/v1/commitments",
        json={
            "title": titre,
            "type": "invoice",
            "category": categorie,
            "amount": montant,
            "frequency": "oneoff",
            "starts_on": jour.isoformat(),
        },
        headers=auth(token),
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def resume(client, token):
    reponse = client.get("/v1/commitments/summary", headers=auth(token))
    assert reponse.status_code == 200
    return reponse.json()


class TestTheEveningOfTheThirtyFirst:
    """Le cas exact des captures : meme compte, meme instant, deux ecrans qui
    montraient deux mois. Le tableau de bord tirait son resume d'un serveur en
    UTC — deja septembre — pendant que la page voisine comptait en heure locale."""

    def test_moncton_still_sees_august(self, client, compte, fige):
        token = compte(MONCTON)
        fige(LE_31_AOUT_22H10_A_MONCTON)
        facture(client, token, "Electricite", date(2026, 8, 31))

        assert resume(client, token)["month"] == "2026-08"

    def test_and_that_evening_bill_is_due_today_not_late(self, client, compte, fige):
        # 22 h 10 le 31, une echeance du 31 : elle est du jour. Un serveur en UTC
        # la donnait pour hier, donc en retard, avec la pastille qui va avec.
        token = compte(MONCTON)
        fige(LE_31_AOUT_22H10_A_MONCTON)
        facture(client, token, "Electricite", date(2026, 8, 31))

        corps = resume(client, token)
        echeance = corps["upcoming"][0]
        assert echeance["due_date"] == "2026-08-31"
        assert echeance["is_late"] is False
        assert corps["late_count"] == 0

    def test_the_breakdown_of_that_evening_holds_only_august(self, client, compte, fige):
        token = compte(MONCTON)
        fige(LE_31_AOUT_22H10_A_MONCTON)
        facture(client, token, "Electricite", date(2026, 8, 31), "100.00", "energy")
        facture(client, token, "Loyer", date(2026, 9, 1), "700.00", "housing")

        corps = resume(client, token)
        categories = {ligne["category"]: ligne["total"] for ligne in corps["by_category"]}

        assert categories == {"energy": "100.00"}
        assert corps["month_total"] == "100.00"


class TestTheMorningOfTheFirst:
    def test_moncton_has_turned_the_page(self, client, compte, fige):
        token = compte(MONCTON)
        fige(LE_1ER_SEPTEMBRE_8H_A_MONCTON)
        facture(client, token, "Electricite", date(2026, 8, 31))
        facture(client, token, "Loyer", date(2026, 9, 1), "700.00", "housing")

        corps = resume(client, token)

        assert corps["month"] == "2026-09"
        assert {ligne["category"] for ligne in corps["by_category"]} == {"housing"}

    def test_and_the_bill_of_the_day_before_is_now_late(self, client, compte, fige):
        token = compte(MONCTON)
        fige(LE_1ER_SEPTEMBRE_8H_A_MONCTON)
        facture(client, token, "Electricite", date(2026, 8, 31))

        assert resume(client, token)["late_count"] == 1


class TestTheOtherSideOfTheWorld:
    """Le symetrique. A Tokyo on est en avance sur le serveur, pas en retard :
    une borne calculee en UTC se trompe dans l'autre sens, et un mois entier
    manque au lieu d'etre en trop."""

    def test_tokyo_is_already_in_september_while_the_server_is_not(
        self, client, compte, fige
    ):
        token = compte(TOKYO, email="tokyo@example.com")
        fige(LE_1ER_SEPTEMBRE_8H_A_TOKYO)
        facture(client, token, "Loyer", date(2026, 9, 1), "700.00", "housing")

        corps = resume(client, token)

        assert horloge._now().date() == date(2026, 8, 31), "le serveur est bien en aout"
        assert corps["month"] == "2026-09"
        assert corps["month_total"] == "700.00"

    def test_a_bill_dated_today_in_tokyo_is_not_late(self, client, compte, fige):
        token = compte(TOKYO, email="tokyo@example.com")
        fige(LE_1ER_SEPTEMBRE_8H_A_TOKYO)
        facture(client, token, "Internet", date(2026, 9, 1), "170.00", "internet")

        assert resume(client, token)["late_count"] == 0


class TestTwoAccountsAtTheSameInstant:
    def test_they_do_not_see_the_same_day(self, client, compte, fige, mailbox):
        # La preuve que le jour vient du compte et non du processus : un seul
        # instant, deux calendriers.
        moncton = compte(MONCTON, email="moncton@example.com")
        tokyo = compte(TOKYO, email="tokyo@example.com")
        fige(LE_31_AOUT_22H10_A_MONCTON)

        assert resume(client, moncton)["month"] == "2026-08"
        assert resume(client, tokyo)["month"] == "2026-09"
