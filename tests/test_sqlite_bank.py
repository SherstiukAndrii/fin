"""SQLite Bank v1: migrations, exact values, FK and atomic payment records."""

import sqlite3
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
    ScheduledPayment,
    bond_schedule,
    deposit_schedule,
    payment_balance,
)
from fin.domain.money import CurrencyMismatchError, Money
from fin.persistence import SCHEMA_VERSION, SQLiteBankRepository


def amount(value: str, currency: str = "UAH") -> Money:
    return Money(Decimal(value), currency)


def lot(
    *,
    lot_id: str = "lot-1",
    isin: str = "UA1234567890",
    face: str = "1000",
) -> BondLot:
    return BondLot(
        id=lot_id,
        instrument=BondInstrument(isin, amount(face), date(2028, 12, 1)),
        acquired_on=date(2027, 1, 15),
        quantity=2,
        unit_price=amount("965.123456789012345678901234567"),
        paid_fees=amount("0.00"),
    )


def deposit(*, deposit_id: str = "deposit-1") -> Deposit:
    return Deposit(
        deposit_id,
        "Sample bank",
        amount("5000.000000001"),
        date(2027, 1, 1),
        date(2028, 12, 31),
        Decimal("0.1234567890123456789012345678"),
    )


def payments(bond: BondLot) -> tuple[ScheduledPayment, ...]:
    terms = [
        BondPaymentTerm(f"coupon-{month}", PaymentKind.COUPON, date(2028, month, 1), amount("40"))
        for month in (3, 6, 9)
    ]
    terms.append(
        BondPaymentTerm("redemption", PaymentKind.PRINCIPAL, date(2028, 12, 1), amount("1000"))
    )
    return bond_schedule(bond, terms)


def test_migration_idempotent_and_survives_reopen(tmp_path) -> None:
    path = tmp_path / "bank.sqlite3"
    with SQLiteBankRepository(path) as store:
        assert store.schema_version() == SCHEMA_VERSION == 6
        store.add_bond_lot(lot())
        store.add_deposit(deposit())
    with SQLiteBankRepository(path) as store:
        assert store.schema_version() == 6
        assert store.get_bond_lot("lot-1") == lot()
        assert store.get_deposit("deposit-1") == deposit()
        assert store.get_bond_lot("missing") is None
        assert store.get_deposit("missing") is None


def test_decimal_is_text_and_preserves_scale_and_unknowns(tmp_path) -> None:
    path = tmp_path / "fin.db"
    unknown = replace(lot(), unit_price=None, paid_fees=None)
    with SQLiteBankRepository(path) as store:
        store.add_bond_lot(unknown)
        store.add_deposit(deposit())
        assert store.get_bond_lot("lot-1") == unknown
        assert store.get_bond_lot("lot-1").cost_basis is None
        assert store.get_deposit("deposit-1") == deposit()
    with sqlite3.connect(path) as db:
        raw = db.execute("SELECT unit_price, paid_fees FROM bond_lots").fetchone()
        assert raw == (None, None)
        assert db.execute("SELECT typeof(principal) FROM deposits").fetchone()[0] == "text"


def test_zero_fee_remains_distinct_from_unknown(tmp_path) -> None:
    with SQLiteBankRepository(tmp_path / "fin.db") as store:
        store.add_bond_lot(lot())
        returned = store.get_bond_lot("lot-1")
        assert returned.paid_fees.amount.as_tuple() == Decimal("0.00").as_tuple()
        assert returned.unit_price.amount.as_tuple() == lot().unit_price.amount.as_tuple()


def test_multiple_lots_share_identical_instrument(tmp_path) -> None:
    with SQLiteBankRepository(tmp_path / "fin.db") as store:
        store.add_bond_lot(lot())
        store.add_bond_lot(lot(lot_id="lot-2"))
        assert store.get_bond_lot("lot-2").instrument == lot().instrument


def test_conflicting_isin_rolls_back_new_lot(tmp_path) -> None:
    with SQLiteBankRepository(tmp_path / "fin.db") as store:
        store.add_bond_lot(lot())
        with pytest.raises(ValueError, match="different contract"):
            store.add_bond_lot(lot(lot_id="lot-2", face="999"))
        assert store.get_bond_lot("lot-2") is None
        assert store.get_bond_lot("lot-1") == lot()


def test_shared_holding_identifiers_rejected_and_rolled_back(tmp_path) -> None:
    with SQLiteBankRepository(tmp_path / "fin.db") as store:
        store.add_bond_lot(lot(lot_id="same"))
        with pytest.raises(sqlite3.IntegrityError):
            store.add_deposit(deposit(deposit_id="same"))
        assert store.get_deposit("same") is None
        assert store.get_bond_lot("same") is not None


def test_three_coupons_and_principal_stay_distinct_after_reopening(tmp_path) -> None:
    path = tmp_path / "fin.db"
    with SQLiteBankRepository(path) as store:
        store.add_bond_lot(lot())
        for payment in payments(lot()):
            store.add_payment(payment)
    with SQLiteBankRepository(path) as store:
        result = store.list_payments("lot-1")
        assert result == payments(lot())
        assert [x.expected.amount for x in result] == [
            Decimal("80"),
            Decimal("80"),
            Decimal("80"),
            Decimal("2000"),
        ]
        assert store.get_payment("absent") is None


