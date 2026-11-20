"""Cash SQLite migrations: opening cutoff, transfers and receipt integrity."""

import sqlite3
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
)
from fin.domain.bank import (
    ActualReceipt,
    BondInstrument,
    BondLot,
    BondPaymentTerm,
    PaymentKind,
    bond_schedule,
)
from fin.domain.money import CurrencyMismatchError, Money
from fin.persistence import SCHEMA_VERSION, SQLiteCashRepository
from fin.persistence.sqlite_bank import _SCHEMA


def money(value: str, currency: str = "UAH") -> Money:
    return Money(Decimal(value), currency)


def account(*, account_id: str = "bank", currency: str = "UAH") -> CashAccount:
    return CashAccount(account_id, "Sample bank", currency, AccountKind.BANK)


def opening() -> OpeningSnapshot:
    return OpeningSnapshot(date(2027, 1, 1), (OpeningBalance("bank", money("1000.000000001")),))


def recorded_receipt(store: SQLiteCashRepository) -> ActualReceipt:
    lot = BondLot(
        "bond-lot",
        BondInstrument("UA1234567890", money("1000"), date(2028, 12, 1)),
        date(2027, 1, 1),
        2,
        money("980"),
        money("0.00"),
    )
    store.add_bond_lot(lot)
    terms = [
        BondPaymentTerm("m3", PaymentKind.COUPON, date(2027, 3, 1), money("40")),
        BondPaymentTerm("m6", PaymentKind.COUPON, date(2027, 6, 1), money("40")),
    ]
    for payment in bond_schedule(lot, terms):
        store.add_payment(payment)
    receipt = ActualReceipt("r1", "bond-bond-lot-m3", date(2027, 3, 3), money("50"))
    store.add_receipt(receipt)
    return receipt


def test_migration_of_v1_keeps_existing_bank_records(tmp_path) -> None:
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        for sql in _SCHEMA:
            db.execute(sql)
        db.execute("PRAGMA user_version = 1")
        db.execute(
            """INSERT INTO bond_instruments (isin, face_amount, currency, matures_on)
               VALUES ('UA1234567890', '1000', 'UAH', '2028-12-01')"""
        )
        db.execute(
            """INSERT INTO holdings (id, kind, currency)
               VALUES ('lot1', 'bond', 'UAH')"""
        )
        db.execute(
            """INSERT INTO bond_lots (id, isin, acquired_on, quantity, unit_price, paid_fees)
               VALUES ('lot1', 'UA1234567890', '2027-01-01', 2, '970.125', '0.00')"""
        )
    with SQLiteCashRepository(path) as store:
        assert store.schema_version() == SCHEMA_VERSION == 4
        assert store.get_bond_lot("lot1").cost_basis == money("1940.250")
        store.add_account(account())
        store.save_opening_snapshot(opening())
    with SQLiteCashRepository(path) as store:
        assert store.get_bond_lot("lot1").quantity == 2
        assert store.get_opening_snapshot() == opening()


def test_empty_database_starts_at_v4_and_reopens_idempotently(tmp_path) -> None:
    path = tmp_path / "fin.db"
    with SQLiteCashRepository(path) as store:
        assert store.schema_version() == 4
    with SQLiteCashRepository(path) as store:
        assert store.schema_version() == 4
        assert store.list_accounts() == ()
        assert store.list_cash_events() == ()


def test_snapshot_and_transaction_roundtrip_without_float(tmp_path) -> None:
    path = tmp_path / "fin.db"
    event = CashEvent(
        "income",
        date(2027, 1, 2),
        CashEventKind.INFLOW,
        (CashLeg("bank", money("0.000000000000000000123")),),
    )
    with SQLiteCashRepository(path) as store:
        store.add_account(account())
        store.save_opening_snapshot(opening())
        store.add_cash_event(event)
    with SQLiteCashRepository(path) as store:
        assert store.get_account("bank") == account()
        assert store.list_cash_events() == (event,)
        assert store.balances_on(date(2027, 1, 2))["bank"] == money("1000.000000001000000000123")
        assert store.balances_on(date(2027, 1, 1))["bank"] == money("1000.000000001")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT typeof(amount) FROM cash_legs").fetchone() == ("text",)


def test_no_cash_credit_from_scheduled_or_unposted_actual_receipts(tmp_path) -> None:
    with SQLiteCashRepository(tmp_path / "fin.db") as store:
        store.add_account(account())
        store.save_opening_snapshot(opening())
        receipt = recorded_receipt(store)
        assert store.balances_on(date(2027, 3, 20))["bank"] == money("1000.000000001")
        posted = CashEvent(
            "post-r1",
            receipt.received_on,
            CashEventKind.RECEIPT,
            (CashLeg("bank", receipt.amount),),
            receipt_id=receipt.id,
        )
        store.add_cash_event(posted)
        assert store.balances_on(date(2027, 3, 2))["bank"] == money("1000.000000001")
        assert store.balances_on(date(2027, 3, 3))["bank"] == money("1050.000000001")


def test_receipt_cannot_be_posted_twice_even_under_different_event_id(tmp_path) -> None:
    with SQLiteCashRepository(tmp_path / "fin.db") as store:
        store.add_account(account())
        store.save_opening_snapshot(opening())
        receipt = recorded_receipt(store)
        posted = CashEvent(
            "first",
            receipt.received_on,
            CashEventKind.RECEIPT,
            (CashLeg("bank", receipt.amount),),
            receipt_id=receipt.id,
        )
        store.add_cash_event(posted)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            store.add_cash_event(replace(posted, id="second"))
        assert len(store.list_cash_events()) == 1


