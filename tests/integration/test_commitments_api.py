from datetime import timedelta
from decimal import Decimal

import pytest

from services.commitments.occurrence_generator import add_months, today_utc

pytestmark = pytest.mark.integration


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def token(verified):
    return verified["tokens"]["access_token"]


@pytest.fixture
def other_token(client, mailbox):
    payload = {"first_name": "Sophie", "email": "sophie@example.com", "password": "MotDePasse1!"}
    assert client.post("/v1/auth/register", json=payload).status_code == 201
    code = mailbox[-1]["code"]
    response = client.post(
        "/v1/auth/verify-email", json={"email": payload["email"], "code": code}
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def netflix(**overrides):
    return {
        "title": "Netflix",
        "type": "subscription",
        "category": "entertainment",
        "amount": "18.99",
        "frequency": "monthly",
        "starts_on": today_utc().isoformat(),
        **overrides,
    }


def loyer(**overrides):
    return {
        "title": "Loyer",
        "type": "invoice",
        "category": "housing",
        "amount": "1250.00",
        "frequency": "monthly",
        "starts_on": today_utc().isoformat(),
        **overrides,
    }


def create(client, token, payload):
    response = client.post("/v1/commitments", json=payload, headers=auth(token))
    assert response.status_code == 201, response.text
    return response.json()


class TestCreation:
    def test_creates_a_subscription(self, client, token):
        body = create(client, token, netflix())
        assert body["title"] == "Netflix"
        assert body["type"] == "subscription"
        assert body["status"] == "active"
        assert body["amount"] == "18.99"
        assert body["next_due_date"] == today_utc().isoformat()

    def test_trims_the_title(self, client, token):
        body = create(client, token, netflix(title="  Spotify  "))
        assert body["title"] == "Spotify"

    def test_requires_authentication(self, client):
        assert client.post("/v1/commitments", json=netflix()).status_code == 401

    @pytest.mark.parametrize(
        "override",
        [
            {"amount": "0"},
            {"amount": "-5.00"},
            {"amount": "12.345"},
            {"type": "mortgage"},
            {"frequency": "daily"},
            {"title": "   "},
            {"title": ""},
            {"reminder_days_before": 45},
            {"reminder_days_before": -1},
        ],
    )
    def test_rejects_invalid_payloads(self, client, token, override):
        response = client.post("/v1/commitments", json=netflix(**override), headers=auth(token))
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "VALIDATION_ERROR"

    def test_rejects_a_term_ending_before_it_starts(self, client, token):
        payload = netflix(ends_on=(today_utc() - timedelta(days=10)).isoformat())
        response = client.post("/v1/commitments", json=payload, headers=auth(token))
        assert response.status_code == 422


class TestListing:
    def test_filters_by_type(self, client, token):
        create(client, token, netflix())
        create(client, token, loyer())

        subscriptions = client.get(
            "/v1/commitments", params={"type": "subscription"}, headers=auth(token)
        ).json()
        invoices = client.get(
            "/v1/commitments", params={"type": "invoice"}, headers=auth(token)
        ).json()

        assert [item["title"] for item in subscriptions] == ["Netflix"]
        assert [item["title"] for item in invoices] == ["Loyer"]

    def test_rejects_an_unknown_type_filter(self, client, token):
        response = client.get("/v1/commitments", params={"type": "car"}, headers=auth(token))
        assert response.status_code == 422

    def test_never_leaks_another_account(self, client, token, other_token):
        create(client, token, netflix())
        assert client.get("/v1/commitments", headers=auth(other_token)).json() == []


class TestRouteOrdering:
    def test_summary_is_not_captured_by_the_id_route(self, client, token):
        response = client.get("/v1/commitments/summary", headers=auth(token))
        assert response.status_code == 200
        assert "month_total" in response.json()

    def test_occurrences_is_not_captured_by_the_id_route(self, client, token):
        response = client.get("/v1/commitments/occurrences", headers=auth(token))
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_a_malformed_id_is_rejected(self, client, token):
        assert client.get("/v1/commitments/not-a-uuid", headers=auth(token)).status_code == 422


class TestSingleCommitment:
    def test_reads_back_what_was_created(self, client, token):
        created = create(client, token, netflix())
        body = client.get(f"/v1/commitments/{created['id']}", headers=auth(token)).json()
        assert body["id"] == created["id"]

    def test_another_account_gets_a_not_found(self, client, token, other_token):
        created = create(client, token, netflix())
        response = client.get(f"/v1/commitments/{created['id']}", headers=auth(other_token))
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "COMMITMENT_NOT_FOUND"

    def test_rejects_an_empty_patch(self, client, token):
        created = create(client, token, netflix())
        response = client.patch(
            f"/v1/commitments/{created['id']}", json={}, headers=auth(token)
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "NO_FIELDS_TO_UPDATE"

    def test_rejects_an_end_date_before_the_stored_start(self, client, token):
        created = create(client, token, netflix())
        response = client.patch(
            f"/v1/commitments/{created['id']}",
            json={"ends_on": (today_utc() - timedelta(days=30)).isoformat()},
            headers=auth(token),
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "INVALID_DATE_RANGE"

    def test_pausing_it_drops_the_pending_schedule(self, client, token):
        created = create(client, token, netflix())
        response = client.patch(
            f"/v1/commitments/{created['id']}", json={"status": "paused"}, headers=auth(token)
        )
        assert response.status_code == 200
        assert response.json()["next_due_date"] is None
        assert client.get("/v1/commitments/occurrences", headers=auth(token)).json() == []

    def test_resuming_it_rebuilds_the_schedule(self, client, token):
        created = create(client, token, netflix())
        client.patch(
            f"/v1/commitments/{created['id']}", json={"status": "paused"}, headers=auth(token)
        )

        response = client.patch(
            f"/v1/commitments/{created['id']}", json={"status": "active"}, headers=auth(token)
        )
        assert response.status_code == 200
        assert response.json()["next_due_date"] is not None
        assert client.get("/v1/commitments/occurrences", headers=auth(token)).json() != []

    def test_archiving_it_drops_the_schedule_but_keeps_the_row(self, client, token):
        created = create(client, token, netflix())
        response = client.patch(
            f"/v1/commitments/{created['id']}", json={"status": "archived"}, headers=auth(token)
        )
        assert response.status_code == 200
        assert response.json()["next_due_date"] is None
        assert client.get("/v1/commitments/occurrences", headers=auth(token)).json() == []
        assert client.get(f"/v1/commitments/{created['id']}", headers=auth(token)).status_code == 200

    def test_the_default_listing_still_returns_archived_rows(self, client, token):
        created = create(client, token, netflix())
        client.patch(
            f"/v1/commitments/{created['id']}", json={"status": "archived"}, headers=auth(token)
        )

        rows = client.get("/v1/commitments", headers=auth(token)).json()
        assert [row["status"] for row in rows] == ["archived"]

    def test_a_settled_occurrence_survives_a_pause(self, client, token):
        created = create(client, token, netflix())
        occurrences = client.get("/v1/commitments/occurrences", headers=auth(token)).json()
        paid = occurrences[0]
        client.patch(
            f"/v1/commitments/occurrences/{paid['id']}",
            json={"status": "paid"},
            headers=auth(token),
        )

        client.patch(
            f"/v1/commitments/{created['id']}", json={"status": "paused"}, headers=auth(token)
        )

        remaining = client.get("/v1/commitments/occurrences", headers=auth(token)).json()
        assert [row["id"] for row in remaining] == [paid["id"]]
        assert remaining[0]["status"] == "paid"

    def test_an_unknown_status_is_rejected(self, client, token):
        created = create(client, token, netflix())
        response = client.patch(
            f"/v1/commitments/{created['id']}", json={"status": "cancelled"}, headers=auth(token)
        )
        assert response.status_code == 422

    def test_renaming_leaves_the_schedule_alone(self, client, token):
        created = create(client, token, netflix())
        before = client.get("/v1/commitments/occurrences", headers=auth(token)).json()

        client.patch(
            f"/v1/commitments/{created['id']}", json={"title": "Netflix Premium"}, headers=auth(token)
        )
        after = client.get("/v1/commitments/occurrences", headers=auth(token)).json()

        assert [row["id"] for row in after] == [row["id"] for row in before]
        assert after[0]["title"] == "Netflix Premium"

    def test_changing_the_amount_reprices_pending_occurrences(self, client, token):
        created = create(client, token, netflix())
        response = client.patch(
            f"/v1/commitments/{created['id']}", json={"amount": "24.99"}, headers=auth(token)
        )
        assert response.status_code == 200

        occurrences = client.get("/v1/commitments/occurrences", headers=auth(token)).json()
        assert all(row["amount"] == "24.99" for row in occurrences)

    def test_deleting_it_makes_it_disappear(self, client, token):
        created = create(client, token, netflix())
        deleted = client.delete(f"/v1/commitments/{created['id']}", headers=auth(token))
        assert deleted.status_code == 200

        assert client.get(f"/v1/commitments/{created['id']}", headers=auth(token)).status_code == 404
        assert client.get("/v1/commitments/occurrences", headers=auth(token)).json() == []


class TestOccurrences:
    def test_carry_the_commitment_identity(self, client, token):
        create(client, token, netflix())
        rows = client.get("/v1/commitments/occurrences", headers=auth(token)).json()
        assert rows[0]["title"] == "Netflix"
        assert rows[0]["type"] == "subscription"
        assert rows[0]["category"] == "entertainment"
        assert rows[0]["status"] == "pending"
        assert rows[0]["is_late"] is False

    def test_marking_one_paid_stamps_the_date(self, client, token):
        create(client, token, netflix())
        rows = client.get("/v1/commitments/occurrences", headers=auth(token)).json()

        response = client.patch(
            f"/v1/commitments/occurrences/{rows[0]['id']}",
            json={"status": "paid"},
            headers=auth(token),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "paid"
        assert body["paid_at"] is not None
        assert body["is_late"] is False

    def test_correcting_the_amount_of_a_variable_invoice(self, client, token):
        create(client, token, loyer(title="Electricite", amount="87.00"))
        rows = client.get("/v1/commitments/occurrences", headers=auth(token)).json()

        body = client.patch(
            f"/v1/commitments/occurrences/{rows[0]['id']}",
            json={"status": "paid", "amount": "112.40"},
            headers=auth(token),
        ).json()
        assert body["amount"] == "112.40"

    def test_reverting_to_pending_clears_the_payment_date(self, client, token):
        create(client, token, netflix())
        rows = client.get("/v1/commitments/occurrences", headers=auth(token)).json()
        url = f"/v1/commitments/occurrences/{rows[0]['id']}"

        client.patch(url, json={"status": "paid"}, headers=auth(token))
        body = client.patch(url, json={"status": "pending"}, headers=auth(token)).json()
        assert body["status"] == "pending"
        assert body["paid_at"] is None

    def test_another_account_cannot_touch_them(self, client, token, other_token):
        create(client, token, netflix())
        rows = client.get("/v1/commitments/occurrences", headers=auth(token)).json()

        response = client.patch(
            f"/v1/commitments/occurrences/{rows[0]['id']}",
            json={"status": "paid"},
            headers=auth(other_token),
        )
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "OCCURRENCE_NOT_FOUND"

    def test_rejects_a_reversed_range(self, client, token):
        today = today_utc()
        response = client.get(
            "/v1/commitments/occurrences",
            params={"start": today.isoformat(), "end": (today - timedelta(days=5)).isoformat()},
            headers=auth(token),
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "INVALID_DATE_RANGE"

    def test_rejects_an_oversized_range(self, client, token):
        today = today_utc()
        response = client.get(
            "/v1/commitments/occurrences",
            params={"start": today.isoformat(), "end": (today + timedelta(days=500)).isoformat()},
            headers=auth(token),
        )
        assert response.status_code == 400


class TestDefaultReminderDelay:
    def test_a_new_commitment_inherits_the_account_default(self, client, token):
        client.patch("/v1/users/me", json={"default_reminder_days": 7}, headers=auth(token))

        created = create(client, token, netflix())

        assert created["reminder_days_before"] == 7

    def test_an_explicit_delay_still_wins(self, client, token):
        client.patch("/v1/users/me", json={"default_reminder_days": 7}, headers=auth(token))

        created = create(client, token, netflix(reminder_days_before=1))

        assert created["reminder_days_before"] == 1

    def test_an_explicit_zero_is_not_mistaken_for_missing(self, client, token):
        client.patch("/v1/users/me", json={"default_reminder_days": 7}, headers=auth(token))

        created = create(client, token, netflix(reminder_days_before=0))

        assert created["reminder_days_before"] == 0

    def test_changing_the_default_leaves_existing_commitments_alone(self, client, token):
        created = create(client, token, netflix())
        assert created["reminder_days_before"] == 3

        client.patch("/v1/users/me", json={"default_reminder_days": 14}, headers=auth(token))

        body = client.get(f"/v1/commitments/{created['id']}", headers=auth(token)).json()
        assert body["reminder_days_before"] == 3

    def test_the_account_default_drives_the_reminder(self, client, token, db):
        client.patch("/v1/users/me", json={"default_reminder_days": 10}, headers=auth(token))
        create(client, token, netflix())

        rows = db("SELECT reminder_days_before FROM commitments")
        assert rows[0][0] == 10


class TestBackdatedInvoice:
    def test_a_bill_dated_yesterday_still_produces_its_due_date(self, client, token):
        yesterday = today_utc() - timedelta(days=1)
        create(
            client,
            token,
            loyer(title="Koodo", frequency="oneoff", starts_on=yesterday.isoformat()),
        )

        rows = client.get(
            "/v1/commitments/occurrences",
            params={
                "start": (yesterday - timedelta(days=5)).isoformat(),
                "end": (today_utc() + timedelta(days=5)).isoformat(),
            },
            headers=auth(token),
        ).json()

        assert len(rows) == 1
        assert rows[0]["due_date"] == yesterday.isoformat()
        assert rows[0]["status"] == "pending"
        assert rows[0]["is_late"] is True

    def test_it_shows_up_as_late_on_the_dashboard(self, client, token):
        create(
            client,
            token,
            loyer(
                frequency="oneoff",
                starts_on=(today_utc() - timedelta(days=2)).isoformat(),
            ),
        )

        body = client.get("/v1/commitments/summary", headers=auth(token)).json()
        assert body["late_count"] == 1

    def test_a_backdated_bill_creates_exactly_one_due_date(self, client, token, db):
        create(
            client,
            token,
            loyer(
                frequency="oneoff",
                starts_on=(today_utc() - timedelta(days=400)).isoformat(),
            ),
        )

        rows = db("select count(*) from commitment_occurrences")
        assert rows[0][0] == 1

    def test_a_recurring_bill_never_backfills(self, client, token, db):
        create(
            client,
            token,
            loyer(starts_on=(today_utc() - timedelta(days=400)).isoformat()),
        )

        rows = db(
            "select count(*) from commitment_occurrences where due_date < :today",
            today=today_utc(),
        )
        assert rows[0][0] == 0

    def test_a_backdated_bill_still_respects_its_term(self, client, token, db):
        past = today_utc() - timedelta(days=10)
        create(
            client,
            token,
            loyer(
                frequency="oneoff",
                starts_on=past.isoformat(),
                ends_on=past.isoformat(),
            ),
        )

        rows = db("select count(*) from commitment_occurrences")
        assert rows[0][0] == 1


class TestSummary:
    def test_splits_subscriptions_from_invoices(self, client, token):
        create(client, token, netflix())
        create(client, token, loyer())

        body = client.get("/v1/commitments/summary", headers=auth(token)).json()
        assert body["subscriptions_total"] == "18.99"
        assert body["invoices_total"] == "1250.00"
        assert body["month_total"] == "1268.99"
        assert body["active_count"] == 2
        assert body["late_count"] == 0
        assert body["currency"] == "CAD"
        assert body["month"] == f"{today_utc().year:04d}-{today_utc().month:02d}"

    def test_lists_what_comes_next(self, client, token):
        create(client, token, netflix())
        body = client.get("/v1/commitments/summary", headers=auth(token)).json()
        assert body["upcoming"][0]["title"] == "Netflix"
        assert body["upcoming_days"] == 14
        assert len(body["upcoming"]) <= 8

    def test_only_covers_the_next_fourteen_days(self, client, token):
        today = today_utc()
        create(client, token, netflix(starts_on=(today + timedelta(days=3)).isoformat()))
        create(
            client,
            token,
            loyer(title="Assurance", starts_on=(today + timedelta(days=13)).isoformat()),
        )
        create(
            client,
            token,
            loyer(title="Impots", starts_on=(today + timedelta(days=20)).isoformat()),
        )

        body = client.get("/v1/commitments/summary", headers=auth(token)).json()
        assert [row["title"] for row in body["upcoming"]] == ["Netflix", "Assurance"]
        assert body["upcoming_total"] == 2

    def test_reports_how_many_were_truncated(self, client, token):
        today = today_utc()
        for index in range(10):
            create(
                client,
                token,
                netflix(
                    title=f"Service {index}",
                    frequency="oneoff",
                    starts_on=(today + timedelta(days=index)).isoformat(),
                ),
            )

        body = client.get("/v1/commitments/summary", headers=auth(token)).json()
        assert len(body["upcoming"]) == 8
        assert body["upcoming_total"] == 10

    def test_excludes_what_is_already_late(self, client, token, db):
        today = today_utc()
        created = create(client, token, netflix())
        moved = db(
            "UPDATE commitment_occurrences SET due_date = :late"
            " WHERE commitment_id = :id AND due_date = :today RETURNING id",
            id=created["id"],
            late=today - timedelta(days=3),
            today=today,
        )
        assert len(moved) == 1

        body = client.get("/v1/commitments/summary", headers=auth(token)).json()
        assert body["late_count"] == 1
        assert body["upcoming"] == []
        assert body["upcoming_total"] == 0

    def test_a_past_start_date_never_creates_history(self, client, token):
        today = today_utc()
        created = create(
            client, token, netflix(starts_on=(today - timedelta(days=40)).isoformat())
        )

        rows = client.get(
            "/v1/commitments/occurrences",
            params={
                "start": (today - timedelta(days=60)).isoformat(),
                "end": (today + timedelta(days=60)).isoformat(),
            },
            headers=auth(token),
        ).json()

        assert created["next_due_date"] >= today.isoformat()
        assert all(row["due_date"] >= today.isoformat() for row in rows)

    def test_moves_an_amount_to_paid_once_settled(self, client, token):
        create(client, token, netflix())
        rows = client.get("/v1/commitments/occurrences", headers=auth(token)).json()
        client.patch(
            f"/v1/commitments/occurrences/{rows[0]['id']}",
            json={"status": "paid"},
            headers=auth(token),
        )

        body = client.get("/v1/commitments/summary", headers=auth(token)).json()
        assert body["paid_total"] == "18.99"
        assert body["pending_total"] == "0.00"
        assert body["upcoming"] == []
        assert body["upcoming_total"] == 0

    def test_is_empty_for_a_fresh_account(self, client, token):
        body = client.get("/v1/commitments/summary", headers=auth(token)).json()
        assert body["month_total"] == "0.00"
        assert body["active_count"] == 0
        assert body["upcoming"] == []
        assert body["by_category"] == []

    def test_ranks_the_categories_by_what_they_cost(self, client, token):
        create(client, token, netflix())
        create(client, token, netflix(title="Spotify", category="music", amount="10.99"))
        create(client, token, loyer())

        body = client.get("/v1/commitments/summary", headers=auth(token)).json()
        assert [row["category"] for row in body["by_category"]] == [
            "housing",
            "entertainment",
            "music",
        ]
        assert [row["total"] for row in body["by_category"]] == ["1250.00", "18.99", "10.99"]

    def test_merges_every_line_of_the_same_category(self, client, token):
        create(client, token, netflix())
        create(client, token, netflix(title="Disney+", amount="12.00"))

        body = client.get("/v1/commitments/summary", headers=auth(token)).json()
        assert body["by_category"] == [
            {"category": "entertainment", "total": "30.99", "count": 2}
        ]

    def test_the_categories_add_up_to_the_month_total(self, client, token):
        today = today_utc()
        create(client, token, netflix())
        create(client, token, loyer())
        create(
            client,
            token,
            netflix(title="Gym", category="fitness", frequency="weekly", amount="9.50"),
        )
        create(
            client,
            token,
            loyer(
                title="Impots",
                category="taxes",
                frequency="oneoff",
                starts_on=(today + timedelta(days=2)).isoformat(),
            ),
        )

        body = client.get("/v1/commitments/summary", headers=auth(token)).json()
        parts = sum(Decimal(row["total"]) for row in body["by_category"])
        assert parts == Decimal(body["month_total"])

    def test_a_skipped_due_date_leaves_its_category(self, client, token):
        create(client, token, netflix())
        create(client, token, loyer())
        rows = client.get("/v1/commitments/occurrences", headers=auth(token)).json()
        target = next(row for row in rows if row["category"] == "entertainment")
        client.patch(
            f"/v1/commitments/occurrences/{target['id']}",
            json={"status": "skipped"},
            headers=auth(token),
        )

        body = client.get("/v1/commitments/summary", headers=auth(token)).json()
        assert [row["category"] for row in body["by_category"]] == ["housing"]

    def test_a_corrected_amount_reaches_every_total(self, client, token):
        create(client, token, loyer(title="Electricite", amount="87.00"))
        rows = client.get("/v1/commitments/occurrences", headers=auth(token)).json()

        client.patch(
            f"/v1/commitments/occurrences/{rows[0]['id']}",
            json={"status": "paid", "amount": "112.40"},
            headers=auth(token),
        )

        body = client.get("/v1/commitments/summary", headers=auth(token)).json()
        assert body["month_total"] == "112.40"
        assert body["paid_total"] == "112.40"
        assert body["pending_total"] == "0.00"
        assert body["by_category"] == [
            {"category": "housing", "total": "112.40", "count": 1}
        ]

    def test_a_correction_never_touches_the_rule(self, client, token):
        created = create(client, token, loyer(title="Electricite", amount="87.00"))
        rows = client.get("/v1/commitments/occurrences", headers=auth(token)).json()

        client.patch(
            f"/v1/commitments/occurrences/{rows[0]['id']}",
            json={"status": "paid", "amount": "112.40"},
            headers=auth(token),
        )

        body = client.get(
            f"/v1/commitments/{created['id']}", headers=auth(token)
        ).json()
        assert body["amount"] == "87.00"

    def test_a_skipped_due_date_leaves_the_month_total(self, client, token):
        create(client, token, netflix())
        create(client, token, loyer())
        rows = client.get("/v1/commitments/occurrences", headers=auth(token)).json()
        target = next(row for row in rows if row["category"] == "entertainment")

        before = client.get("/v1/commitments/summary", headers=auth(token)).json()
        client.patch(
            f"/v1/commitments/occurrences/{target['id']}",
            json={"status": "skipped"},
            headers=auth(token),
        )
        after = client.get("/v1/commitments/summary", headers=auth(token)).json()

        assert Decimal(before["month_total"]) - Decimal(after["month_total"]) == Decimal("18.99")
        assert after["subscriptions_total"] == "0.00"
        assert after["pending_total"] == after["month_total"]

    def test_a_skipped_due_date_is_never_late(self, client, token, db):
        create(client, token, netflix())
        rows = client.get("/v1/commitments/occurrences", headers=auth(token)).json()
        client.patch(
            f"/v1/commitments/occurrences/{rows[0]['id']}",
            json={"status": "skipped"},
            headers=auth(token),
        )
        db(
            "UPDATE commitment_occurrences SET due_date = :late WHERE id = :id",
            id=rows[0]["id"],
            late=today_utc() - timedelta(days=10),
        )

        body = client.get("/v1/commitments/summary", headers=auth(token)).json()
        assert body["late_count"] == 0

    def test_a_settled_due_date_stays_in_its_category(self, client, token):
        create(client, token, netflix())
        rows = client.get("/v1/commitments/occurrences", headers=auth(token)).json()
        client.patch(
            f"/v1/commitments/occurrences/{rows[0]['id']}",
            json={"status": "paid"},
            headers=auth(token),
        )

        body = client.get("/v1/commitments/summary", headers=auth(token)).json()
        assert body["by_category"] == [
            {"category": "entertainment", "total": "18.99", "count": 1}
        ]

    def test_another_account_never_bleeds_into_the_breakdown(self, client, token, other_token):
        create(client, token, netflix())
        create(client, other_token, loyer())

        body = client.get("/v1/commitments/summary", headers=auth(token)).json()
        assert [row["category"] for row in body["by_category"]] == ["entertainment"]
class TestLongCycleHorizon:
    def test_a_yearly_commitment_started_last_month_still_shows_its_next_date(
        self, client, token
    ):
        today = today_utc()
        created = create(
            client,
            token,
            netflix(
                title="Adobe",
                frequency="yearly",
                starts_on=(today - timedelta(days=32)).isoformat(),
            ),
        )

        assert created["next_due_date"] is not None
        assert created["next_due_date"] > today.isoformat()

    def test_a_quarterly_commitment_keeps_a_date_just_past_its_charge(self, client, token):
        today = today_utc()
        created = create(
            client,
            token,
            netflix(
                title="Assurance",
                frequency="quarterly",
                starts_on=(today - timedelta(days=1)).isoformat(),
            ),
        )

        assert created["next_due_date"] is not None

    def test_the_listing_agrees_with_the_creation(self, client, token):
        today = today_utc()
        created = create(
            client,
            token,
            netflix(
                title="Adobe",
                frequency="yearly",
                starts_on=(today - timedelta(days=32)).isoformat(),
            ),
        )

        rows = client.get("/v1/commitments", headers=auth(token)).json()

        assert rows[0]["next_due_date"] == created["next_due_date"]

    def test_a_monthly_commitment_keeps_the_short_window(self, client, token):
        today = today_utc()
        create(client, token, netflix())

        rows = client.get(
            "/v1/commitments/occurrences",
            params={
                "start": today.isoformat(),
                "end": (today + timedelta(days=395)).isoformat(),
            },
            headers=auth(token),
        ).json()

        assert all(
            row["due_date"] <= (today + timedelta(days=90)).isoformat() for row in rows
        )
class TestLateDueDate:
    def test_a_missed_payment_shows_on_the_commitment(self, client, token, db):
        created = create(client, token, netflix())
        db("update commitment_occurrences set due_date = due_date - interval '3 days'")

        body = client.get(f"/v1/commitments/{created['id']}", headers=auth(token)).json()

        assert body["late_due_date"] == (today_utc() - timedelta(days=3)).isoformat()

    def test_the_next_date_stays_the_upcoming_one(self, client, token, db):
        created = create(client, token, netflix())
        db(
            "update commitment_occurrences set due_date = due_date - interval '3 days' "
            "where due_date = :d",
            d=today_utc(),
        )

        body = client.get(f"/v1/commitments/{created['id']}", headers=auth(token)).json()

        assert body["late_due_date"] < today_utc().isoformat()
        assert body["next_due_date"] > today_utc().isoformat()

    def test_nothing_late_leaves_the_field_empty(self, client, token):
        created = create(client, token, netflix())

        body = client.get(f"/v1/commitments/{created['id']}", headers=auth(token)).json()

        assert body["late_due_date"] is None
        assert body["next_due_date"] == today_utc().isoformat()

    def test_settling_the_missed_payment_clears_it(self, client, token, db):
        created = create(client, token, netflix())
        db("update commitment_occurrences set due_date = due_date - interval '3 days'")
        late = client.get("/v1/commitments/occurrences/late", headers=auth(token)).json()
        client.patch(
            f"/v1/commitments/occurrences/{late[0]['id']}",
            json={"status": "paid"},
            headers=auth(token),
        )

        body = client.get(f"/v1/commitments/{created['id']}", headers=auth(token)).json()

        assert body["late_due_date"] is None

    def test_the_listing_carries_it_too(self, client, token, db):
        create(client, token, netflix())
        db("update commitment_occurrences set due_date = due_date - interval '2 days'")

        rows = client.get("/v1/commitments", headers=auth(token)).json()

        assert rows[0]["late_due_date"] == (today_utc() - timedelta(days=2)).isoformat()


class TestPaymentDate:
    def _first(self, client, token):
        create(client, token, netflix())
        return client.get("/v1/commitments/occurrences", headers=auth(token)).json()[0]

    def test_defaults_to_today(self, client, token):
        row = self._first(client, token)

        body = client.patch(
            f"/v1/commitments/occurrences/{row['id']}",
            json={"status": "paid"},
            headers=auth(token),
        ).json()

        assert body["paid_on"] == today_utc().isoformat()

    def test_accepts_a_past_date(self, client, token):
        row = self._first(client, token)
        paid = today_utc() - timedelta(days=2)

        body = client.patch(
            f"/v1/commitments/occurrences/{row['id']}",
            json={"status": "paid", "paid_on": paid.isoformat()},
            headers=auth(token),
        ).json()

        assert body["paid_on"] == paid.isoformat()

    def test_refuses_a_future_date(self, client, token):
        row = self._first(client, token)
        later = today_utc() + timedelta(days=1)

        response = client.patch(
            f"/v1/commitments/occurrences/{row['id']}",
            json={"status": "paid", "paid_on": later.isoformat()},
            headers=auth(token),
        )

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "FUTURE_PAYMENT_DATE"

    def test_the_listing_keeps_it(self, client, token):
        row = self._first(client, token)
        paid = today_utc() - timedelta(days=4)
        client.patch(
            f"/v1/commitments/occurrences/{row['id']}",
            json={"status": "paid", "paid_on": paid.isoformat()},
            headers=auth(token),
        )

        rows = client.get("/v1/commitments/occurrences", headers=auth(token)).json()

        assert rows[0]["paid_on"] == paid.isoformat()

    def test_marking_it_pending_again_clears_it(self, client, token):
        row = self._first(client, token)
        client.patch(
            f"/v1/commitments/occurrences/{row['id']}",
            json={"status": "paid"},
            headers=auth(token),
        )

        body = client.patch(
            f"/v1/commitments/occurrences/{row['id']}",
            json={"status": "pending"},
            headers=auth(token),
        ).json()

        assert body["paid_on"] is None
        assert body["paid_at"] is None

    def test_skipping_records_no_payment_date(self, client, token):
        row = self._first(client, token)

        body = client.patch(
            f"/v1/commitments/occurrences/{row['id']}",
            json={"status": "skipped"},
            headers=auth(token),
        ).json()

        assert body["paid_on"] is None

    def test_the_schedule_does_not_move(self, client, token):
        created = create(client, token, netflix())
        row = client.get("/v1/commitments/occurrences", headers=auth(token)).json()[0]
        client.patch(
            f"/v1/commitments/occurrences/{row['id']}",
            json={"status": "paid", "paid_on": (today_utc() - timedelta(days=2)).isoformat()},
            headers=auth(token),
        )

        rows = client.get(
            "/v1/commitments/occurrences",
            params={
                "start": today_utc().isoformat(),
                "end": (today_utc() + timedelta(days=60)).isoformat(),
            },
            headers=auth(token),
        ).json()
        body = client.get(f"/v1/commitments/{created['id']}", headers=auth(token)).json()

        upcoming = [row["due_date"] for row in rows if row["status"] == "pending"]
        assert body["next_due_date"] == upcoming[0]
        assert upcoming[0] == add_months(today_utc(), 1).isoformat()