def test_deposit_schedule_and_nullable_rate(tmp_path) -> None:
    dep = replace(deposit(), annual_rate=None)
    terms = [
        DepositPaymentTerm("interest", PaymentKind.INTEREST, date(2027, 6, 1), amount("50")),
        DepositPaymentTerm("principal", PaymentKind.PRINCIPAL, date(2028, 12, 31), dep.principal),
    ]
    with SQLiteBankRepository(tmp_path / "fin.db") as store:
        store.add_deposit(dep)
        for payment in deposit_schedule(dep, terms):
            store.add_payment(payment)
        assert store.get_deposit(dep.id) == dep
        assert store.list_payments(dep.id) == deposit_schedule(dep, terms)


def test_partial_receipts_reconcile_after_reopen(tmp_path) -> None:
    path = tmp_path / "fin.db"
    payment = payments(lot())[0]
    receipt1 = ActualReceipt("r1", payment.id, date(2028, 3, 3), amount("50"))
    receipt2 = ActualReceipt("r2", payment.id, date(2028, 3, 10), amount("30"))
    with SQLiteBankRepository(path) as store:
        store.add_bond_lot(lot())
        store.add_payment(payment)
        store.add_receipt(receipt1)
        store.add_receipt(receipt2)
    with SQLiteBankRepository(path) as store:
        saved = store.get_payment(payment.id)
        receipts = store.list_receipts(payment.id)
        assert receipts == (receipt1, receipt2)
        partial = payment_balance(saved, receipts, date(2028, 3, 5))
        assert partial.received == amount("50")
        assert partial.outstanding == amount("30")
        assert partial.status == PaymentStatus.OVERDUE
        assert payment_balance(saved, receipts, date(2028, 3, 10)).status == PaymentStatus.PAID


def test_duplicate_receipt_cannot_credit_twice(tmp_path) -> None:
    with SQLiteBankRepository(tmp_path / "fin.db") as store:
        store.add_bond_lot(lot())
        payment = payments(lot())[0]
        store.add_payment(payment)
        receipt = ActualReceipt("r1", payment.id, date(2028, 3, 3), amount("10"))
        store.add_receipt(receipt)
        with pytest.raises(ValueError, match="Duplicate"):
            store.add_receipt(receipt)
        assert store.list_receipts(payment.id) == (receipt,)


def test_actual_receipts_may_exceed_plan_and_remain_auditable(tmp_path) -> None:
    with SQLiteBankRepository(tmp_path / "fin.db") as store:
        store.add_bond_lot(lot())
        payment = payments(lot())[0]
        store.add_payment(payment)
        store.add_receipt(ActualReceipt("r1", payment.id, date(2028, 3, 1), amount("50")))
        store.add_receipt(ActualReceipt("r2", payment.id, date(2028, 3, 2), amount("31")))
        balance = payment_balance(payment, store.list_receipts(payment.id), date(2028, 3, 2))
        assert balance.received == amount("81")
        assert balance.outstanding == amount("0")
        assert balance.excess == amount("1")
        assert balance.status == PaymentStatus.EXCESS
        assert len(store.list_receipts(payment.id)) == 2


def test_orphan_payment_and_receipt_are_rejected(tmp_path) -> None:
    with SQLiteBankRepository(tmp_path / "fin.db") as store:
        payment = payments(lot())[0]
        with pytest.raises(ValueError, match="Unknown holding"):
            store.add_payment(payment)
        with pytest.raises(ValueError, match="Unknown scheduled"):
            store.add_receipt(ActualReceipt("r1", payment.id, date(2028, 3, 1), amount("20")))
        assert store.list_payments("lot-1") == ()


def test_payment_currency_kind_and_dates_checked(tmp_path) -> None:
    with SQLiteBankRepository(tmp_path / "fin.db") as store:
        store.add_bond_lot(lot())
        valid = payments(lot())[0]
        with pytest.raises(CurrencyMismatchError):
            store.add_payment(replace(valid, expected=amount("80", "USD")))
        with pytest.raises(ValueError, match="kind"):
            store.add_payment(replace(valid, kind=PaymentKind.INTEREST))
        with pytest.raises(ValueError, match="outside"):
            store.add_payment(replace(valid, due_on=date(2026, 12, 1)))
        assert store.list_payments("lot-1") == ()


def test_total_scheduled_principal_is_bounded(tmp_path) -> None:
    with SQLiteBankRepository(tmp_path / "fin.db") as store:
        store.add_bond_lot(lot())
        first = ScheduledPayment(
            "p1", "lot-1", PaymentKind.PRINCIPAL, date(2028, 6, 1), amount("1200")
        )
        store.add_payment(first)
        with pytest.raises(ValueError, match="exceeds"):
            store.add_payment(
                ScheduledPayment(
                    "p2", "lot-1", PaymentKind.PRINCIPAL, date(2028, 12, 1), amount("900")
                )
            )
        assert store.list_payments("lot-1") == (first,)


def test_unknown_future_schema_version_is_not_downgraded(tmp_path) -> None:
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="Unsupported"):
        SQLiteBankRepository(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 99


def test_foreign_keys_are_enabled_and_missing_payment_rejected(tmp_path) -> None:
    path = tmp_path / "fin.db"
    with SQLiteBankRepository(path) as store:
        assert store._db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            with store._db:
                store._db.execute(
                    """INSERT INTO actual_receipts
                       (id, payment_id, received_on, amount, currency)
                       VALUES ('orphan', 'missing', '2028-03-01', '1', 'UAH')"""
                )
