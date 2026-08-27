import pytest

from models.db.commitments_db import MAX_COMMITMENTS_PER_TYPE
from models.schemas.commitment_schema import MAX_BATCH_IDS
from services.commitments.occurrence_generator import today_utc

pytestmark = pytest.mark.integration

LIMIT = MAX_COMMITMENTS_PER_TYPE
BATCH_STATUS = "/v1/commitments/batch-status"
BATCH_DELETE = "/v1/commitments/batch-delete"


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def token(verified):
    return verified["tokens"]["access_token"]


def payload(index, commitment_type="subscription", frequency="monthly"):
    return {
        "title": f"Ligne {index}",
        "type": commitment_type,
        "category": "other",
        "amount": "9.99",
        "frequency": frequency,
        "starts_on": today_utc().isoformat(),
    }


def fill(client, token, count, commitment_type="subscription", frequency="oneoff"):
    ids = []
    for index in range(count):
        response = client.post(
            "/v1/commitments",
            json=payload(index, commitment_type, frequency),
            headers=auth(token),
        )
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    return ids


def move(client, token, ids, status):
    return client.patch(BATCH_STATUS, json={"ids": ids, "status": status}, headers=auth(token))


def live(client, token):
    return client.get("/v1/commitments", headers=auth(token)).json()


def status_of(client, token, commitment_id):
    row = next(item for item in live(client, token) if item["id"] == commitment_id)
    return row["status"]


class TestMixedEligibility:
    def test_only_the_lines_that_move_are_counted(self, client, token):
        ids = fill(client, token, 4)
        assert move(client, token, [ids[0]], "paused").status_code == 200

        body = move(client, token, ids, "paused").json()

        assert len(body["changed"]) == 3
        assert ids[0] not in body["changed"]

    def test_the_answer_names_the_lines_that_moved(self, client, token):
        # C'est ce que l'annulation rejoue : une liste approximative rendrait
        # une ligne a un etat qu'elle n'avait pas.
        ids = fill(client, token, 3)

        body = move(client, token, ids, "archived").json()

        assert sorted(body["changed"]) == sorted(ids)
        assert body["blocked"] == []

    def test_an_unknown_identifier_is_ignored(self, client, token):
        ids = fill(client, token, 2)
        inconnu = "11111111-1111-1111-1111-111111111111"

        body = move(client, token, [*ids, inconnu], "paused").json()

        assert sorted(body["changed"]) == sorted(ids)

    def test_another_account_cannot_be_touched(self, client, token, other_token):
        ids = fill(client, token, 2)

        body = move(client, other_token, ids, "archived").json()

        assert body["changed"] == []
        assert status_of(client, token, ids[0]) == "active"


class TestTheCeilingHolds:
    def test_a_selection_that_would_overflow_is_partly_refused(self, client, token):
        ids = fill(client, token, LIMIT)
        archived = ids[:5]
        assert len(move(client, token, archived, "archived").json()["changed"]) == 5
        fill(client, token, 3)

        body = move(client, token, archived, "active").json()

        assert len(body["changed"]) == 2
        assert len(body["blocked"]) == 3

    def test_the_ones_that_pass_are_the_first_sent(self, client, token):
        # L'ordre du client est celui de l'ecran : c'est le seul qu'on puisse
        # expliquer a quelqu'un qui voit deux de ses cinq lignes revenir.
        ids = fill(client, token, LIMIT)
        archived = ids[:5]
        move(client, token, archived, "archived")
        fill(client, token, 3)

        body = move(client, token, archived, "active").json()

        assert body["changed"] == archived[:2]
        assert body["blocked"] == archived[2:]

    def test_nothing_is_refused_when_there_is_room(self, client, token):
        ids = fill(client, token, 10)
        move(client, token, ids, "archived")

        body = move(client, token, ids, "active").json()

        assert len(body["changed"]) == 10
        assert body["blocked"] == []


class TestTheScheduleFollows:
    def test_pausing_by_batch_clears_the_upcoming_payments(self, client, token):
        # Le piege du lot : status est un champ qui replanifie. Un UPDATE
        # unique aurait laisse ces echeances sur le calendrier.
        ids = fill(client, token, 3, frequency="monthly")
        assert self.occurrences(client, token, ids)

        move(client, token, ids, "paused")

        assert self.occurrences(client, token, ids) == []

    def test_reactivating_by_batch_brings_them_back(self, client, token):
        ids = fill(client, token, 3, frequency="monthly")
        move(client, token, ids, "paused")

        move(client, token, ids, "active")

        assert self.occurrences(client, token, ids)

    @staticmethod
    def occurrences(client, token, ids):
        today = today_utc()
        rows = client.get(
            "/v1/commitments/occurrences",
            params={"start": today.isoformat(), "end": today.replace(day=28).isoformat()},
            headers=auth(token),
        ).json()
        return [row for row in rows if row["commitment_id"] in ids]


class TestBatchDelete:
    def test_it_removes_the_selection_and_names_it(self, client, token):
        ids = fill(client, token, 4)

        body = client.post(BATCH_DELETE, json={"ids": ids[:3]}, headers=auth(token)).json()

        assert body["deleted"] == 3
        assert sorted(body["ids"]) == sorted(ids[:3])
        assert [row["id"] for row in live(client, token)] == [ids[3]]

    def test_the_removed_lines_are_restorable(self, client, token):
        ids = fill(client, token, 3)
        removed = client.post(BATCH_DELETE, json={"ids": ids}, headers=auth(token)).json()["ids"]

        restored = client.post(
            "/v1/commitments/restore", json={"ids": removed}, headers=auth(token)
        )

        assert restored.json()["restored"] == 3
        assert len(live(client, token)) == 3

    def test_another_account_cannot_delete(self, client, token, other_token):
        ids = fill(client, token, 2)

        body = client.post(BATCH_DELETE, json={"ids": ids}, headers=auth(other_token)).json()

        assert body["deleted"] == 0
        assert len(live(client, token)) == 2


class TestTheEnvelope:
    def test_an_empty_selection_is_refused(self, client, token):
        assert move(client, token, [], "paused").status_code == 422

    def test_a_selection_beyond_the_ceiling_is_refused(self, client, token):
        trop = ["11111111-1111-1111-1111-111111111111"] * (MAX_BATCH_IDS + 1)

        assert move(client, token, trop, "paused").status_code == 422

    def test_an_unknown_status_is_refused(self, client, token):
        ids = fill(client, token, 1)

        assert move(client, token, ids, "zombie").status_code == 422


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
