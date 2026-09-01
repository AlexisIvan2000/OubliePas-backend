import calendar
from datetime import date, datetime, timedelta, timezone

from models.db.commitments_db import Commitment
from repositories.commitment_repository import CommitmentRepository
from core.clock import today_in, today_utc

MONTHS_BY_FREQUENCY = {"monthly": 1, "quarterly": 3, "yearly": 12}
GENERATION_HORIZON_DAYS = 90
HORIZON_MARGIN_DAYS = 30
LONGEST_PERIOD_DAYS = {"weekly": 7, "monthly": 31, "quarterly": 92, "yearly": 366, "oneoff": 0}
MAX_OCCURRENCES_PER_COMMITMENT = 60


def horizon_days(frequency: str) -> int:
    # Au moins une période entière, sinon un engagement annuel n'a aucune
    # échéance en base la plupart de l'année et n'affiche plus rien.
    return max(
        GENERATION_HORIZON_DAYS,
        LONGEST_PERIOD_DAYS[frequency] + HORIZON_MARGIN_DAYS,
    )


# Réexporté : la définition vit dans core.clock, à côté de today_for.
__all__ = ["today_utc"]


def add_months(anchor: date, months: int) -> date:
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    return date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))


def nth_due_date(starts_on: date, frequency: str, index: int) -> date:
    if frequency == "weekly":
        return starts_on + timedelta(weeks=index)
    return add_months(starts_on, index * MONTHS_BY_FREQUENCY[frequency])


def _first_index(starts_on: date, frequency: str, floor: date) -> int:
    # Une période de marge : le calcul en mois ignore le jour du mois et pourrait
    # dépasser l'indice réel. Le filtre due >= floor jette l'itération en trop.
    if floor <= starts_on:
        return 0
    if frequency == "weekly":
        return max(0, (floor - starts_on).days // 7 - 1)
    elapsed = (floor.year - starts_on.year) * 12 + (floor.month - starts_on.month)
    return max(0, elapsed // MONTHS_BY_FREQUENCY[frequency] - 1)


def occurrence_dates(
    *,
    starts_on: date,
    frequency: str,
    ends_on: date | None,
    floor: date,
    horizon: date,
) -> list[date]:
    if horizon < floor:
        return []

    if frequency == "oneoff":
        # Seule exception au plancher : une facture ponctuelle datée d'hier a bel
        # et bien existé. Les fréquences récurrentes le gardent, sinon un
        # abonnement antidaté de deux ans fabriquerait deux ans d'historique.
        within_term = ends_on is None or starts_on <= ends_on
        if starts_on <= horizon and within_term:
            return [starts_on]
        return []

    dates: list[date] = []
    index = _first_index(starts_on, frequency, floor)
    while len(dates) < MAX_OCCURRENCES_PER_COMMITMENT:
        due = nth_due_date(starts_on, frequency, index)
        if due > horizon:
            break
        if ends_on is not None and due > ends_on:
            break
        if due >= floor:
            dates.append(due)
        index += 1
    return dates


class OccurrenceGenerator:
    def __init__(self, repo: CommitmentRepository):
        self.repo = repo

    def _rows(self, commitment: Commitment, today: date) -> list[dict]:
        dates = occurrence_dates(
            starts_on=commitment.starts_on,
            frequency=commitment.frequency,
            ends_on=commitment.ends_on,
            floor=today,
            horizon=today + timedelta(days=horizon_days(commitment.frequency)),
        )
        return [
            {
                "commitment_id": commitment.id,
                "user_id": commitment.user_id,
                "due_date": due,
                "amount": commitment.amount,
            }
            for due in dates
        ]

    # Exigé, jamais deviné : un plancher pris sur l'horloge du serveur décalerait
    # la génération d'un jour pour la moitié de la planète, sans que rien ne le
    # signale.
    async def sync(self, commitment: Commitment, *, today: date) -> int:
        if commitment.status != "active":
            return 0
        return await self.repo.add_occurrences(self._rows(commitment, today))

    async def resync(self, commitment: Commitment, *, today: date) -> int:
        reference = today
        await self.repo.delete_pending_occurrences_from(commitment.id, reference)
        return await self.sync(commitment, today=reference)

    async def sync_all_active(self, *, today: date | None = None) -> int:
        created = 0
        for commitment, zone in await self.repo.active_with_timezone():
            # « Avant aujourd'hui » ne veut pas dire la même chose à Tokyo et à
            # Moncton : le plancher est celui du propriétaire.
            created += await self.sync(commitment, today=today or today_in(zone))
        return created
