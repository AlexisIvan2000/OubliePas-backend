import io
import re

import pytest

from core.config import to_async_url

pytestmark = pytest.mark.unit

ENV = "alembic/env.py"


class TestAsyncRewrite:
    def test_a_plain_postgres_url_gains_the_async_driver(self):
        assert to_async_url("postgresql://u:p@host:5432/db") == (
            "postgresql+asyncpg://u:p@host:5432/db"
        )

    def test_an_url_that_already_names_the_driver_is_left_alone(self):
        url = "postgresql+asyncpg://u:p@host/db"

        assert to_async_url(url) == url

    def test_only_the_scheme_is_rewritten(self):
        # Le mot postgresql peut figurer dans le nom de la base ou de l'hote :
        # une substitution globale les abimerait.
        assert to_async_url("postgresql://u:p@host/postgresql") == (
            "postgresql+asyncpg://u:p@host/postgresql"
        )

    def test_another_scheme_is_not_touched(self):
        assert to_async_url("postgres://u:p@host/db") == "postgres://u:p@host/db"


class TestAlembicUsesTheSameRule:
    def test_it_rewrites_the_url_given_on_the_command_line(self):
        # Sans cela, coller l'URL du tableau de bord dans -x db_url echoue sur
        # une erreur de pilote, et le message affiche le mot de passe.
        source = io.open(ENV, encoding="utf-8").read()

        assert "to_async_url(_x.get(" in source

    def test_it_escapes_the_percent_sign(self):
        # set_main_option passe par configparser, qui interprete les % : un mot
        # de passe encode y perdrait des caracteres en silence.
        source = io.open(ENV, encoding="utf-8").read()

        assert re.search(r'\.replace\("%", "%%"\)', source)
