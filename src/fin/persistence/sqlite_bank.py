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

SCHEMA_VERSION = 2

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

_SCHEMA_V2 = (
    """CREATE TABLE cash_accounts (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        currency TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('cash', 'bank'))
    )""",
    """CREATE TABLE opening_snapshot (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        as_of TEXT NOT NULL
    )""",
    """CREATE TABLE opening_balances (
        account_id TEXT PRIMARY KEY REFERENCES cash_accounts(id) ON DELETE RESTRICT,
        amount TEXT NOT NULL,
        currency TEXT NOT NULL
    )""",
    """CREATE TABLE cash_events (
        id TEXT PRIMARY KEY,
        effective_on TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (
            kind IN ('inflow', 'outflow', 'transfer', 'correction', 'receipt')
        ),
        receipt_id TEXT UNIQUE REFERENCES actual_receipts(id) ON DELETE RESTRICT,
        note TEXT
    )""",
    """CREATE TABLE cash_legs (
        event_id TEXT NOT NULL REFERENCES cash_events(id) ON DELETE RESTRICT,
        account_id TEXT NOT NULL REFERENCES cash_accounts(id) ON DELETE RESTRICT,
        amount TEXT NOT NULL,
        currency TEXT NOT NULL,
        PRIMARY KEY (event_id, account_id)
    )""",
    "CREATE INDEX cash_events_dates ON cash_events (effective_on, id)",
)


