"""Custody institution per bond lot, automatic maturity principal and overpayments."""

import sqlite3
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

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
    Deposit,
    PaymentKind,
    PaymentStatus,
    ScheduledPayment,
    payment_balance,
)
from fin.domain.money import Money
from fin.persistence import SCHEMA_VERSION, SQLiteCashRepository
from fin.persistence.sqlite_bank import _SCHEMA, _SCHEMA_V2
from fin.web import create_app


def amount(value: str) -> Money:
    return Money(Decimal(value), "UAH")


def lot(lot_id: str, *, custodian: str | None = None) -> BondLot:
    return BondLot(
        lot_id,
        BondInstrument("UA1234567890", amount("1000"), date(2028, 11, 20)),
        date(2026, 11, 20),
        200,
        amount("970.50"),
        amount("0"),
        custodian,
    )


def deposit() -> Deposit:
    return Deposit(
        "d1",
        "mono",
        amount("100000"),
        date(2026, 11, 20),
        date(2027, 11, 20),
        Decimal("0.15"),
    )


def test_two_lots_same_isin_keep_separate_custody_providers(tmp_path) -> None:
    path = tmp_path / "finance.sqlite3"
    with SQLiteCashRepository(path) as db:
        db.add_bond_lot(lot("mono", custodian="mono"))
        db.add_bond_lot(lot("broker", custodian="Freedom24"))
    with SQLiteCashRepository(path) as db:
        assert db.get_bond_lot("mono").custodian == "mono"
        assert db.get_bond_lot("broker").custodian == "Freedom24"
        assert db.get_bond_lot("mono").instrument == db.get_bond_lot("broker").instrument
        db.update_bond_lot(replace(db.get_bond_lot("broker"), custodian="UkrSib"))
        assert db.get_bond_lot("broker").custodian == "UkrSib"
        assert db.get_bond_lot("mono").custodian == "mono"


def test_invalid_custodian_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="Custodian"):
        replace(lot("a"), custodian=" ")
    with pytest.raises(ValueError, match="Custodian"):
        replace(lot("a"), custodian="x" * 201)


def test_version_two_migration_preserves_manual_lot_schedule_and_receipt(tmp_path) -> None:
    path = tmp_path / "existing.sqlite3"
    with sqlite3.connect(path) as connection:
        for command in (*_SCHEMA, *_SCHEMA_V2):
            connection.execute(command)
        connection.execute("PRAGMA user_version=2")
        connection.execute(
            """INSERT INTO bond_instruments (isin, face_amount, currency, matures_on)
               VALUES ('UA1234567890', '1000', 'UAH', '2028-11-20')"""
        )
        connection.execute(
            "INSERT INTO holdings (id, kind, currency) VALUES ('old-lot', 'bond', 'UAH')"
        )
        connection.execute(
            """INSERT INTO bond_lots
               (id, isin, acquired_on, quantity, unit_price, paid_fees)
               VALUES ('old-lot', 'UA1234567890', '2026-11-20', 200, '970.50', '0')"""
        )
        connection.execute(
            """INSERT INTO scheduled_payments
               (id, holding_id, kind, due_on, expected_amount, currency)
               VALUES ('old-coupon', 'old-lot', 'coupon', '2027-05-20', '100', 'UAH')"""
        )
        connection.execute(
            """INSERT INTO actual_receipts
               (id, payment_id, received_on, amount, currency)
               VALUES ('old-receipt', 'old-coupon', '2027-05-21', '80', 'UAH')"""
        )
    with SQLiteCashRepository(path) as db:
        assert db.schema_version() == SCHEMA_VERSION == 3
        assert db.get_bond_lot("old-lot").custodian is None
        assert len(db.list_payments("old-lot")) == 1
        assert db.list_receipts("old-coupon")[0].amount == amount("80")
        assert not db.is_auto_principal("old-coupon")
    with SQLiteCashRepository(path) as db:
        assert db.schema_version() == 3
        assert len(db.list_payments("old-lot")) == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_auto_bond_principal_exact_value_date_and_no_double_entry(tmp_path) -> None:
    with SQLiteCashRepository(tmp_path / "fin.sqlite3") as db:
        bond = lot("new-lot", custodian="mono")
        coupon = ScheduledPayment(
            "coupon1",
            bond.id,
            PaymentKind.COUPON,
            date(2027, 5, 20),
            amount("8000.25"),
        )
        db.add_holding_with_schedule(bond, (coupon,))
        payments = db.list_payments(bond.id)
        assert len(payments) == 2
        principal = next(p for p in payments if p.kind == PaymentKind.PRINCIPAL)
        assert db.is_auto_principal(principal.id)
        assert principal.expected == amount("200000")
        assert principal.due_on == bond.instrument.matures_on
        assert len([p for p in payments if p.kind == PaymentKind.PRINCIPAL]) == 1
        assert db.list_cash_events() == ()
        with pytest.raises(ValueError, match="Auto principal"):
            db.update_payment(replace(principal, expected=amount("199999")))


