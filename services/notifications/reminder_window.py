from datetime import date, timedelta

from models.db.commitments_db import (
    MAX_CANCELLATION_NOTICE_DAYS,
    MAX_REMINDER_DAYS,
    OVERDUE_REMINDER_DAYS,
    OVERDUE_REMINDER_WINDOW_DAYS,
)

NOTICE = "notice"
OVERDUE = "overdue"
ACTION = "action_required"

# De UTC-12 a UTC+14 : le jour d'une personne ne s'ecarte jamais de plus d'un
# jour de celui du serveur. La requete elargit donc d'un jour de chaque cote,
# parce que personne ne doit etre ecarte avant que sa propre date ait pu
# trancher ; le predicat reprend ensuite les memes bornes avec cette date-la.
TIMEZONE_SPREAD_DAYS = 1


def bounds(kind: str, on_date: date) -> tuple[date, date]:
    """Les bornes d'une famille, telles qu'elles valent pour un jour donne."""
    if kind == NOTICE:
        return on_date, on_date + timedelta(days=MAX_REMINDER_DAYS)
    if kind == OVERDUE:
        return (
            on_date - timedelta(days=OVERDUE_REMINDER_WINDOW_DAYS),
            on_date - timedelta(days=OVERDUE_REMINDER_DAYS),
        )
    return (
        on_date,
        on_date + timedelta(days=MAX_CANCELLATION_NOTICE_DAYS + MAX_REMINDER_DAYS),
    )


def query_bounds(kind: str, on_date: date) -> tuple[date, date]:
    """Les memes bornes, elargies de l'ecart maximal entre deux fuseaux."""
    earliest, latest = bounds(kind, on_date)
    marge = timedelta(days=TIMEZONE_SPREAD_DAYS)
    return earliest - marge, latest + marge


def is_due(kind: str, occurrence, commitment, today: date) -> bool:
    """Le meme calcul, exact, avec le jour de la personne concernee.

    Une echeance ecartee ici n'est pas perdue : les bornes forment un
    intervalle et non une egalite de date, donc le passage suivant la
    retrouvera. C'est ce qui rend un passage saute rattrapable.
    """
    earliest, latest = bounds(kind, today)
    if kind == NOTICE:
        # Le delai propre a l'engagement. La requete ne peut pas l'appliquer au
        # jour pres sans ecarter les fuseaux voisins ; elle le fait large, et
        # c'est ici que la borne devient juste.
        latest = min(latest, today + timedelta(days=commitment.reminder_days_before))
    return earliest <= occurrence.due_date <= latest
