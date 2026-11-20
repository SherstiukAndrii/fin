"""Domain value objects: deterministic, offline and independent of storage and UI."""

from fin.domain.money import CurrencyMismatchError, Money

__all__ = ["CurrencyMismatchError", "Money"]
