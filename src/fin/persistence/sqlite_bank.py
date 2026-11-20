"""Versioned, local SQLite storage for the existing Bank domain.

Persistence never books cash: schedules and actual receipts remain distinct.
All Decimal values are stored as TEXT, never SQL REAL.
"""

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import TracebackType

from fin.domain.bank import (
    ActualReceipt,
    BondInstrument,
    BondLot,
    Deposit,
    PaymentKind,
    ScheduledPayment,
    payment_balance,
)
from fin.domain.money import CurrencyMismatchError, Money

SCHEMA_VERSION = 1

_SCHEMA = (
    """CREATE TABLE bond_instruments (
        isin TEXT PRIMARY KEY,
        face_amount TEXT NOT NULL,
        currency TEXT NOT NULL,
        matures_on TEXT NOT NULL
    )""",
    """CREATE TABLE holdings (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK (kind IN ('bond', 'deposit')),
        currency TEXT NOT NULL
    )""",
    """CREATE TABLE bond_lots (
        id TEXT PRIMARY KEY REFERENCES holdings(id) ON DELETE RESTRICT,
        isin TEXT NOT NULL REFERENCES bond_instruments(isin) ON DELETE RESTRICT,
        acquired_on TEXT NOT NULL,
        quantity INTEGER NOT NULL CHECK (quantity > 0),
        unit_price TEXT,
        paid_fees TEXT
    )""",
    """CREATE TABLE deposits (
        id TEXT PRIMARY KEY REFERENCES holdings(id) ON DELETE RESTRICT,
        bank TEXT NOT NULL,
        principal TEXT NOT NULL,
        opened_on TEXT NOT NULL,
        matures_on TEXT NOT NULL,
        annual_rate TEXT
    )""",
    """CREATE TABLE scheduled_payments (
        id TEXT PRIMARY KEY,
        holding_id TEXT NOT NULL REFERENCES holdings(id) ON DELETE RESTRICT,
        kind TEXT NOT NULL CHECK (kind IN ('coupon', 'interest', 'principal')),
        due_on TEXT NOT NULL,
        expected_amount TEXT NOT NULL,
        currency TEXT NOT NULL
    )""",
    """CREATE TABLE actual_receipts (
        id TEXT PRIMARY KEY,
        payment_id TEXT NOT NULL REFERENCES scheduled_payments(id) ON DELETE RESTRICT,
        received_on TEXT NOT NULL,
        amount TEXT NOT NULL,
        currency TEXT NOT NULL
    )""",
    "CREATE INDEX payment_holding_idx ON scheduled_payments (holding_id, due_on, id)",
    "CREATE INDEX receipt_payment_idx ON actual_receipts (payment_id, received_on, id)",
)


def _migrate(db: sqlite3.Connection) -> None:
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version == SCHEMA_VERSION:
        return
    if version != 0:
        raise RuntimeError(f"Unsupported Fin database schema version: {version}")
    # One atomic migration. In particular, executescript() must not be used:
    # it implicitly commits an outstanding transaction.
    db.execute("BEGIN IMMEDIATE")
    try:
        for sql in _SCHEMA:
            db.execute(sql)
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        db.commit()
    except BaseException:
        db.rollback()
        raise


def _value(amount: Money | None) -> str | None:
    return None if amount is None else str(amount.amount)


def _money(value: str, currency: str) -> Money:
    return Money(Decimal(value), currency)


