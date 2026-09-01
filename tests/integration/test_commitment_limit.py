import pytest

from models.db.commitments_db import MAX_COMMITMENTS_PER_TYPE, RESTORE_CEILING_FACTOR
from models.schemas.commitment_schema import MAX_BATCH_IDS
from services.commitments.occurrence_generator import today_utc

pytestmark = pytest.mark.integration

LIMIT = MAX_COMMITMENTS_PER_TYPE


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def token(verified):
    return verified["tokens"]["access_token"]


def payload(index, commitment_type="subscription"):
    return {
        "title": f"Ligne {index}",
        "type": commitment_type,
        "category": "other",
        "amount": "9.99",
        # Une echeance unique par ligne : le plafond se compte sur les
        # engagements, pas sur ce que le generateur produit derriere.
        "frequency": "oneoff",
        "starts_on": today_utc().isoformat(),
    }


def create(client, token, index, commitment_type="subscription"):
    return client.post(
        "/v1/commitments", json=payload(index, commitment_type), headers=auth(token)
    )


def fill(client, token, count, commitment_type="subscription"):
    ids = []
    for index in range(count):
        response = create(client, token, index, commitment_type)
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    return ids


def vider(client, token, ids):
    response = client.post(
        "/v1/commitments/batch-delete", json={"ids": ids}, headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()["deleted"]


def restaurer(client, token, ids):
    return client.post("/v1/commitments/restore", json={"ids": ids}, headers=auth(token))


def live(client, token):
    response = client.get("/v1/commitments", headers=auth(token))
    assert response.status_code == 200
    return response.json()


class TestTheCeiling:
    def test_the_last_one_under_the_ceiling_is_accepted(self, client, token):
        fill(client, token, LIMIT - 1)

        assert create(client, token, LIMIT - 1).status_code == 201

    def test_the_next_one_is_refused(self, client, token):
        fill(client, token, LIMIT)

        response = create(client, token, LIMIT)

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "COMMITMENT_LIMIT_REACHED"

    def test_the_refusal_names_the_type_and_the_ceiling(self, client, token):
        fill(client, token, LIMIT, "invoice")

        detail = create(client, token, LIMIT, "invoice").json()["detail"]

        assert detail["type"] == "invoice"
        assert detail["limit"] == LIMIT

    def test_nothing_is_created_when_it_is_refused(self, client, token):
        fill(client, token, LIMIT)
        create(client, token, LIMIT)

        assert len(live(client, token)) == LIMIT

    def test_a_paused_line_still_counts(self, client, token):
        ids = fill(client, token, LIMIT)
        client.patch(
            f"/v1/commitments/{ids[0]}", json={"status": "paused"}, headers=auth(token)
        )

        assert create(client, token, LIMIT).status_code == 409


class TestWhatDoesNotCount:
    def test_archiving_one_frees_a_slot(self, client, token):
        ids = fill(client, token, LIMIT)
        archived = client.patch(
            f"/v1/commitments/{ids[0]}", json={"status": "archived"}, headers=auth(token)
        )
        assert archived.status_code == 200

        assert create(client, token, LIMIT).status_code == 201

    def test_the_bin_does_not_count(self, client, token):
        ids = fill(client, token, LIMIT)
        assert client.delete(f"/v1/commitments/{ids[0]}", headers=auth(token)).status_code == 200

        assert create(client, token, LIMIT).status_code == 201

    def test_the_other_type_is_not_affected(self, client, token):
        fill(client, token, LIMIT)

        assert create(client, token, 0, "invoice").status_code == 201

    def test_the_ceiling_is_per_account(self, client, token, other_token):
        fill(client, token, LIMIT)

        assert create(client, other_token, 0).status_code == 201


class TestTheOtherTwoDoors:
    def edit(self, client, token, commitment_id, changes):
        return client.patch(
            f"/v1/commitments/{commitment_id}", json=changes, headers=auth(token)
        )

    def test_unarchiving_into_a_full_shelf_is_refused(self, client, token):
        ids = fill(client, token, LIMIT)
        self.edit(client, token, ids[0], {"status": "archived"})
        assert create(client, token, LIMIT).status_code == 201

        response = self.edit(client, token, ids[0], {"status": "active"})

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "COMMITMENT_LIMIT_REACHED"

    def test_unarchiving_is_allowed_when_a_slot_is_free(self, client, token):
        ids = fill(client, token, LIMIT)
        self.edit(client, token, ids[0], {"status": "archived"})

        assert self.edit(client, token, ids[0], {"status": "active"}).status_code == 200

    def test_moving_a_type_into_a_full_shelf_is_refused(self, client, token):
        fill(client, token, LIMIT)
        facture = fill(client, token, 1, "invoice")[0]

        response = self.edit(client, token, facture, {"type": "subscription"})

        assert response.status_code == 409
        assert response.json()["detail"]["type"] == "subscription"

    def test_moving_a_type_is_allowed_when_a_slot_is_free(self, client, token):
        fill(client, token, LIMIT - 1)
        facture = fill(client, token, 1, "invoice")[0]

        assert self.edit(client, token, facture, {"type": "subscription"}).status_code == 200

    def test_an_ordinary_edit_at_the_ceiling_is_untouched(self, client, token):
        # La garde ne doit refuser que ce qui fait entrer une ligne de plus.
        ids = fill(client, token, LIMIT)

        assert self.edit(client, token, ids[0], {"title": "Renomme"}).status_code == 200

    def test_pausing_at_the_ceiling_is_untouched(self, client, token):
        ids = fill(client, token, LIMIT)

        assert self.edit(client, token, ids[0], {"status": "paused"}).status_code == 200

    def test_archiving_at_the_ceiling_is_untouched(self, client, token):
        ids = fill(client, token, LIMIT)

        assert self.edit(client, token, ids[0], {"status": "archived"}).status_code == 200


class TestRestoring:
    def test_restoring_is_allowed_even_above_the_ceiling(self, client, token):
        # Decision figee : le plafond ne garde que la creation. Refuser une
        # restauration ferait de la corbeille une nasse - on y jetterait pour
        # faire de la place, sans jamais pouvoir en ressortir.
        ids = fill(client, token, LIMIT)
        client.delete(f"/v1/commitments/{ids[0]}", headers=auth(token))
        assert create(client, token, LIMIT).status_code == 201

        restored = client.post(
            "/v1/commitments/restore", json={"ids": [ids[0]]}, headers=auth(token)
        )

        assert restored.status_code == 200
        assert restored.json()["restored"] == 1
        assert len(live(client, token)) == LIMIT + 1


class TestTheTrashHasARoof:
    def test_the_cycle_that_used_to_grow_without_end_is_stopped(self, client, token):
        # Le contournement : creer 25, tout jeter, recreer 25, tout jeter, et la
        # corbeille en tient 50 pendant que 25 vivent. Chaque tour ajoutait 25
        # lignes de plus que le plafond, sans fin et sans qu'aucune garde ne
        # s'en apercoive.
        premiers = fill(client, token, LIMIT)
        vider(client, token, premiers)
        seconds = fill(client, token, LIMIT)
        vider(client, token, seconds)
        fill(client, token, LIMIT)

        refus = restaurer(client, token, premiers + seconds)

        assert refus.status_code == 409
        detail = refus.json()["detail"]
        assert detail["code"] == "RESTORE_LIMIT_REACHED"
        assert detail["limit"] == RESTORE_CEILING_FACTOR * LIMIT
        assert len(live(client, token)) == LIMIT

    def test_the_roof_itself_is_reachable(self, client, token):
        # Exactement deux fois le plafond passe : le refus commence au-dela,
        # sinon reprendre une corbeille pleine deviendrait impossible.
        jetes = fill(client, token, LIMIT)
        vider(client, token, jetes)
        fill(client, token, LIMIT)

        rendu = restaurer(client, token, jetes)

        assert rendu.status_code == 200
        assert len(live(client, token)) == RESTORE_CEILING_FACTOR * LIMIT

    def test_each_type_carries_its_own_roof(self, client, token):
        # Un compte au toit sur ses abonnements peut toujours reprendre ses
        # factures : les deux populations ne se comptent pas ensemble.
        jetes = fill(client, token, LIMIT, "invoice")
        vider(client, token, jetes)
        fill(client, token, LIMIT, "invoice")
        fill(client, token, LIMIT, "subscription")

        assert restaurer(client, token, jetes).status_code == 200

    def test_an_archived_line_comes_back_even_from_under_the_roof(self, client, token):
        # Une ligne archivee ne compte dans aucune population : la refuser
        # rendrait un historique irrecuperable pour une place qu'il ne prend
        # pas. Le toit doit deja etre atteint, sinon la garde passerait aussi
        # bien sans faire la difference.
        cree = create(client, token, 0).json()["id"]
        client.patch(
            f"/v1/commitments/{cree}", json={"status": "archived"}, headers=auth(token)
        )
        vider(client, token, [cree])
        jetes = fill(client, token, LIMIT)
        vider(client, token, jetes)
        fill(client, token, LIMIT)
        assert restaurer(client, token, jetes).status_code == 200
        assert len(live(client, token)) == RESTORE_CEILING_FACTOR * LIMIT

        assert restaurer(client, token, [cree]).status_code == 200

    def test_a_batch_never_lands_half_way(self, client, token):
        # Le refus precede l'ecriture : rien ne doit sortir de la corbeille
        # quand le lot depasse, pas meme les premieres lignes du lot.
        premiers = fill(client, token, LIMIT)
        vider(client, token, premiers)
        seconds = fill(client, token, LIMIT)
        vider(client, token, seconds)
        fill(client, token, LIMIT)

        restaurer(client, token, premiers + seconds)

        corbeille = client.get("/v1/commitments/trash", headers=auth(token)).json()
        assert len(corbeille) == 2 * LIMIT


class TestTheSizeOfARestoreBatch:
    def test_it_stops_where_the_other_batches_stop(self, client, token):
        # 500 ici et 200 ailleurs etait un ecart sans raison : le lot le plus
        # gros doit couter le meme travail que les autres.
        trop = ["11111111-1111-1111-1111-111111111111"] * (MAX_BATCH_IDS + 1)

        assert restaurer(client, token, trop).status_code == 422

    def test_a_full_batch_is_still_accepted(self, client, token):
        juste = ["11111111-1111-1111-1111-111111111111"] * MAX_BATCH_IDS

        assert restaurer(client, token, juste).status_code == 200


class TestTheFrontKnowsTheCeiling:
    def test_the_account_carries_it(self, client, token):
        # L'indicateur des pages abonnements et factures lit ce champ : sans
        # lui, le front devrait ecrire le chiffre de son cote et mentir le jour
        # ou il change ici.
        response = client.get("/v1/auth/me", headers=auth(token))

        assert response.status_code == 200
        assert response.json()["commitment_limit"] == LIMIT


@pytest.fixture
def other_token(client, mailbox):
    account = {
        "first_name": "Sophie",
        "email": "sophie@example.com",
        "password": "MotDePasse1!",
    }
    assert client.post("/v1/auth/register", json=account).status_code == 201
    response = client.post(
        "/v1/auth/verify-email", json={"email": account["email"], "code": mailbox[-1]["code"]}
    )
    assert response.status_code == 200
    return response.json()["access_token"]
