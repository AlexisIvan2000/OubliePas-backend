import base64

import pytest
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid01

from core.config import check_vapid_keys

SUBJECT = "mailto:rappels@oubliepas.com"


def pair():
    vapid = Vapid01()
    vapid.generate_keys()
    key = vapid.private_key
    b64 = lambda raw: base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return (
        b64(key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)),
        b64(key.private_numbers().private_value.to_bytes(32, "big")),
    )


def pem():
    vapid = Vapid01()
    vapid.generate_keys()
    return vapid.private_pem().decode()


class TestAbsentKeys:
    @pytest.mark.parametrize("public,private", [(None, None), ("", ""), (None, "")])
    def test_no_pair_at_all_lets_the_api_start(self, public, private):
        check_vapid_keys(public, private, SUBJECT)

    def test_the_subject_alone_is_not_a_reason_to_refuse(self):
        check_vapid_keys(None, None, "pas-une-url")


class TestIncompletePair:
    def test_a_public_key_without_its_private_half_is_refused(self):
        public, _ = pair()
        with pytest.raises(RuntimeError, match="VAPID_PRIVATE_KEY"):
            check_vapid_keys(public, None, SUBJECT)

    def test_a_private_key_without_its_public_half_is_refused(self):
        _, private = pair()
        with pytest.raises(RuntimeError, match="VAPID_PUBLIC_KEY"):
            check_vapid_keys(None, private, SUBJECT)


class TestUnreadablePrivateKey:
    def test_a_pem_is_refused_at_startup(self):
        # Le piege exact : la commande `vapid` ecrit des .pem, et from_raw les
        # rejette a la premiere signature, donc au premier clic d'un utilisateur.
        public, _ = pair()
        with pytest.raises(RuntimeError, match="VAPID_PRIVATE_KEY"):
            check_vapid_keys(public, pem(), SUBJECT)

    @pytest.mark.parametrize("private", ["pas-une-cle", "AAAA", "!!!!", "0" * 43])
    def test_anything_that_is_not_a_raw_key_is_refused(self, private):
        public, _ = pair()
        with pytest.raises(RuntimeError, match="VAPID_PRIVATE_KEY"):
            check_vapid_keys(public, private, SUBJECT)


class TestMismatchedPair:
    def test_two_halves_from_different_pairs_are_refused(self):
        # Aucune exception ne se leverait a l'usage : le navigateur s'abonne avec
        # la cle annoncee et le service de push rejette nos envois en silence.
        stale, _ = pair()
        _, private = pair()
        with pytest.raises(RuntimeError, match="VAPID_PUBLIC_KEY"):
            check_vapid_keys(stale, private, SUBJECT)


class TestAcceptedPair:
    def test_a_matching_pair_passes(self):
        public, private = pair()
        check_vapid_keys(public, private, SUBJECT)

    def test_padding_and_stray_spaces_do_not_condemn_a_good_pair(self):
        public, private = pair()
        check_vapid_keys(f"  {public}==  ", f"  {private}  ", SUBJECT)

    @pytest.mark.parametrize("subject", ["mailto:ops@oubliepas.com", "https://oubliepas.com"])
    def test_the_two_forms_push_services_accept(self, subject):
        public, private = pair()
        check_vapid_keys(public, private, subject)

    @pytest.mark.parametrize("subject", ["", None, "oubliepas.com", "ops@oubliepas.com"])
    def test_a_subject_no_push_service_accepts_is_refused(self, subject):
        public, private = pair()
        with pytest.raises(RuntimeError, match="VAPID_SUBJECT"):
            check_vapid_keys(public, private, subject)
