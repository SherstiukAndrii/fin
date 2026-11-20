"""Pure cash-ledger invariants: cutoff, transfers, receipts, dates and FX."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from fin.domain.accounts import (
    AccountKind,
    CashAccount,
    CashEvent,
    CashEventKind,
    CashLeg,
    OpeningBalance,
    OpeningSnapshot,
    actual_balances,
)
from fin.domain.money import CurrencyMismatchError, Money


def cash(value: str, currency: str = "UAH") -> Money:
    return Money(Decimal(value), currency)


def accounts() -> tuple[CashAccount, ...]:
    return (
        CashAccount("bank", "Main bank", "UAH", AccountKind.BANK),
        CashAccount("wallet", "Pocket", "UAH", AccountKind.CASH),
    )


def opening() -> OpeningSnapshot:
    return OpeningSnapshot(
        date(2027, 1, 1),
        (OpeningBalance("bank", cash("1000")), OpeningBalance("wallet", cash("100"))),
    )


def transfer() -> CashEvent:
    return CashEvent(
        "move1",
        date(2027, 1, 3),
        CashEventKind.TRANSFER,
        (CashLeg("bank", cash("-200")), CashLeg("wallet", cash("200"))),
    )


def test_closing_snapshot_does_not_replay_same_day_events() -> None:
    paid_before_opening = CashEvent(
        "old", date(2027, 1, 1), CashEventKind.OUTFLOW, (CashLeg("bank", cash("-500")),)
    )
    assert actual_balances(
        accounts(), opening(), [paid_before_opening], as_of=date(2027, 1, 3)
    ) == {"bank": cash("1000"), "wallet": cash("100")}


def test_transfer_changes_allocation_but_not_total() -> None:
    result = actual_balances(accounts(), opening(), [transfer()], as_of=date(2027, 1, 3))
    assert result == {"bank": cash("800"), "wallet": cash("300")}
    assert sum(x.amount for x in result.values()) == Decimal("1100")


def test_inflow_and_outflow_only_after_effective_date() -> None:
    movements = [
        CashEvent(
            "income", date(2027, 1, 5), CashEventKind.INFLOW, (CashLeg("bank", cash("500")),)
        ),
        CashEvent(
            "expense", date(2027, 1, 6), CashEventKind.OUTFLOW, (CashLeg("bank", cash("-50")),)
        ),
    ]
    assert actual_balances(accounts(), opening(), movements, as_of=date(2027, 1, 5))[
        "bank"
    ] == cash("1500")
    assert actual_balances(accounts(), opening(), movements, as_of=date(2027, 1, 6))[
        "bank"
    ] == cash("1450")


def test_actual_receipt_is_applied_once_when_explicitly_posted() -> None:
    posted = CashEvent(
        "entry1",
        date(2027, 1, 6),
        CashEventKind.RECEIPT,
        (CashLeg("bank", cash("30")),),
        receipt_id="receipt1",
    )
    assert actual_balances(accounts(), opening(), [posted], as_of=date(2027, 1, 5))["bank"] == cash(
        "1000"
    )
    assert actual_balances(accounts(), opening(), [posted], as_of=date(2027, 1, 6))["bank"] == cash(
        "1030"
    )
    with pytest.raises(ValueError, match="credited twice"):
        actual_balances(
            accounts(),
            opening(),
            [posted, replace(posted, id="entry2")],
            as_of=date(2027, 1, 6),
        )


def test_foreign_currency_cannot_be_silently_converted() -> None:
    bad = CashEvent(
        "foreign",
        date(2027, 1, 3),
        CashEventKind.INFLOW,
        (CashLeg("bank", cash("10", "USD")),),
    )
    with pytest.raises(CurrencyMismatchError):
        actual_balances(accounts(), opening(), [bad], as_of=date(2027, 1, 4))
    with pytest.raises(CurrencyMismatchError):
        CashEvent(
            "fx",
            date(2027, 1, 3),
            CashEventKind.TRANSFER,
            (CashLeg("bank", cash("-100")), CashLeg("wallet", cash("4", "USD"))),
        )


def test_invalid_transfers_rejected() -> None:
    with pytest.raises(ValueError, match="sum to zero"):
        replace(transfer(), legs=(CashLeg("bank", cash("-50")), CashLeg("wallet", cash("49"))))
    with pytest.raises(ValueError, match="twice"):
        replace(transfer(), legs=(CashLeg("bank", cash("-10")), CashLeg("bank", cash("10"))))


def test_receipt_requires_link_and_correction_requires_explanation() -> None:
    with pytest.raises(ValueError, match="reference"):
        CashEvent(
            "r",
            date(2027, 1, 5),
            CashEventKind.RECEIPT,
            (CashLeg("bank", cash("10")),),
        )
    with pytest.raises(ValueError, match="Corrections"):
        CashEvent(
            "c",
            date(2027, 1, 5),
            CashEventKind.CORRECTION,
            (CashLeg("bank", cash("10")),),
        )


def test_opening_requires_all_accounts_and_currencies() -> None:
    with pytest.raises(ValueError, match="every"):
        actual_balances(
            accounts(),
            OpeningSnapshot(date(2027, 1, 1), (OpeningBalance("bank", cash("0")),)),
            [],
            as_of=date(2027, 1, 2),
        )
    with pytest.raises(CurrencyMismatchError):
        actual_balances(
            accounts(),
            OpeningSnapshot(
                date(2027, 1, 1),
                (OpeningBalance("bank", cash("0", "USD")), OpeningBalance("wallet", cash("0"))),
            ),
            [],
            as_of=date(2027, 1, 2),
        )


def test_zero_opening_valid_but_missing_and_duplicate_not_allowed() -> None:
    assert OpeningBalance("wallet", cash("0")).amount == cash("0")
    with pytest.raises(ValueError, match="once"):
        OpeningSnapshot(
            date(2027, 1, 1),
            (OpeningBalance("bank", cash("0")), OpeningBalance("bank", cash("0"))),
        )
    with pytest.raises(ValueError, match="nonnegative"):
        OpeningBalance("bank", cash("-1"))


def test_duplicate_events_and_unknown_accounts_fail() -> None:
    with pytest.raises(ValueError, match="Duplicate cash event"):
        actual_balances(accounts(), opening(), [transfer(), transfer()], as_of=date(2027, 1, 3))
    bad = CashEvent(
        "bad",
        date(2027, 1, 3),
        CashEventKind.INFLOW,
        (CashLeg("foreign", cash("100")),),
    )
    with pytest.raises(ValueError, match="unknown account"):
        actual_balances(accounts(), opening(), [bad], as_of=date(2027, 1, 3))


def test_history_before_opening_not_invented() -> None:
    with pytest.raises(ValueError, match="before opening"):
        actual_balances(accounts(), opening(), [], as_of=date(2026, 12, 31))
