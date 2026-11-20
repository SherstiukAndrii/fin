"""Read-only, native-currency cash outlook at a selected day.

The outlook never writes ledger events or treats redemption as investment income.
It presents actual posted cash, uncredited receipts and outstanding scheduled
cash flows separately, and combines them only as an explicitly conditional view.
"""

from collections import defaultdict
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from fin.domain.bank import ActualReceipt, PaymentKind, ScheduledPayment, payment_balance
from fin.domain.money import Money


@dataclass(frozen=True, slots=True)
class CashOutlook:
    currency: str
    actual_cash: Money
    uncredited: Money
    expected_income: Money
    expected_principal: Money
    potential_cash: Money


def cash_outlook(
    balances: Iterable[Money],
    payments: Iterable[ScheduledPayment],
    receipts: Iterable[ActualReceipt],
    credited_receipt_ids: Collection[str],
    *,
    opening_on: date,
    as_of: date,
) -> tuple[CashOutlook, ...]:
    """Summarize cash independently for each currency, as of end of day.

    Only post-opening payment obligations contribute to the remaining forecast:
    the opening snapshot already subsumes earlier cash activity. Receipts before
    the opening snapshot are likewise not re-added. A receipt counts in actual
    cash only when separately posted to the ledger (provided in `balances`).
    Other recorded receipts are shown as uncredited, never as actual cash.

    `potential_cash` is a conditional outlook, NOT immediately spendable money:
    it assumes uncredited receipts get posted and all due unpaid obligations
    are eventually received. Purchases, liabilities, FX and reinvestment are
    outside this calculation.
    """
    if type(as_of) is not date or type(opening_on) is not date:
        raise TypeError("Expected calendar dates")
    if as_of < opening_on:
        raise ValueError("Cash outlook cannot precede opening snapshot")

    totals: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: {
            "actual": Decimal("0"),
            "uncredited": Decimal("0"),
            "income": Decimal("0"),
            "principal": Decimal("0"),
        }
    )
    for balance in balances:
        totals[balance.currency]["actual"] += balance.amount

    by_payment: dict[str, list[ActualReceipt]] = defaultdict(list)
    known_receipts: set[str] = set()
    for receipt in receipts:
        if receipt.id in known_receipts:
            raise ValueError("Duplicate actual receipt ID")
        known_receipts.add(receipt.id)
        by_payment[receipt.payment_id].append(receipt)

    credited = set(credited_receipt_ids)
    known_payments: set[str] = set()
    for payment in payments:
        if payment.id in known_payments:
            raise ValueError("Duplicate scheduled payment ID")
        known_payments.add(payment.id)
        related = by_payment.get(payment.id, ())
        # Validate associations and currency even if the obligation predates opening.
        outstanding = payment_balance(payment, related, as_of).outstanding
        if opening_on < payment.due_on <= as_of:
            key = "principal" if payment.kind == PaymentKind.PRINCIPAL else "income"
            totals[payment.expected.currency][key] += outstanding.amount

        for receipt in related:
            if opening_on < receipt.received_on <= as_of and receipt.id not in credited:
                totals[receipt.amount.currency]["uncredited"] += receipt.amount.amount

    if by_payment.keys() - known_payments:
        raise ValueError("Actual receipt references unknown scheduled payment")

    result = []
    for currency, row in sorted(totals.items()):
        def make(amount: Decimal) -> Money:
            return Money(amount, currency)
        potential = row["actual"] + row["uncredited"] + row["income"] + row["principal"]
        result.append(
            CashOutlook(
                currency=currency,
                actual_cash=make(row["actual"]),
                uncredited=make(row["uncredited"]),
                expected_income=make(row["income"]),
                expected_principal=make(row["principal"]),
                potential_cash=make(potential),
            )
        )
    return tuple(result)
