from dataclasses import dataclass
from datetime import date, timedelta

from models.db.commitments_db import MIN_TRIAL_NOTICE_DAYS, Commitment

TRIAL = "trial"
CANCELLATION = "cancellation"


@dataclass(frozen=True)
class ActionWindow:
    deadline: date
    reason: str
    lead_days: int

    def opens_on(self) -> date:
        return self.deadline - timedelta(days=self.lead_days)

    def is_open(self, reference: date) -> bool:
        return self.opens_on() <= reference <= self.deadline

    def days_left(self, reference: date) -> int:
        return (self.deadline - reference).days


def _trial_window(commitment: Commitment, due_date: date) -> ActionWindow | None:
    if commitment.trial_ends_on is None or due_date != commitment.starts_on:
        return None
    return ActionWindow(
        deadline=commitment.trial_ends_on,
        reason=TRIAL,
        lead_days=max(commitment.reminder_days_before, MIN_TRIAL_NOTICE_DAYS),
    )


def _cancellation_window(commitment: Commitment, due_date: date) -> ActionWindow | None:
    if commitment.cancellation_notice_days is None:
        return None
    return ActionWindow(
        deadline=due_date - timedelta(days=commitment.cancellation_notice_days),
        reason=CANCELLATION,
        lead_days=commitment.reminder_days_before,
    )


def action_window(commitment: Commitment, due_date: date) -> ActionWindow | None:
    windows = [
        window
        for window in (
            _trial_window(commitment, due_date),
            _cancellation_window(commitment, due_date),
        )
        if window is not None
    ]
    if not windows:
        return None
    return min(windows, key=lambda window: (window.deadline, window.reason))