class SQLiteBankRepository:
    """Single-connection store; one insert method equals one atomic transaction.

    A file path survives reopening; ':memory:' is supported for tests. Unknown
    database versions are never downgraded or overwritten. Call close() or use
    the context manager to release the connection.
    """

    def __init__(self, path: str | Path) -> None:
        self._db = sqlite3.connect(path, timeout=5)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        try:
            _migrate(self._db)
        except BaseException:
            self._db.close()
            raise

    def __enter__(self) -> "SQLiteBankRepository":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._db.close()

    def schema_version(self) -> int:
        return self._db.execute("PRAGMA user_version").fetchone()[0]

    def add_bond_lot(self, lot: BondLot) -> None:
        instrument = lot.instrument
        with self._db:
            self._db.execute(
                """INSERT INTO bond_instruments (isin, face_amount, currency, matures_on)
                   VALUES (?, ?, ?, ?) ON CONFLICT(isin) DO NOTHING""",
                (
                    instrument.isin,
                    _value(instrument.face_value),
                    instrument.face_value.currency,
                    instrument.matures_on.isoformat(),
                ),
            )
            existing = self._db.execute(
                "SELECT face_amount, currency, matures_on FROM bond_instruments WHERE isin = ?",
                (instrument.isin,),
            ).fetchone()
            if (
                _money(existing["face_amount"], existing["currency"]) != instrument.face_value
                or existing["matures_on"] != instrument.matures_on.isoformat()
            ):
                raise ValueError("ISIN already exists with different contract terms")
            self._db.execute(
                "INSERT INTO holdings (id, kind, currency) VALUES (?, 'bond', ?)",
                (lot.id, instrument.face_value.currency),
            )
            self._db.execute(
                """INSERT INTO bond_lots
                   (id, isin, acquired_on, quantity, unit_price, paid_fees)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    lot.id,
                    instrument.isin,
                    lot.acquired_on.isoformat(),
                    lot.quantity,
                    _value(lot.unit_price),
                    _value(lot.paid_fees),
                ),
            )

    def get_bond_lot(self, lot_id: str) -> BondLot | None:
        row = self._db.execute(
            """SELECT l.id, l.acquired_on, l.quantity, l.unit_price, l.paid_fees,
                      i.isin, i.face_amount, i.currency, i.matures_on
               FROM bond_lots l JOIN bond_instruments i ON i.isin = l.isin
               WHERE l.id = ?""",
            (lot_id,),
        ).fetchone()
        if row is None:
            return None
        instrument = BondInstrument(
            row["isin"],
            _money(row["face_amount"], row["currency"]),
            date.fromisoformat(row["matures_on"]),
        )
        return BondLot(
            row["id"],
            instrument,
            date.fromisoformat(row["acquired_on"]),
            row["quantity"],
            None if row["unit_price"] is None else _money(row["unit_price"], row["currency"]),
            None if row["paid_fees"] is None else _money(row["paid_fees"], row["currency"]),
        )

    def add_deposit(self, deposit: Deposit) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO holdings (id, kind, currency) VALUES (?, 'deposit', ?)",
                (deposit.id, deposit.principal.currency),
            )
            self._db.execute(
                """INSERT INTO deposits
                   (id, bank, principal, opened_on, matures_on, annual_rate)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    deposit.id,
                    deposit.bank,
                    _value(deposit.principal),
                    deposit.opened_on.isoformat(),
                    deposit.matures_on.isoformat(),
                    None if deposit.annual_rate is None else str(deposit.annual_rate),
                ),
            )

    def get_deposit(self, deposit_id: str) -> Deposit | None:
        row = self._db.execute(
            """SELECT d.id, d.bank, d.principal, d.opened_on, d.matures_on, d.annual_rate,
                      h.currency FROM deposits d JOIN holdings h ON h.id = d.id
               WHERE d.id = ?""",
            (deposit_id,),
        ).fetchone()
        if row is None:
            return None
        return Deposit(
            row["id"],
            row["bank"],
            _money(row["principal"], row["currency"]),
            date.fromisoformat(row["opened_on"]),
            date.fromisoformat(row["matures_on"]),
            None if row["annual_rate"] is None else Decimal(row["annual_rate"]),
        )

    def add_payment(self, payment: ScheduledPayment) -> None:
        """Persist an already-expanded lot/deposit amount; never multiply twice."""
        with self._db:
            holding = self._db.execute(
                "SELECT kind, currency FROM holdings WHERE id = ?",
                (payment.holding_id,),
            ).fetchone()
            if holding is None:
                raise ValueError("Unknown holding for scheduled payment")
            if payment.expected.currency != holding["currency"]:
                raise CurrencyMismatchError("Scheduled payment has the wrong currency")
            allowed = (
                (PaymentKind.COUPON, PaymentKind.PRINCIPAL)
                if holding["kind"] == "bond"
                else (PaymentKind.INTEREST, PaymentKind.PRINCIPAL)
            )
            if payment.kind not in allowed:
                raise ValueError("Payment kind does not match holding type")
            if holding["kind"] == "bond":
                lot = self.get_bond_lot(payment.holding_id)
                start, end = lot.acquired_on, lot.instrument.matures_on
                max_principal = lot.instrument.face_value * lot.quantity
            else:
                deposit = self.get_deposit(payment.holding_id)
                start, end = deposit.opened_on, deposit.matures_on
                max_principal = deposit.principal
            if not start <= payment.due_on <= end:
                raise ValueError("Scheduled payment outside holding dates")
            if payment.kind == PaymentKind.PRINCIPAL:
                existing = self._db.execute(
                    """SELECT expected_amount FROM scheduled_payments
                       WHERE holding_id = ? AND kind = 'principal'""",
                    (payment.holding_id,),
                ).fetchall()
                existing_amount = sum(
                    (Decimal(row["expected_amount"]) for row in existing), Decimal("0")
                )
                if existing_amount + payment.expected.amount > max_principal.amount:
                    raise ValueError("Scheduled principal exceeds holding principal")
            self._db.execute(
                """INSERT INTO scheduled_payments
                   (id, holding_id, kind, due_on, expected_amount, currency)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    payment.id,
                    payment.holding_id,
                    payment.kind.value,
                    payment.due_on.isoformat(),
                    _value(payment.expected),
                    payment.expected.currency,
                ),
            )

    def get_payment(self, payment_id: str) -> ScheduledPayment | None:
        row = self._db.execute(
            "SELECT * FROM scheduled_payments WHERE id = ?", (payment_id,)
        ).fetchone()
        if row is None:
            return None
        return ScheduledPayment(
            row["id"],
            row["holding_id"],
            PaymentKind(row["kind"]),
            date.fromisoformat(row["due_on"]),
            _money(row["expected_amount"], row["currency"]),
        )

    def list_payments(self, holding_id: str) -> tuple[ScheduledPayment, ...]:
        ids = self._db.execute(
            """SELECT id FROM scheduled_payments
               WHERE holding_id = ? ORDER BY due_on, id""",
            (holding_id,),
        ).fetchall()
        return tuple(self.get_payment(row["id"]) for row in ids)

    def add_receipt(self, receipt: ActualReceipt) -> None:
        with self._db:
            payment = self.get_payment(receipt.payment_id)
            if payment is None:
                raise ValueError("Unknown scheduled payment for actual receipt")
            # Validate the full set (including future receipts) atomically.
            payment_balance(
                payment,
                (*self.list_receipts(payment.id), receipt),
                date.max,
            )
            self._db.execute(
                """INSERT INTO actual_receipts
                   (id, payment_id, received_on, amount, currency)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    receipt.id,
                    receipt.payment_id,
                    receipt.received_on.isoformat(),
                    _value(receipt.amount),
                    receipt.amount.currency,
                ),
            )

    def list_receipts(self, payment_id: str) -> tuple[ActualReceipt, ...]:
        rows = self._db.execute(
            """SELECT id, payment_id, received_on, amount, currency
               FROM actual_receipts WHERE payment_id = ? ORDER BY received_on, id""",
            (payment_id,),
        ).fetchall()
        return tuple(
            ActualReceipt(
                row["id"],
                row["payment_id"],
                date.fromisoformat(row["received_on"]),
                _money(row["amount"], row["currency"]),
            )
            for row in rows
        )
