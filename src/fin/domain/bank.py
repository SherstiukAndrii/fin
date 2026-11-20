"""Offline Bank domain: holdings, scheduled payments, and actual receipts.

Schedules describe contractual expectations, not booked cash movements.
No pricing, tax deduction, accrued interest, FX, or automatic reinvestment.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from fin.domain.money import CurrencyMismatchError, Money


def _identifier(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise ValueError("ID must contain only letters, digits, underscores or hyphens")


def _date(value: date) -> None:
    if type(value) is not date:
        raise TypeError("Dates must be calendar dates, not timestamps")


def _positive(amount: Money) -> None:
    if amount.amount <= 0:
        raise ValueError("Amount must be positive")


def _nonnegative(amount: Money) -> None:
    if amount.amount < 0:
        raise ValueError("Amount cannot be negative")


def _currency(amount: Money, currency: str) -> None:
    if amount.currency != currency:
        raise CurrencyMismatchError(f"Expected {currency}, got {amount.currency}")


class PaymentKind(StrEnum):
    COUPON = "coupon"
    INTEREST = "interest"
    PRINCIPAL = "principal"


class PaymentStatus(StrEnum):
    EXPECTED = "expected"
    PARTIAL = "partial"
    OVERDUE = "overdue"
    PAID = "paid"
    EXCESS = "excess"


@dataclass(frozen=True, slots=True)
class BondInstrument:
    isin: str
    face_value: Money
    matures_on: date

    def __post_init__(self) -> None:
        if (
            not isinstance(self.isin, str)
            or re.fullmatch(r"[A-Z]{2}[A-Z0-9]{10}", self.isin) is None
        ):
            raise ValueError("ISIN must have 12 uppercase alphanumeric characters")
        _positive(self.face_value)
        _date(self.matures_on)


@dataclass(frozen=True, slots=True)
class BondLot:
    id: str
    instrument: BondInstrument
    acquired_on: date
    quantity: int
    unit_price: Money | None
    paid_fees: Money | None
    custodian: str | None = None  # Bank/broker holding this lot; not part of the ISIN.

    def __post_init__(self) -> None:
        _identifier(self.id)
        _date(self.acquired_on)
        if self.acquired_on > self.instrument.matures_on:
            raise ValueError("Cannot acquire a bond lot after maturity")
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int):
            raise TypeError("Bond quantity must be an integer")
        if self.quantity <= 0:
            raise ValueError("Bond quantity must be positive")
        if self.custodian is not None and (
            not isinstance(self.custodian, str)
            or not self.custodian.strip()
            or len(self.custodian) > 200
        ):
            raise ValueError("Custodian must be a nonempty institution name (max 200 characters)")
        if self.unit_price is not None:
            _positive(self.unit_price)
            _currency(self.unit_price, self.instrument.face_value.currency)
        if self.paid_fees is not None:
            _nonnegative(self.paid_fees)
            _currency(self.paid_fees, self.instrument.face_value.currency)

    @property
    def cost_basis(self) -> Money | None:
        """Historical purchase cost, unknown if price or fees are missing."""
        if self.unit_price is None or self.paid_fees is None:
            return None
        return self.unit_price * self.quantity + self.paid_fees


@dataclass(frozen=True, slots=True)
class Deposit:
    id: str
    bank: str
    principal: Money
    opened_on: date
    matures_on: date
    annual_rate: Decimal | None = None

    def __post_init__(self) -> None:
        _identifier(self.id)
        if not isinstance(self.bank, str) or not self.bank.strip():
            raise ValueError("Bank name is required")
        _positive(self.principal)
        _date(self.opened_on)
        _date(self.matures_on)
        if self.matures_on <= self.opened_on:
            raise ValueError("Deposit maturity must be later than its opening")
        if self.annual_rate is not None:
            if not isinstance(self.annual_rate, Decimal):
                raise TypeError("Annual rate must be a Decimal fraction")
            if not self.annual_rate.is_finite() or self.annual_rate < 0:
                raise ValueError("Annual rate must be finite and nonnegative")


@dataclass(frozen=True, slots=True)
class BondPaymentTerm:
    id: str
    kind: PaymentKind
    due_on: date
    amount_per_bond: Money

    def __post_init__(self) -> None:
        _identifier(self.id)
        _date(self.due_on)
        _positive(self.amount_per_bond)
        if self.kind not in (PaymentKind.COUPON, PaymentKind.PRINCIPAL):
            raise ValueError("Bond terms can only be coupons or principal")


@dataclass(frozen=True, slots=True)
class DepositPaymentTerm:
    id: str
    kind: PaymentKind
    due_on: date
    amount_for_deposit: Money

    def __post_init__(self) -> None:
        _identifier(self.id)
        _date(self.due_on)
        _positive(self.amount_for_deposit)
        if self.kind not in (PaymentKind.INTEREST, PaymentKind.PRINCIPAL):
            raise ValueError("Deposit terms can only be interest or principal")


@dataclass(frozen=True, slots=True)
class ScheduledPayment:
    id: str
    holding_id: str
    kind: PaymentKind
    due_on: date
    expected: Money

    def __post_init__(self) -> None:
        _identifier(self.id)
        _identifier(self.holding_id)
        _date(self.due_on)
        _positive(self.expected)


@dataclass(frozen=True, slots=True)
class ActualReceipt:
    id: str
    payment_id: str
    received_on: date
    amount: Money

    def __post_init__(self) -> None:
        _identifier(self.id)
        _identifier(self.payment_id)
        _date(self.received_on)
        _positive(self.amount)


@dataclass(frozen=True, slots=True)
class PaymentBalance:
    received: Money
    outstanding: Money
    status: PaymentStatus
    excess: Money  # Confirmed amount above the plan, never silently discarded.


def bond_schedule(lot: BondLot, terms: Iterable[BondPaymentTerm]) -> tuple[ScheduledPayment, ...]:
    """Expand explicitly eligible per-bond terms ONCE for this lot.

    Do not infer coupons from maturity, or derive entitlements from acquisition
    dates alone: the caller supplies the eligible contractual payment terms.
    Partial principal repayments may appear more than once.
    """
    result: list[ScheduledPayment] = []
    seen: set[str] = set()
    principal = Money(Decimal("0"), lot.instrument.face_value.currency)
    for term in terms:
        if term.id in seen:
            raise ValueError("Duplicate bond payment term ID")
        seen.add(term.id)
        if not lot.acquired_on <= term.due_on <= lot.instrument.matures_on:
            raise ValueError("Bond payment outside holding dates")
        _currency(term.amount_per_bond, lot.instrument.face_value.currency)
        if term.kind == PaymentKind.PRINCIPAL:
            principal += term.amount_per_bond
            if principal.amount > lot.instrument.face_value.amount:
                raise ValueError("Scheduled principal exceeds bond face value")
        result.append(
            ScheduledPayment(
                id=f"bond-{lot.id}-{term.id}",
                holding_id=lot.id,
                kind=term.kind,
                due_on=term.due_on,
                expected=term.amount_per_bond * lot.quantity,
            )
        )
    return tuple(result)


def deposit_schedule(
    deposit: Deposit, terms: Iterable[DepositPaymentTerm]
) -> tuple[ScheduledPayment, ...]:
    """Store explicit amounts for the WHOLE deposit, without multiplying again."""
    result: list[ScheduledPayment] = []
    seen: set[str] = set()
    principal = Money(Decimal("0"), deposit.principal.currency)
    for term in terms:
        if term.id in seen:
            raise ValueError("Duplicate deposit payment term ID")
        seen.add(term.id)
        if not deposit.opened_on <= term.due_on <= deposit.matures_on:
            raise ValueError("Deposit payment outside contract dates")
        _currency(term.amount_for_deposit, deposit.principal.currency)
        if term.kind == PaymentKind.PRINCIPAL:
            principal += term.amount_for_deposit
            if principal.amount > deposit.principal.amount:
                raise ValueError("Scheduled principal exceeds deposit principal")
        result.append(
            ScheduledPayment(
                id=f"deposit-{deposit.id}-{term.id}",
                holding_id=deposit.id,
                kind=term.kind,
                due_on=term.due_on,
                expected=term.amount_for_deposit,
            )
        )
    return tuple(result)


def payment_balance(
    payment: ScheduledPayment, receipts: Iterable[ActualReceipt], as_of: date
) -> PaymentBalance:
    """Reconcile receipts against a schedule without posting money to an account.

    Future-dated receipts are excluded from the as-of balance. Duplicate receipt
    IDs and mismatched obligations/currencies are rejected. The actual amount
    may differ from the plan: overpayments are explicitly reported as excess.
    """
    _date(as_of)
    received = Money(Decimal("0"), payment.expected.currency)
    seen: set[str] = set()
    for receipt in receipts:
        if receipt.id in seen:
            raise ValueError("Duplicate actual receipt ID")
        seen.add(receipt.id)
        if receipt.payment_id != payment.id:
            raise ValueError("Actual receipt belongs to another payment")
        _currency(receipt.amount, payment.expected.currency)
        if receipt.received_on <= as_of:
            received += receipt.amount
    delta = payment.expected - received
    zero = Money(Decimal("0"), payment.expected.currency)
    outstanding = delta if delta.amount > 0 else zero
    excess = Money(-delta.amount, payment.expected.currency) if delta.amount < 0 else zero
    if excess.amount > 0:
        status = PaymentStatus.EXCESS
    elif outstanding.amount == 0:
        status = PaymentStatus.PAID
    elif as_of > payment.due_on:
        status = PaymentStatus.OVERDUE
    elif received.amount > 0:
        status = PaymentStatus.PARTIAL
    else:
        status = PaymentStatus.EXPECTED
    return PaymentBalance(received=received, outstanding=outstanding, status=status, excess=excess)