def test_cash_receipt_must_have_matching_amount_date_and_currency(tmp_path) -> None:
    with SQLiteCashRepository(tmp_path / "fin.db") as store:
        store.add_account(account())
        store.save_opening_snapshot(opening())
        receipt = recorded_receipt(store)
        event = CashEvent(
            "post",
            receipt.received_on,
            CashEventKind.RECEIPT,
            (CashLeg("bank", money("50")),),
            receipt_id=receipt.id,
        )
        with pytest.raises(ValueError, match="date"):
            store.add_cash_event(replace(event, effective_on=date(2027, 3, 4)))
        with pytest.raises(ValueError, match="amount"):
            store.add_cash_event(replace(event, legs=(CashLeg("bank", money("49")),)))
        with pytest.raises(ValueError, match="existing"):
            store.add_cash_event(replace(event, receipt_id="missing"))
        with pytest.raises(CurrencyMismatchError):
            store.add_cash_event(replace(event, legs=(CashLeg("bank", money("50", "USD")),)))
        assert store.list_cash_events() == ()


def test_transfers_have_no_external_income_and_survive_reopen(tmp_path) -> None:
    path = tmp_path / "fin.db"
    wallet = CashAccount("wallet", "Cash", "UAH", AccountKind.CASH)
    snap = OpeningSnapshot(
        date(2027, 1, 1),
        (OpeningBalance("bank", money("1000")), OpeningBalance("wallet", money("100"))),
    )
    event = CashEvent(
        "tx",
        date(2027, 1, 3),
        CashEventKind.TRANSFER,
        (CashLeg("bank", money("-300")), CashLeg("wallet", money("300"))),
    )
    with SQLiteCashRepository(path) as store:
        store.add_account(account())
        store.add_account(wallet)
        store.save_opening_snapshot(snap)
        store.add_cash_event(event)
    with SQLiteCashRepository(path) as store:
        assert store.balances_on(date(2027, 1, 2)) == {
            "bank": money("1000"),
            "wallet": money("100"),
        }
        assert store.balances_on(date(2027, 1, 3)) == {"bank": money("700"), "wallet": money("400")}
        assert store.list_cash_events() == (event,)


def test_opening_snapshot_not_replayed_and_cannot_be_replaced(tmp_path) -> None:
    with SQLiteCashRepository(tmp_path / "fin.db") as store:
        store.add_account(account())
        store.save_opening_snapshot(opening())
        with pytest.raises(sqlite3.IntegrityError):
            store.save_opening_snapshot(opening())
        with pytest.raises(ValueError, match="after"):
            store.add_cash_event(
                CashEvent(
                    "old",
                    date(2027, 1, 1),
                    CashEventKind.OUTFLOW,
                    (CashLeg("bank", money("-20")),),
                )
            )
        assert store.balances_on(date(2027, 1, 1))["bank"] == money("1000.000000001")


def test_snapshot_missing_account_or_wrong_currency_is_rejected_atomically(tmp_path) -> None:
    with SQLiteCashRepository(tmp_path / "fin.db") as store:
        store.add_account(account())
        with pytest.raises(ValueError, match="unknown account"):
            store.save_opening_snapshot(
                OpeningSnapshot(date(2027, 1, 1), (OpeningBalance("other", money("5")),))
            )
        with pytest.raises(CurrencyMismatchError):
            store.save_opening_snapshot(
                OpeningSnapshot(
                    date(2027, 1, 1),
                    (OpeningBalance("bank", money("5", "USD")),),
                )
            )
        assert store.get_opening_snapshot() is None
        store.save_opening_snapshot(opening())
        with pytest.raises(ValueError, match="New accounts"):
            store.add_account(CashAccount("later", "Later", "UAH", AccountKind.CASH))
        assert store.list_accounts() == (account(),)


def test_missing_snapshot_and_unknown_account_block_posting(tmp_path) -> None:
    with SQLiteCashRepository(tmp_path / "fin.db") as store:
        store.add_account(account())
        event = CashEvent(
            "e",
            date(2027, 1, 4),
            CashEventKind.INFLOW,
            (CashLeg("bank", money("10")),),
        )
        with pytest.raises(ValueError, match="opening"):
            store.add_cash_event(event)
        store.save_opening_snapshot(opening())
        with pytest.raises(ValueError, match="unknown account"):
            store.add_cash_event(replace(event, legs=(CashLeg("missing", money("10")),)))
        assert store.list_cash_events() == ()


def test_correction_requires_explanation_and_remains_auditable(tmp_path) -> None:
    correction = CashEvent(
        "corr1",
        date(2027, 1, 31),
        CashEventKind.CORRECTION,
        (CashLeg("bank", money("-5.20")),),
        note="Count adjustment",
    )
    with SQLiteCashRepository(tmp_path / "fin.db") as store:
        store.add_account(account())
        store.save_opening_snapshot(opening())
        store.add_cash_event(correction)
        assert store.list_cash_events() == (correction,)
        assert store.balances_on(date(2027, 1, 31))["bank"] == money("994.800000001")