def _migrate(db: sqlite3.Connection) -> None:
    """Apply 0 → 1 → 2 or 1 → 2 atomically, preserving all Bank data."""
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version == SCHEMA_VERSION:
        return
    if version not in (0, 1):
        raise RuntimeError(f"Unsupported Fin database schema version: {version}")
    # Do not use executescript: it commits pending transactions implicitly.
    db.execute("BEGIN IMMEDIATE")
    try:
        if version == 0:
            for sql in _SCHEMA:
                db.execute(sql)
        for sql in _SCHEMA_V2:
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

    def update_bond_lot(self, lot: BondLot) -> None:
        """Edit purchase details without silently rewriting contractual payouts."""
        with self._db:
            old = self.get_bond_lot(lot.id)
            if old is None:
                raise ValueError("Lot not found")
            if lot.instrument != old.instrument:
                raise ValueError("ISIN, nominal and maturity cannot change on an existing lot")
            payments = self.list_payments(lot.id)
            if payments and lot.quantity != old.quantity:
                raise ValueError("Quantity cannot change after scheduling payments")
            if any(payment.due_on < lot.acquired_on for payment in payments):
                raise ValueError("Purchase date must precede all scheduled payments")
            self._db.execute(
                """UPDATE bond_lots SET acquired_on=?, quantity=?, unit_price=?, paid_fees=?
                   WHERE id=?""",
                (
                    lot.acquired_on.isoformat(),
                    lot.quantity,
                    _value(lot.unit_price),
                    _value(lot.paid_fees),
                    lot.id,
                ),
            )

    def update_deposit(self, deposit: Deposit) -> None:
        with self._db:
            old = self.get_deposit(deposit.id)
            if old is None:
                raise ValueError("Deposit not found")
            if deposit.principal.currency != old.principal.currency:
                raise ValueError("Cannot change currency of an existing deposit")
            payments = self.list_payments(deposit.id)
            if any(not deposit.opened_on <= p.due_on <= deposit.matures_on for p in payments):
                raise ValueError("Payment dates conflict with changed deposit dates")
            principal = sum(
                (p.expected.amount for p in payments if p.kind == PaymentKind.PRINCIPAL),
                Decimal("0"),
            )
            if principal > deposit.principal.amount:
                raise ValueError("Scheduled principal exceeds edited deposit principal")
            self._db.execute(
                """UPDATE deposits SET bank=?, principal=?, opened_on=?, matures_on=?,
                   annual_rate=? WHERE id=?""",
                (
                    deposit.bank,
                    str(deposit.principal.amount),
                    deposit.opened_on.isoformat(),
                    deposit.matures_on.isoformat(),
                    None if deposit.annual_rate is None else str(deposit.annual_rate),
                    deposit.id,
                ),
            )

    def update_payment(self, payment: ScheduledPayment) -> None:
        with self._db:
            old = self.get_payment(payment.id)
            if old is None:
                raise ValueError("Payment not found")
            if payment.holding_id != old.holding_id:
                raise ValueError("Cannot move a payment between holdings")
            if payment.expected.currency != old.expected.currency:
                raise CurrencyMismatchError("Cannot change payment currency")
            bond = self.get_bond_lot(payment.holding_id)
            deposit = self.get_deposit(payment.holding_id)
            if bond:
                if payment.kind not in (PaymentKind.COUPON, PaymentKind.PRINCIPAL):
                    raise ValueError("Bond requires a coupon or principal payment")
                start, end = bond.acquired_on, bond.instrument.matures_on
                max_principal = bond.instrument.face_value.amount * bond.quantity
            elif deposit:
                if payment.kind not in (PaymentKind.INTEREST, PaymentKind.PRINCIPAL):
                    raise ValueError("Deposit requires interest or principal")
                start, end = deposit.opened_on, deposit.matures_on
                max_principal = deposit.principal.amount
            else:
                raise ValueError("Holding not found")
            if not start <= payment.due_on <= end:
                raise ValueError("Payment outside holding contract dates")
            siblings = [p for p in self.list_payments(payment.holding_id) if p.id != payment.id]
            if payment.kind == PaymentKind.PRINCIPAL:
                total = payment.expected.amount + sum(
                    (p.expected.amount for p in siblings if p.kind == PaymentKind.PRINCIPAL),
                    Decimal("0"),
                )
                if total > max_principal:
                    raise ValueError("Scheduled principal exceeds holding principal")
            payment_balance(payment, self.list_receipts(payment.id), date.max)
            self._db.execute(
                """UPDATE scheduled_payments SET kind=?, due_on=?, expected_amount=?
                   WHERE id=?""",
                (
                    payment.kind.value,
                    payment.due_on.isoformat(),
                    _value(payment.expected),
                    payment.id,
                ),
            )

    def update_receipt(self, receipt: ActualReceipt) -> None:
        with self._db:
            original = self._db.execute(
                "SELECT payment_id FROM actual_receipts WHERE id=?", (receipt.id,)
            ).fetchone()
            if original is None:
                raise ValueError("Receipt not found")
            if receipt.payment_id != original["payment_id"]:
                raise ValueError("Cannot move receipt to another payment")
            linked = self._db.execute(
                "SELECT 1 FROM cash_events WHERE receipt_id=?", (receipt.id,)
            ).fetchone()
            if linked:
                raise ValueError("Already credited to cash: correction needed, not editing receipt")
            payment = self.get_payment(receipt.payment_id)
            others = [r for r in self.list_receipts(payment.id) if r.id != receipt.id]
            payment_balance(payment, (*others, receipt), date.max)
            self._db.execute(
                "UPDATE actual_receipts SET received_on=?, amount=? WHERE id=?",
                (receipt.received_on.isoformat(), _value(receipt.amount), receipt.id),
            )

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

    def list_bond_lots(self) -> tuple[BondLot, ...]:
        """All bond lots; returns domain values, never SQLite floating point."""
        rows = self._db.execute("SELECT id FROM bond_lots ORDER BY id").fetchall()
        return tuple(self.get_bond_lot(row["id"]) for row in rows)

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

    def list_deposits(self) -> tuple[Deposit, ...]:
        """All deposit contracts, independently from scheduled cash events."""
        rows = self._db.execute("SELECT id FROM deposits ORDER BY id").fetchall()
        return tuple(self.get_deposit(row["id"]) for row in rows)

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
