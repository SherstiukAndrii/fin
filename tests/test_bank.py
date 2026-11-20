"""Bank domain invariants: contracts, multiple coupons and actual settlements."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from fin.domain.bank import (
    ActualReceipt,
    BondInstrument,
    BondLot,
    BondPaymentTerm,
    Deposit,
    DepositPaymentTerm,
    PaymentKind,
    PaymentStatus,
    bond_schedule,
    deposit_schedule,
    payment_balance,
)
from fin.domain.money import CurrencyMismatchError, Money


def uah(value: str) -> Money:
    return Money(Decimal(value), "UAH")


def bond() -> BondLot:
    instrument = BondInstrument("UA1234567890", uah("1000"), date(2027, 12, 1))
    return BondLot(
        id="lot1",
        instrument=instrument,
        acquired_on=date(2027, 1, 15),
        quantity=2,
        unit_price=uah("965.25"),
        paid_fees=uah("12.50"),
    )


def deposit() -> Deposit:
    return Deposit(
        id="dep1",
        bank="Sample bank",
        principal=uah("5000"),
        opened_on=date(2027, 1, 1),
        matures_on=date(2027, 12, 31),
        annual_rate=Decimal("0.12"),
    )


def coupon_terms() -> list[BondPaymentTerm]:
    return [
        BondPaymentTerm(f"c{month}", PaymentKind.COUPON, date(2027, month, 1), uah("40"))
        for month in (3, 6, 9)
    ]


def test_bond_three_coupons_and_separate_redemption() -> None:
    terms = [
        *coupon_terms(),
        BondPaymentTerm("maturity", PaymentKind.PRINCIPAL, date(2027, 12, 1), uah("1000")),
    ]
    result = bond_schedule(bond(), terms)
    assert len(result) == 4
    assert [p.kind for p in result] == [
        PaymentKind.COUPON,
        PaymentKind.COUPON,
        PaymentKind.COUPON,
        PaymentKind.PRINCIPAL,
    ]
    assert [p.expected.amount for p in result] == [
        Decimal("80"),
        Decimal("80"),
        Decimal("80"),
        Decimal("2000"),
    ]
    assert sum(p.expected.amount for p in result[:3]) == Decimal("240")
    assert all(p.holding_id == "lot1" for p in result)
    assert len({p.id for p in result}) == 4


def test_no_implicit_maturity_coupon_or_principal() -> None:
    result = bond_schedule(bond(), coupon_terms())
    assert len(result) == 3
    assert all(p.kind == PaymentKind.COUPON for p in result)


def test_bond_lot_historical_cost_is_not_face_value() -> None:
    assert bond().cost_basis == uah("1943.00")
    assert bond().instrument.face_value == uah("1000")


def test_unknown_purchase_price_or_fees_do_not_become_zero() -> None:
    lot = bond()
    assert replace(lot, unit_price=None).cost_basis is None
    assert replace(lot, paid_fees=None).cost_basis is None
    assert replace(lot, paid_fees=uah("0")).cost_basis == uah("1930.50")


def test_bond_allows_explicit_partial_principal_schedule() -> None:
    terms = [
        BondPaymentTerm("part1", PaymentKind.PRINCIPAL, date(2027, 5, 1), uah("400")),
        BondPaymentTerm("part2", PaymentKind.PRINCIPAL, date(2027, 12, 1), uah("600")),
    ]
    assert [p.expected.amount for p in bond_schedule(bond(), terms)] == [
        Decimal("800"),
        Decimal("1200"),
    ]


def test_bond_rejects_principal_over_face_value() -> None:
    terms = [
        BondPaymentTerm("p1", PaymentKind.PRINCIPAL, date(2027, 5, 1), uah("600")),
        BondPaymentTerm("p2", PaymentKind.PRINCIPAL, date(2027, 12, 1), uah("500")),
    ]
    with pytest.raises(ValueError, match="exceeds"):
        bond_schedule(bond(), terms)


def test_bond_rejects_duplicate_terms() -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        bond_schedule(bond(), [coupon_terms()[0], coupon_terms()[0]])


def test_bond_rejects_earlier_payments_instead_of_inventing_entitlement() -> None:
    term = BondPaymentTerm("old", PaymentKind.COUPON, date(2027, 1, 1), uah("40"))
    with pytest.raises(ValueError, match="outside"):
        bond_schedule(bond(), [term])


def test_deposit_schedule_uses_total_amount_without_multiplication() -> None:
    terms = [
        DepositPaymentTerm("interest1", PaymentKind.INTEREST, date(2027, 6, 1), uah("250")),
        DepositPaymentTerm("interest2", PaymentKind.INTEREST, date(2027, 12, 1), uah("250")),
        DepositPaymentTerm("principal", PaymentKind.PRINCIPAL, date(2027, 12, 31), uah("5000")),
    ]
    result = deposit_schedule(deposit(), terms)
    assert [p.expected.amount for p in result] == [
        Decimal("250"),
        Decimal("250"),
        Decimal("5000"),
    ]
    assert result[0].holding_id == "dep1"


def test_deposit_rejects_principal_over_amount() -> None:
    terms = [
        DepositPaymentTerm("a", PaymentKind.PRINCIPAL, date(2027, 4, 1), uah("3000")),
        DepositPaymentTerm("b", PaymentKind.PRINCIPAL, date(2027, 9, 1), uah("3000")),
    ]
    with pytest.raises(ValueError, match="exceeds"):
        deposit_schedule(deposit(), terms)


def test_deposit_has_no_automatically_generated_interest() -> None:
    assert deposit_schedule(deposit(), []) == ()


def test_partial_and_overdue_payment_without_double_posting() -> None:
    payment = bond_schedule(bond(), coupon_terms())[0]
    first = ActualReceipt("r1", payment.id, date(2027, 3, 3), uah("50"))
    second = ActualReceipt("r2", payment.id, date(2027, 3, 10), uah("30"))
    before = payment_balance(payment, [first, second], date(2027, 3, 1))
    assert before.received == uah("0")
    assert before.outstanding == uah("80")
    assert before.status == PaymentStatus.EXPECTED
    partial = payment_balance(payment, [first, second], date(2027, 3, 5))
    assert partial.received == uah("50")
    assert partial.outstanding == uah("30")
    assert partial.status == PaymentStatus.OVERDUE
    complete = payment_balance(payment, [first, second], date(2027, 3, 10))
    assert complete.received == uah("80")
    assert complete.outstanding == uah("0")
    assert complete.status == PaymentStatus.PAID


def test_partial_before_due_date() -> None:
    payment = bond_schedule(bond(), coupon_terms())[0]
    receipt = ActualReceipt("early", payment.id, date(2027, 2, 25), uah("10"))
    assert payment_balance(payment, [receipt], date(2027, 2, 28)).status == PaymentStatus.PARTIAL


def test_unpaid_payment_becomes_overdue_after_due_date() -> None:
    payment = bond_schedule(bond(), coupon_terms())[0]
    assert payment_balance(payment, [], date(2027, 3, 2)).status == PaymentStatus.OVERDUE


def test_duplicate_actual_receipt_id_is_rejected() -> None:
    payment = bond_schedule(bond(), coupon_terms())[0]
    receipt = ActualReceipt("same", payment.id, date(2027, 3, 1), uah("10"))
    with pytest.raises(ValueError, match="Duplicate"):
        payment_balance(payment, [receipt, receipt], date(2027, 3, 1))


def test_excess_receipts_are_recorded_as_actual() -> None:
    payment = bond_schedule(bond(), coupon_terms())[0]
    receipt = ActualReceipt("extra", payment.id, date(2027, 3, 2), uah("81"))
    balance = payment_balance(payment, [receipt], date(2027, 3, 2))
    assert balance.received == uah("81")
    assert balance.outstanding == uah("0")
    assert balance.status == PaymentStatus.PAID


def test_receipt_cannot_settle_another_obligation() -> None:
    payment = bond_schedule(bond(), coupon_terms())[0]
    receipt = ActualReceipt("wrong", "another", date(2027, 3, 2), uah("10"))
    with pytest.raises(ValueError, match="another payment"):
        payment_balance(payment, [receipt], date(2027, 3, 2))


def test_currency_mismatch_is_rejected_for_terms_and_receipts() -> None:
    term = BondPaymentTerm("fx", PaymentKind.COUPON, date(2027, 3, 1), Money(Decimal("40"), "USD"))
    with pytest.raises(CurrencyMismatchError):
        bond_schedule(bond(), [term])
    payment = bond_schedule(bond(), coupon_terms())[0]
    receipt = ActualReceipt("fx", payment.id, date(2027, 3, 2), Money(Decimal("10"), "USD"))
    with pytest.raises(CurrencyMismatchError):
        payment_balance(payment, [receipt], date(2027, 3, 2))


def test_invalid_bond_quantity_and_deposit_rate() -> None:
    with pytest.raises(ValueError, match="positive"):
        BondLot("bad", bond().instrument, date(2027, 1, 1), 0, uah("900"), uah("0"))
    with pytest.raises(TypeError, match="integer"):
        BondLot("bad", bond().instrument, date(2027, 1, 1), True, uah("900"), uah("0"))
    with pytest.raises(ValueError, match="finite"):
        Deposit("bad", "Bank", uah("100"), date(2027, 1, 1), date(2027, 2, 1), Decimal("NaN"))


def test_kind_and_dates_are_validated() -> None:
    with pytest.raises(ValueError, match="Bond terms"):
        BondPaymentTerm("x", PaymentKind.INTEREST, date(2027, 3, 1), uah("1"))
    with pytest.raises(ValueError, match="Deposit terms"):
        DepositPaymentTerm("x", PaymentKind.COUPON, date(2027, 3, 1), uah("1"))
    with pytest.raises(ValueError, match="outside"):
        deposit_schedule(
            deposit(),
            [DepositPaymentTerm("early", PaymentKind.INTEREST, date(2026, 12, 1), uah("1"))],
        )
