"""Actual cash accounts and ledger, independent of forecast and investment valuation.

Opening snapshot is the closing state for one selected day. Changes dated on
or before that day must not be applied to it a second time.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from urllib.parse import urlsplit

from fin.domain.money import CurrencyMismatchError, Money


def _id(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise ValueError("ID must contain letters, digits, underscores or hyphens")


def _day(value: date) -> None:
    if type(value) is not date:
        raise TypeError("Expected a calendar date")


class AccountKind(StrEnum):
    CASH = "cash"
    BANK = "bank"


class CashEventKind(StrEnum):
    INFLOW = "inflow"
    OUTFLOW = "outflow"
    TRANSFER = "transfer"
    CORRECTION = "correction"
    RECEIPT = "receipt"


def _web_link(value: str | None) -> None:
    """Never render a user-managed javascript/file/data URL as a bank link."""
    if value is None:
        return
    if not isinstance(value, str) or len(value) > 1024:
        raise ValueError("Bank link must be a URL up to 1024 characters")
    url = urlsplit(value)
    if (
        value != value.strip()
        or url.scheme != "https"
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.fragment
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError("Bank links must be clean HTTPS addresses")


@dataclass(frozen=True, slots=True)
class Bank:
    id: str
    name: str
    website_url: str | None = None
    cabinet_url: str | None = None

    def __post_init__(self) -> None:
        _id(self.id)
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Bank name must not be blank")
        _web_link(self.website_url)
        _web_link(self.cabinet_url)


@dataclass(frozen=True, slots=True)
class SecuritiesAccount:
    """Custody record inside a bank; never counted as a cash balance."""
    id: str
    bank_id: str
    name: str

    def __post_init__(self) -> None:
        _id(self.id)
        _id(self.bank_id)
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Securities account name must not be blank")


@dataclass(frozen=True, slots=True)
class CashAccount:
    id: str
    name: str
    currency: str
    kind: AccountKind
    bank_id: str | None = None
    opened_on: date | None = None

    def __post_init__(self) -> None:
        _id(self.id)
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Account name must not be blank")
        Money(Decimal("0"), self.currency)  # Reuse the shared currency validation.
        if not isinstance(self.kind, AccountKind):
            raise ValueError("Unsupported account kind")
        if self.bank_id is not None:
            _id(self.bank_id)
        if self.kind == AccountKind.CASH and self.bank_id is not None:
            raise ValueError("Physical cash cannot belong to a bank")
        if self.opened_on is not None:
            _day(self.opened_on)


@dataclass(frozen=True, slots=True)
class OpeningBalance:
    account_id: str
    amount: Money

    def __post_init__(self) -> None:
        _id(self.account_id)
        if not isinstance(self.amount, Money):
            raise TypeError("Opening balance must be Money")
        if self.amount.amount < 0:
            raise ValueError("Opening cash balances must be nonnegative; debts are separate")


@dataclass(frozen=True, slots=True)
class OpeningSnapshot:
    as_of: date
    balances: tuple[OpeningBalance, ...]

    def __post_init__(self) -> None:
        _day(self.as_of)
        if not isinstance(self.balances, tuple) or not self.balances:
            raise ValueError("Opening snapshot requires at least one account balance")
        ids = [balance.account_id for balance in self.balances]
        if len(ids) != len(set(ids)):
            raise ValueError("An account may occur only once in an opening snapshot")


@dataclass(frozen=True, slots=True)
class CashLeg:
    account_id: str
    change: Money

    def __post_init__(self) -> None:
        _id(self.account_id)
        if not isinstance(self.change, Money):
            raise TypeError("Cash movement must be Money")
        if self.change.amount == 0:
            raise ValueError("Cash movement cannot be zero")


@dataclass(frozen=True, slots=True)
class CashEvent:
    id: str
    effective_on: date
    kind: CashEventKind
    legs: tuple[CashLeg, ...]
    receipt_id: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        _id(self.id)
        _day(self.effective_on)
        if not isinstance(self.kind, CashEventKind):
            raise ValueError("Unsupported cash event type")
        if not isinstance(self.legs, tuple):
            raise TypeError("Cash event legs must be a tuple")
        if len(self.legs) != (2 if self.kind == CashEventKind.TRANSFER else 1):
            raise ValueError("Transfers need two legs; other events need one")
        if len({leg.account_id for leg in self.legs}) != len(self.legs):
            raise ValueError("An account cannot appear twice in a cash event")
        if self.kind == CashEventKind.TRANSFER:
            first, second = self.legs
            if first.change.currency != second.change.currency:
                raise CurrencyMismatchError("Cross-currency transfer needs explicit FX")
            if first.change.amount + second.change.amount != 0:
                raise ValueError("Transfer legs must sum to zero")
        elif self.kind in (CashEventKind.INFLOW, CashEventKind.RECEIPT):
            if self.legs[0].change.amount <= 0:
                raise ValueError("Incoming cash must be positive")
        elif self.kind == CashEventKind.OUTFLOW and self.legs[0].change.amount >= 0:
            raise ValueError("Outgoing cash must be negative")

        if self.kind == CashEventKind.RECEIPT:
            if self.receipt_id is None:
                raise ValueError("A payment receipt event must reference an actual receipt")
            _id(self.receipt_id)
        elif self.receipt_id is not None:
            raise ValueError("Only receipt events may reference a payment receipt")

        if self.kind == CashEventKind.CORRECTION:
            if not isinstance(self.note, str) or not self.note.strip():
                raise ValueError("Corrections require an explicit explanation")
        elif self.note is not None and not isinstance(self.note, str):
            raise TypeError("Cash event note must be text")


def actual_balances(
    accounts: Iterable[CashAccount],
    opening: OpeningSnapshot,
    events: Iterable[CashEvent],
    *,
    as_of: date,
) -> dict[str, Money]:
    """Compute end-of-day actual cash: no scheduled cash and no implicit FX.

    Only movements strictly after the closing opening snapshot date are replayed.
    Events on/after later dates affect cash once, regardless of input order.
    """
    _day(as_of)
    if as_of < opening.as_of:
        raise ValueError("Cannot calculate cash before opening snapshot")
    account_map: dict[str, CashAccount] = {}
    for account in accounts:
        if account.id in account_map:
            raise ValueError("Duplicate account ID")
        account_map[account.id] = account
    initial: dict[str, Money] = {}
    for row in opening.balances:
        if row.account_id not in account_map:
            raise ValueError("Opening snapshot references an unknown account")
        if row.amount.currency != account_map[row.account_id].currency:
            raise CurrencyMismatchError("Opening balance currency does not match account")
        initial[row.account_id] = row.amount
    later = {
        account.id
        for account in account_map.values()
        if account.opened_on is not None and account.opened_on > opening.as_of
    }
    if set(initial) != set(account_map) - later:
        raise ValueError("Opening snapshot must contain every account active at opening")
    result = dict(initial)
    for account_id in later:
        result[account_id] = Money(Decimal("0"), account_map[account_id].currency)
    seen_events: set[str] = set()
    seen_receipts: set[str] = set()
    for event in events:
        if event.id in seen_events:
            raise ValueError("Duplicate cash event ID")
        seen_events.add(event.id)
        if event.receipt_id is not None:
            if event.receipt_id in seen_receipts:
                raise ValueError("An actual receipt cannot be credited twice")
            seen_receipts.add(event.receipt_id)
        for leg in event.legs:
            account = account_map.get(leg.account_id)
            if account is None:
                raise ValueError("Cash event references an unknown account")
            if leg.change.currency != account.currency:
                raise CurrencyMismatchError("Cash event currency does not match account")
            if account.opened_on is not None and event.effective_on < account.opened_on:
                raise ValueError("Cash event predates account activation")
        if opening.as_of < event.effective_on <= as_of:
            for leg in event.legs:
                result[leg.account_id] += leg.change
    return {
        account_id: money
        for account_id, money in result.items()
        if account_map[account_id].opened_on is None
        or account_map[account_id].opened_on <= as_of
    }
