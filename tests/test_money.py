"""Invariants for the Money value object (no live data, network or database)."""

from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

import pytest

from fin.domain.money import CurrencyMismatchError, Money


def test_money_preserves_precision_without_implicit_rounding() -> None:
    amount = Decimal("123.123456789123456789123456789")
    money = Money(amount, "UAH")
    assert money.amount == amount
    assert money.amount.as_tuple() == amount.as_tuple()


def test_money_is_immutable_and_hashable() -> None:
    money = Money(Decimal("5.00"), "USD")
    with pytest.raises(AttributeError):
        money.amount = Decimal("8.00")
    assert {money} == {Money(Decimal("5.00"), "USD")}


def test_add_and_subtract_same_currency() -> None:
    a = Money(Decimal("100.20"), "UAH")
    b = Money(Decimal("0.03"), "UAH")
    assert (a + b).amount == Decimal("100.23")
    assert (a - b).amount == Decimal("100.17")
    assert (-b).amount == Decimal("-0.03")


@pytest.mark.parametrize("operation", ["add", "subtract"])
def test_different_currencies_rejected(operation: str) -> None:
    uah = Money(Decimal("10"), "UAH")
    usd = Money(Decimal("10"), "USD")
    with pytest.raises(CurrencyMismatchError, match="FX conversion"):
        if operation == "add":
            uah + usd
        else:
            uah - usd


@pytest.mark.parametrize("bad", [1.25, 1, "1.25", None])
def test_only_decimal_amounts_are_accepted(bad: object) -> None:
    with pytest.raises(TypeError, match="Decimal"):
        Money(bad, "UAH")


@pytest.mark.parametrize("bad", ["uah", "", "USD UAH", " USD", "₴", None])
def test_invalid_currency_identifiers_are_rejected(bad: object) -> None:
    with pytest.raises(ValueError, match="Currency"):
        Money(Decimal("1"), bad)


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_nonfinite_values_are_rejected(bad: Decimal) -> None:
    with pytest.raises(ValueError, match="finite"):
        Money(bad, "UAH")


def test_non_iso_currency_identifiers_supported() -> None:
    assert Money(Decimal("0.00000001"), "BTC").currency == "BTC"
    assert Money(Decimal("1"), "USDT").currency == "USDT"


def test_scalar_multiplication_and_division_are_exact_when_terminating() -> None:
    money = Money(Decimal("10.25"), "EUR")
    assert (money * Decimal("2.5")).amount == Decimal("25.625")
    assert (3 * money).amount == Decimal("30.75")
    assert (money / 2).amount == Decimal("5.125")


@pytest.mark.parametrize("factor", [0.5, "2", True])
def test_non_decimal_scalars_and_boolean_are_rejected(factor: object) -> None:
    money = Money(Decimal("10"), "UAH")
    with pytest.raises(TypeError):
        money * factor
    with pytest.raises(TypeError):
        money / factor


def test_division_by_zero_raises() -> None:
    with pytest.raises(ZeroDivisionError):
        Money(Decimal("3"), "UAH") / Decimal("0")


def test_rounding_is_explicit_and_policy_dependent() -> None:
    money = Money(Decimal("1.005"), "UAH")
    assert money.rounded(decimal_places=2, rounding=ROUND_HALF_UP).amount == Decimal("1.01")
    assert money.rounded(decimal_places=2, rounding=ROUND_HALF_EVEN).amount == Decimal("1.00")
    assert money.amount == Decimal("1.005")


def test_rounding_supports_different_instrument_precision() -> None:
    money = Money(Decimal("0.000000015"), "BTC")
    rounded = money.rounded(decimal_places=8, rounding=ROUND_HALF_UP)
    assert rounded.amount == Decimal("0.00000002")
    assert rounded.currency == "BTC"


@pytest.mark.parametrize("places", [-1, 1.0, True])
def test_invalid_rounding_places_rejected(places: object) -> None:
    with pytest.raises((ValueError, TypeError)):
        Money(Decimal("1.22"), "UAH").rounded(decimal_places=places, rounding=ROUND_HALF_UP)


def test_unsupported_operations_do_not_coerce_or_convert() -> None:
    money = Money(Decimal("10"), "UAH")
    with pytest.raises(TypeError):
        money + Decimal("1")
    with pytest.raises(TypeError):
        money * Money(Decimal("2"), "UAH")
