"""Small, immutable money primitives; FX conversion is deliberately out of scope.

Amounts must be Decimal instances. Currency identifiers are explicit, uppercase,
and may include user-defined codes; no fixed minor-unit precision is assumed.
"""

import re
from dataclasses import dataclass
from decimal import Decimal


class CurrencyMismatchError(ValueError):
    """Arithmetic was attempted on amounts expressed in different currencies."""


@dataclass(frozen=True, slots=True)
class Money:
    """An exact decimal amount in one currency, without implicit conversion.

    No rounding occurs during construction or ordinary arithmetic. The caller
    chooses the number of decimal places and rounding policy only at a real
    posting or presentation boundary.
    """

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise TypeError("Money amount must be Decimal (not float or int)")
        if not self.amount.is_finite():
            raise ValueError("Money amount must be finite")
        if not isinstance(self.currency, str):
            raise ValueError("Currency must be an uppercase, nonempty identifier")
        if re.fullmatch(r"[A-Z][A-Z0-9]{0,15}", self.currency) is None:
            raise ValueError("Currency must be an uppercase, nonempty identifier")

    def _require_same_currency(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"Cannot combine {self.currency} and {other.currency} without FX conversion"
            )

    def __add__(self, other: object) -> "Money":
        if not isinstance(other, Money):
            return NotImplemented
        self._require_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: object) -> "Money":
        if not isinstance(other, Money):
            return NotImplemented
        self._require_same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> "Money":
        return Money(-self.amount, self.currency)

    def __mul__(self, multiplier: object) -> "Money":
        if isinstance(multiplier, bool) or not isinstance(multiplier, (Decimal, int)):
            return NotImplemented
        return Money(self.amount * multiplier, self.currency)

    def __rmul__(self, multiplier: object) -> "Money":
        return self.__mul__(multiplier)

    def __truediv__(self, divisor: object) -> "Money":
        if isinstance(divisor, bool) or not isinstance(divisor, (Decimal, int)):
            return NotImplemented
        return Money(self.amount / divisor, self.currency)

    def rounded(self, *, decimal_places: int, rounding: str) -> "Money":
        """Explicitly round for posting or display; never mutate the original.

        Example: money.rounded(decimal_places=2, rounding=ROUND_HALF_UP).
        Decimal context precision applies to arithmetic, as with Decimal itself.
        """
        if isinstance(decimal_places, bool) or not isinstance(decimal_places, int):
            raise TypeError("decimal_places must be a nonnegative integer")
        if decimal_places < 0:
            raise ValueError("decimal_places must be nonnegative")
        quantum = Decimal(1).scaleb(-decimal_places)
        return Money(self.amount.quantize(quantum, rounding=rounding), self.currency)
