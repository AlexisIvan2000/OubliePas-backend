import re
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent.parent

# Les couches qui repondent a quelqu'un. Un « aujourd'hui » calcule ici sans son
# fuseau donne le jour du serveur : a 22 h en Atlantique le tableau de bord
# affichait deja le mois suivant, et la page voisine le mois courant.
COUCHES = ("services", "api", "repositories")

INTERDITS = re.compile(r"\b(today_utc\(\)|date\.today\(\)|datetime\.today\(\))")

# Ce qui n'appartient a personne a le droit de lire l'horloge du serveur.
TOLERES = {
    # La definition elle-meme, et sa voisine today_for.
    Path("core/clock.py"),
    # Purge, journaux, verrou : aucun de ces calculs n'atterrit sur un ecran.
    Path("jobs/daily.py"),
}


def sources() -> list[Path]:
    fichiers = []
    for couche in COUCHES:
        fichiers.extend(sorted((RACINE / couche).rglob("*.py")))
    return [f for f in fichiers if f.relative_to(RACINE) not in TOLERES]


def lignes_fautives(chemin: Path) -> list[str]:
    relatif = chemin.relative_to(RACINE).as_posix()
    fautes = []
    for numero, ligne in enumerate(chemin.read_text(encoding="utf-8").splitlines(), 1):
        nu = ligne.split("#", 1)[0]
        if INTERDITS.search(nu):
            fautes.append(f"{relatif}:{numero} {ligne.strip()}")
    return fautes


class TestOnlyOneDefinitionOfToday:
    def test_no_layer_that_answers_someone_reads_the_server_clock(self):
        # La garde qui compte. Le jour d'une personne se lit avec today_for ou
        # today_in ; celui du serveur ne convient qu'a ce que personne ne
        # regarde, et cette liste-la est courte et nommee plus haut.
        fautes = [faute for chemin in sources() for faute in lignes_fautives(chemin)]

        assert fautes == [], (
            "today_utc() ou date.today() dans une couche qui repond a quelqu'un : "
            "utiliser today_for(user) ou today_in(fuseau)"
        )

    def test_the_sweep_actually_reads_something(self):
        # Un chemin faux ne trouverait aucun fichier et la garde passerait a
        # vide, ce qui se lit exactement comme un succes.
        assert len(sources()) > 20

    @pytest.mark.parametrize(
        "extrait",
        ["today = today_utc()", "reference = date.today()", "x = datetime.today()"],
    )
    def test_the_detector_catches_what_it_is_meant_to(self, tmp_path, extrait):
        faux = tmp_path / "faux.py"
        faux.write_text(f"def f():\n    {extrait}\n", encoding="utf-8")

        assert INTERDITS.search(extrait)

    @pytest.mark.parametrize(
        "extrait",
        [
            "today = today_for(user)",
            "reference = today_in(zone)",
            "from core.clock import today_utc",
            "# today_utc() est reserve aux purges",
        ],
    )
    def test_and_leaves_the_rest_alone(self, extrait):
        nu = extrait.split("#", 1)[0]

        assert not INTERDITS.search(nu)


class TestWhatIsAllowedToUseTheServerClock:
    def test_the_tolerated_files_exist(self):
        # Une tolerance qui pointe un fichier disparu elargit la garde en
        # silence : elle ne protegerait plus rien de ce qu'elle nomme.
        for chemin in TOLERES:
            assert (RACINE / chemin).exists(), f"{chemin} tolere mais absent"

    def test_the_services_cannot_fall_back_to_the_server_clock(self):
        # Les points d'entree du cron exigent leur date au lieu de la deviner :
        # un parametre optionnel serait un « aujourd'hui » de serveur qui
        # revient par la porte de derriere, sans qu'aucun appel ne change.
        for chemin, signature in (
            ("services/notifications/reminder_service.py", "async def send_due(self, *, at: datetime)"),
            ("services/notifications/weekly_digest.py", "async def send(self, *, at: datetime)"),
            ("services/commitments/occurrence_generator.py", "async def sync(self, commitment: Commitment, *, today: date)"),
        ):
            source = (RACINE / chemin).read_text(encoding="utf-8")
            assert signature in source, f"{chemin} : {signature} attendue"
