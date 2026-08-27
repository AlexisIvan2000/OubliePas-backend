import re

import pytest

from services.emailing.messages import MESSAGES

pytestmark = pytest.mark.unit

# Les courriels vouvoient, comme le reste de l'application. La derive se fait
# une cle a la fois, en ajoutant un message ecrit sur le ton des precedents :
# ce filet la refuse a l'ajout plutot qu'a la relecture.
TUTOIEMENT = re.compile(
    r"\b(tu|ton|ta|tes|toi)\b"
    r"|\bt'(?:as|es|est|a)\b"
    r"|\b(?:utilise|ignore|regarde|clique|ouvre|choisis|verifie|vérifie)\b"
    r"|réponds|reçois|dois\b",
    re.IGNORECASE,
)


def tutoyants(locale):
    return {
        cle: valeur
        for cle, valeur in MESSAGES[locale].items()
        if isinstance(valeur, str) and TUTOIEMENT.search(valeur)
    }


class TestTheFrenchMailsUseVous:
    def test_no_message_addresses_the_reader_as_tu(self):
        assert tutoyants("fr") == {}

    def test_the_detector_would_catch_a_relapse(self):
        # Sans ce controle, le test precedent resterait vert le jour ou
        # l'expression cesserait de reconnaitre quoi que ce soit.
        assert TUTOIEMENT.search("Si tu n'as pas créé de compte, ignore ce message.")
        assert TUTOIEMENT.search("Utilise ce code pour confirmer ton adresse.")
        assert TUTOIEMENT.search("Ne réponds pas directement à ce courriel.")

    def test_it_leaves_the_polite_forms_alone(self):
        assert not TUTOIEMENT.search("Utilisez ce code pour confirmer votre adresse.")
        assert not TUTOIEMENT.search("Ne répondez pas directement à ce courriel.")
        assert not TUTOIEMENT.search("Voici vos prochaines échéances :")

    def test_both_languages_still_carry_the_same_keys(self):
        assert set(MESSAGES["fr"]) == set(MESSAGES["en"])
