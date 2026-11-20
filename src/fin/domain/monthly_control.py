"""Immutable monthly account observations, separate from the actual cash ledger."""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date

from fin.domain.money import Money


def is_month_end(day: date) -> bool:
    """Monthly control observations always represent closing month-end balances."""
    return type(day) is date and day.day == monthrange(day.year, day.month)[1]


@dataclass(frozen=True, slots=True)
class MonthlyControlBalance:
    account_id: str
    observed: Money
    calculated_at_capture: Money
    correction_event_id: str | None

    @property
    def recorded_difference(self) -> Money:
        return self.observed - self.calculated_at_capture


@dataclass(frozen=True, slots=True)
class MonthlyControl:
    id: str
    as_of: date
    balances: tuple[MonthlyControlBalance, ...]
    note: str | None
