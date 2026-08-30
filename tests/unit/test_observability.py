import logging

import pytest

from core.observability import (
    ANONYMOUS,
    ContextFilter,
    bind,
    current_caller,
    current_request_id,
    new_request_id,
)


def record() -> logging.LogRecord:
    return logging.LogRecord("test", logging.INFO, "f.py", 1, "message", None, None)


class TestTheSuppliedIdentifier:
    def test_a_sane_one_is_kept(self):
        # Le garder permet de suivre une requete a travers plusieurs services.
        assert new_request_id("abc-123_XY.z") == "abc-123_XY.z"

    @pytest.mark.parametrize(
        "forge",
        [
            "ligne\nINFO faux message",
            "ligne\rautre",
            "a" * 65,
            "",
            None,
            "espace interdit",
            "point-virgule;",
        ],
    )
    def test_anything_else_is_replaced(self, forge):
        # Un identifiant qui entre dans le journal sans filtre laisserait ecrire
        # des lignes entieres : une fausse trace se lirait comme une vraie.
        produit = new_request_id(forge)

        assert produit != forge
        assert len(produit) == 12
        assert produit.isalnum()

    def test_two_requests_never_share_one(self):
        assert new_request_id() != new_request_id()


class TestTheFilter:
    def test_outside_a_request_it_says_so(self):
        # Le cron journalise hors de toute requete : la ligne doit rester
        # formatable, pas exploser sur un attribut manquant.
        ligne = record()

        ContextFilter().filter(ligne)

        assert ligne.request_id == ANONYMOUS
        assert ligne.caller == ANONYMOUS

    def test_it_carries_whatever_was_bound(self):
        bind("req-1", "user-42")
        ligne = record()

        ContextFilter().filter(ligne)

        assert (ligne.request_id, ligne.caller) == ("req-1", "user-42")

    def test_it_never_refuses_a_line(self):
        # Un filtre qui rend faux avale la ligne : celui-ci n'est la que pour
        # enrichir, jamais pour trier.
        assert ContextFilter().filter(record()) is True

    def test_the_format_string_of_the_app_can_be_rendered(self):
        # La garde qui compte : un format qui reclame %(request_id)s sur une
        # ligne depourvue de l'attribut leve au moment de l'ecriture, c'est-a-dire
        # en production et nulle part ailleurs.
        formateur = logging.Formatter(
            "%(asctime)s %(levelname)s [%(request_id)s %(caller)s] %(name)s %(message)s"
        )
        ligne = record()
        ContextFilter().filter(ligne)

        assert "message" in formateur.format(ligne)


class TestBinding:
    def test_what_is_bound_is_readable(self):
        bind("req-9", "ip:203.0.113.7")

        assert current_request_id() == "req-9"
        assert current_caller() == "ip:203.0.113.7"
