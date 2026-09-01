from datetime import timedelta

import pytest

from jobs.daily import run_daily
from services.commitments.occurrence_generator import today_utc

pytestmark = pytest.mark.integration

# Assez loin pour que l'echeance entre dans la fenetre de preavis puis en
# ressorte, sans jamais toucher la borne des retards.
PREAVIS = 3


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def token(verified):
    return verified["tokens"]["access_token"]


@pytest.fixture
def run_job(session_runner):
    def go(**kwargs):
        return session_runner(lambda session: run_daily(session, **kwargs))

    return go


@pytest.fixture
def rappels(mailbox):
    return lambda kind="reminder": [m for m in mailbox if m["kind"] == kind]


def facture(client, token, jour, lead=PREAVIS):
    reponse = client.post(
        "/v1/commitments",
        json={
            "title": "Electricite",
            "type": "invoice",
            "category": "energy",
            "amount": "100.00",
            "frequency": "oneoff",
            "starts_on": jour.isoformat(),
            "reminder_days_before": lead,
        },
        headers=auth(token),
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


class TestNoReminderIsEverMissed:
    """La fenetre est un intervalle, jamais une egalite de date. C'est ce qui
    rend un passage saute rattrapable, et ce qui permet a la selection de
    dependre du fuseau de chacun sans rien perdre en chemin."""

    def test_a_due_date_crosses_the_window_and_is_taken_exactly_once(
        self, client, token, run_job, rappels
    ):
        echeance = today_utc() + timedelta(days=PREAVIS)
        facture(client, token, echeance)

        # Chaque jour, de bien avant l'ouverture a bien apres.
        for decalage in range(-2, PREAVIS + 2):
            run_job(today=today_utc() + timedelta(days=decalage))

        assert len(rappels()) == 1

    def test_a_skipped_pass_is_caught_up_by_the_next_one(
        self, client, token, run_job, rappels
    ):
        # Le jour d'ouverture est saute — panne, deploiement, machine eteinte.
        # Le lendemain doit encore trouver l'echeance.
        echeance = today_utc() + timedelta(days=PREAVIS)
        facture(client, token, echeance)
        jour_douverture = today_utc()

        run_job(today=jour_douverture + timedelta(days=1))

        assert len(rappels()) == 1

    def test_even_several_skipped_passes_in_a_row(self, client, token, run_job, rappels):
        echeance = today_utc() + timedelta(days=PREAVIS)
        facture(client, token, echeance)

        run_job(today=echeance)

        assert len(rappels()) == 1

    def test_a_due_date_already_past_never_comes_back_as_a_notice(
        self, client, token, run_job, rappels
    ):
        # La borne basse du preavis. Sans elle, une echeance depassee
        # ressortirait en « a venir » — annoncee comme future le jour meme ou le
        # rappel de retard la donne pour en retard.
        facture(client, token, today_utc() - timedelta(days=1))

        run_job(today=today_utc())

        assert rappels("reminder") == []

    def test_but_a_pass_before_the_window_opens_sends_nothing(
        self, client, token, run_job, rappels
    ):
        # La garde inverse : si tout partait toujours, « une fois exactement »
        # serait vrai pour de mauvaises raisons.
        echeance = today_utc() + timedelta(days=PREAVIS + 10)
        facture(client, token, echeance)

        run_job(today=today_utc())

        assert rappels() == []


class TestNoDuplicate:
    """Le journal des rappels porte (echeance, famille, canal) et rien d'autre.
    Il ne connait ni l'heure ni le fuseau : c'est pour cela qu'un second passage
    dans la meme journee, ou une bascule de fuseau, ne renvoie rien."""

    def test_two_passes_the_same_day_send_one_reminder(
        self, client, token, run_job, rappels
    ):
        facture(client, token, today_utc() + timedelta(days=PREAVIS))

        run_job(today=today_utc())
        run_job(today=today_utc())

        assert len(rappels()) == 1

    def test_and_the_days_that_follow_send_nothing_more(
        self, client, token, run_job, rappels
    ):
        facture(client, token, today_utc() + timedelta(days=PREAVIS))
        run_job(today=today_utc())

        for decalage in range(1, 4):
            run_job(today=today_utc() + timedelta(days=decalage))

        assert len(rappels()) == 1

    def test_a_pass_replayed_for_a_past_date_sends_nothing_either(
        self, client, token, run_job, rappels
    ):
        # Rejouer une date passee est une operation d'exploitation ordinaire.
        # Elle ne doit pas ecrire une seconde fois a la meme personne.
        facture(client, token, today_utc() + timedelta(days=PREAVIS))
        run_job(today=today_utc())

        run_job(today=today_utc() - timedelta(days=1))
        run_job(today=today_utc())

        assert len(rappels()) == 1


class TestEmailAndPushAreTreatedTheSame:
    """Deux canaux, un seul calcul de selection. Le journal les distingue par sa
    colonne de canal, sinon allumer le push eteindrait le courriel."""

    @pytest.fixture
    def push_actif(self, client, token, pushbox, monkeypatch):
        # push_configured est faux en test : sans cette bascule le canal ne
        # serait jamais essaye, et ces tests ne prouveraient rien.
        import services.notifications.reminder_service as service

        monkeypatch.setattr(service, "push_configured", lambda: True)
        client.patch(
            "/v1/users/me", json={"reminder_push_enabled": True}, headers=auth(token)
        )
        client.post(
            "/v1/push/subscriptions",
            json={
                "endpoint": "https://fcm.googleapis.com/fcm/send/appareil",
                "p256dh": "cle",
                "auth": "secret",
            },
            headers=auth(token),
        )
        return pushbox

    def test_the_same_due_date_reaches_both_channels(
        self, client, token, run_job, rappels, push_actif
    ):
        facture(client, token, today_utc() + timedelta(days=PREAVIS))

        run_job(today=today_utc())

        assert len(rappels()) == 1
        assert len(push_actif) == 1

    def test_neither_channel_repeats_itself(
        self, client, token, run_job, rappels, push_actif
    ):
        facture(client, token, today_utc() + timedelta(days=PREAVIS))

        run_job(today=today_utc())
        run_job(today=today_utc())

        assert len(rappels()) == 1
        assert len(push_actif) == 1

    def test_one_channel_switched_off_does_not_silence_the_other(
        self, client, token, run_job, rappels, push_actif
    ):
        client.patch(
            "/v1/users/me", json={"reminder_email_enabled": False}, headers=auth(token)
        )
        facture(client, token, today_utc() + timedelta(days=PREAVIS))

        run_job(today=today_utc())

        assert rappels() == []
        assert len(push_actif) == 1

    def test_and_a_skipped_pass_is_caught_up_on_both(
        self, client, token, run_job, rappels, push_actif
    ):
        facture(client, token, today_utc() + timedelta(days=PREAVIS))

        run_job(today=today_utc() + timedelta(days=1))

        assert len(rappels()) == 1
        assert len(push_actif) == 1


class TestTheDayIsTheirs:
    """A midi UTC, l'ouest est toujours sur la meme date que le serveur — midi
    moins douze heures fait minuit. Seul l'extreme est bascule : a UTC+14 il est
    2 h le lendemain. C'est le seul endroit ou le jour du compte et celui du
    serveur different pendant le passage, et donc le seul qui prouve d'ou vient
    la date."""

    LIGNE_DE_DATE = "Pacific/Kiritimati"  # UTC+14

    @pytest.fixture
    def compte(self, client, mailbox):
        def creer(fuseau, email):
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

    def test_the_far_east_is_reminded_on_its_own_day(
        self, client, compte, run_job, rappels
    ):
        # Preavis nul : le rappel ne part que le jour de l'echeance. Pour ce
        # compte, ce jour est deja demain quand le serveur lance le passage.
        passage = today_utc()
        demain = passage + timedelta(days=1)
        token = compte(self.LIGNE_DE_DATE, "kiritimati@example.com")
        facture(client, token, demain, lead=0)

        run_job(today=passage)

        assert len(rappels()) == 1

    def test_while_a_utc_account_waits_a_day(self, client, compte, run_job, rappels):
        # Le meme jour d'echeance, le meme passage, un autre fuseau : rien ne
        # part. Sans le filtre par personne, les deux se ressembleraient.
        passage = today_utc()
        demain = passage + timedelta(days=1)
        token = compte("UTC", "greenwich@example.com")
        facture(client, token, demain, lead=0)

        run_job(today=passage)

        assert rappels() == []

    def test_and_that_account_gets_it_the_next_pass(
        self, client, compte, run_job, rappels
    ):
        # Rien n'est perdu : la fenetre est un intervalle, le passage suivant
        # le reprend.
        passage = today_utc()
        demain = passage + timedelta(days=1)
        token = compte("UTC", "greenwich@example.com")
        facture(client, token, demain, lead=0)

        run_job(today=passage)
        run_job(today=demain)

        assert len(rappels()) == 1