def test_manual_duplicate_principal_rolls_back_holding(tmp_path) -> None:
    bond = lot("bad")
    manual = ScheduledPayment(
        "manual",
        bond.id,
        PaymentKind.PRINCIPAL,
        bond.instrument.matures_on,
        amount("200000"),
    )
    with SQLiteCashRepository(tmp_path / "fin.sqlite3") as db:
        with pytest.raises(ValueError, match="automatically"):
            db.add_holding_with_schedule(bond, (manual,))
        assert db.get_bond_lot("bad") is None
        assert db.get_payment("manual") is None


def test_auto_deposit_principal_follows_edits_until_actual_receipt(tmp_path) -> None:
    with SQLiteCashRepository(tmp_path / "fin.sqlite3") as db:
        original = deposit()
        db.add_holding_with_schedule(original, ())
        principal = db.list_payments(original.id)[0]
        assert principal.kind == PaymentKind.PRINCIPAL
        assert db.is_auto_principal(principal.id)
        updated = replace(original, principal=amount("120000"), matures_on=date(2028, 11, 20))
        db.update_deposit(updated)
        revised = db.get_payment(principal.id)
        assert revised.expected == amount("120000")
        assert revised.due_on == date(2028, 11, 20)
        db.add_receipt(
            ActualReceipt("received", principal.id, date(2028, 11, 20), amount("110000"))
        )
        with pytest.raises(ValueError, match="receipt was recorded"):
            db.update_deposit(replace(updated, principal=amount("130000")))
        assert db.get_deposit(original.id) == updated


def test_actual_overpayment_is_distinct_from_plan_and_cash_credit(tmp_path) -> None:
    with SQLiteCashRepository(tmp_path / "fin.sqlite3") as db:
        db.add_account(CashAccount("wallet", "Mono", "UAH", AccountKind.BANK))
        db.save_opening_snapshot(
            OpeningSnapshot(date(2026, 11, 20), (OpeningBalance("wallet", amount("1000")),))
        )
        bond = lot("coupon-lot", custodian="mono")
        scheduled = ScheduledPayment(
            "coupon-expected",
            bond.id,
            PaymentKind.COUPON,
            date(2027, 5, 20),
            amount("100"),
        )
        db.add_holding_with_schedule(bond, (scheduled,))
        actual = ActualReceipt("actual", scheduled.id, date(2027, 5, 21), amount("125"))
        db.add_receipt(actual)
        before = payment_balance(scheduled, db.list_receipts(scheduled.id), date(2027, 5, 20))
        assert before.received == amount("0")
        assert db.balances_on(date(2027, 5, 22))["wallet"] == amount("1000")
        after = payment_balance(scheduled, db.list_receipts(scheduled.id), date(2027, 5, 21))
        assert after.status == PaymentStatus.EXCESS
        assert after.received == amount("125")
        assert after.outstanding == amount("0")
        assert after.excess == amount("25")
        db.add_cash_event(
            CashEvent(
                "post-coupon",
                actual.received_on,
                CashEventKind.RECEIPT,
                (CashLeg("wallet", actual.amount),),
                receipt_id=actual.id,
            )
        )
        assert db.balances_on(date(2027, 5, 22))["wallet"] == amount("1125")


def test_web_custodian_and_actual_overpayment_display(tmp_path) -> None:
    path = tmp_path / "fin.sqlite3"
    with SQLiteCashRepository(path) as db:
        db.add_account(CashAccount("a", "mono", "UAH", AccountKind.BANK))
    with TestClient(create_app(path), base_url="http://localhost") as client:
        token = client.app.state.csrf_token
        form = {
            "csrf": token,
            "isin": "UA1234567890",
            "custodian": "mono",
            "currency": "UAH",
            "face_value": "1000",
            "quantity": "200",
            "acquired_on": "2026-11-20",
            "matures_on": "2028-11-20",
            "unit_price": "",
            "paid_fees": "",
        }
        page = client.get("/bank")
        assert '<select name="custodian" required>' in page.text
        assert '<option value="mono"' in page.text
        response = client.post("/bank/bond", data=form, follow_redirects=False)
        assert response.status_code == 303
        page = client.get("/bank")
        assert "mono" in page.text
        assert "200 000" in page.text
        assert "Авто: дата и тело вложения" in page.text
        with SQLiteCashRepository(path) as db:
            bond = db.list_bond_lots()[0]
            principal = db.list_payments(bond.id)[0]
            assert db.is_auto_principal(principal.id)
        cash = client.post(
            "/bank/receipt",
            data={
                "csrf": token,
                "payment_id": principal.id,
                "received_on": "2028-11-20",
                "amount": "200 050",
            },
            follow_redirects=False,
        )
        assert cash.status_code == 303
        page = client.get("/bank?as_of=2028-11-21").text
        assert "Сверх плана" in page
        assert "+50" in page
