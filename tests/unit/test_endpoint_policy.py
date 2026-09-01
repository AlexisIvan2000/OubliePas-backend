import pytest

from services.pushing.endpoint_policy import refusal_reason, refused_host

ACCEPTES = [
    "https://fcm.googleapis.com/fcm/send/eXempleDeJeton",
    "https://android.googleapis.com/gcm/send/eXempleDeJeton",
    "https://updates.push.services.mozilla.com/wpush/v2/eXempleDeJeton",
    "https://wns2-par02p.notify.windows.com/w/?token=eXempleDeJeton",
    "https://db5p.notify.live.net/w/?token=eXempleDeJeton",
    "https://web.push.apple.com/eXempleDeJeton",
    "https://fcm.googleapis.com:443/fcm/send/eXempleDeJeton",
    "https://FCM.GoogleAPIs.COM/fcm/send/eXempleDeJeton",
]


class TestTheAddressesOfRealPushServices:
    @pytest.mark.parametrize("endpoint", ACCEPTES)
    def test_they_pass(self, endpoint):
        assert refusal_reason(endpoint) is None

    def test_the_comparison_ignores_the_case_of_the_host(self):
        # C'est urlsplit qui normalise, pas nous : hostname rend l'hote en
        # minuscules. Le test garde la propriete, la comparaison n'a pas a la
        # refaire — et elle disparait avec hostname si on lui prefere netloc,
        # ce que le cas du champ utilisateur plus bas surveille.
        assert refusal_reason("https://Web.Push.Apple.com/jeton") is None


class TestWhatTheFloorRefuses:
    def test_a_plain_http_address(self):
        assert refusal_reason("http://fcm.googleapis.com/fcm/send/x") == "scheme"

    @pytest.mark.parametrize(
        "endpoint",
        [
            "https://169.254.169.254/latest/meta-data/",
            "https://127.0.0.1:443/",
            "https://10.0.0.5/",
            "https://[::1]/",
        ],
    )
    def test_an_address_written_as_a_number(self, endpoint):
        # Le cas qui a ouvert le constat : le service poste ou on lui dit, et
        # l'etat de la reponse revient a l'appelant.
        assert refusal_reason(endpoint) == "ip-literal"

    @pytest.mark.parametrize(
        "endpoint",
        [
            "https://localhost/",
            "https://redis.railway.internal/",
            "https://imprimante.local/",
            "https://metadata.google.internal/computeMetadata/v1/",
        ],
    )
    def test_a_name_that_only_exists_inside(self, endpoint):
        assert refusal_reason(endpoint) == "internal"

    def test_a_port_that_is_not_the_web(self):
        assert refusal_reason("https://fcm.googleapis.com:6379/") == "port"

    def test_an_address_with_no_host_at_all(self):
        assert refusal_reason("https:///fcm/send/x") == "no-host"

    def test_anything_that_is_not_a_push_service(self):
        assert refusal_reason("https://exemple.test/collecteur") == "not-allowed"


class TestTheImpersonations:
    def test_a_host_that_merely_starts_like_one_of_ours(self):
        assert refusal_reason("https://fcm.googleapis.com.exemple.test/x") == "not-allowed"

    def test_a_host_that_merely_ends_like_one_of_ours(self):
        # Le point du suffixe porte la garde : sans lui, ce nom passerait pour
        # un hote de Microsoft.
        assert refusal_reason("https://evilnotify.windows.com/w/") == "not-allowed"

    def test_the_allowed_host_hidden_in_the_user_field(self):
        # netloc rendrait l'hote autorise ; c'est hostname qui rend la cible.
        assert (
            refusal_reason("https://fcm.googleapis.com@redis.railway.internal/") == "internal"
        )

    def test_the_same_trick_pointing_at_a_number(self):
        assert refusal_reason("https://web.push.apple.com@169.254.169.254/") == "ip-literal"

    def test_a_trailing_dot_does_not_buy_a_new_host(self):
        # « fcm.googleapis.com. » designe le meme hote pour un resolveur, et
        # aurait echappe a une comparaison litterale.
        assert refusal_reason("https://fcm.googleapis.com./fcm/send/x") is None

    def test_a_subdomain_of_an_exact_host_is_not_the_host(self):
        assert refusal_reason("https://x.fcm.googleapis.com/fcm/send/x") == "not-allowed"


class TestWhatTheLogIsAllowedToSay:
    def test_it_names_the_host_and_nothing_else(self):
        # L'adresse complete vaut porteur d'autorite : qui la connait peut
        # reprendre l'abonnement d'un autre compte.
        endpoint = "https://exemple.test/collecteur?jeton=secret-de-la-victime"

        trace = refused_host(endpoint)

        assert trace == "exemple.test"
        assert "secret-de-la-victime" not in trace

    def test_an_unreadable_address_still_leaves_a_line(self):
        assert refused_host("pas une adresse") == "?"
