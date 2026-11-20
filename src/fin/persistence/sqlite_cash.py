"""SQLite persistence for cash accounts, one opening snapshot and dated cash entries.

The Cash repository extends the existing Bank repository on the same connection.
No money enters an account merely because a payment is scheduled or recorded.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

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
from fin.persistence.sqlite_bank import SQLiteBankRepository


def _money(value: str, currency: str) -> Money:
    return Money(Decimal(value), currency)


class SQLiteCashRepository(SQLiteBankRepository):
    """Access Bank and Cash records in one versioned offline SQLite database.

    V2 supports one immutable opening snapshot of all accounts. New accounts
    after that snapshot and historical reconciliation are deferred to later PRs.
    """

    def __init__(self, path: str | Path) -> None:
        super().__init__(path)

    def add_account(self, account: CashAccount) -> None:
        with self._db:
            if self._db.execute("SELECT 1 FROM opening_snapshot").fetchone() is not None:
                raise ValueError("New accounts after opening snapshot need explicit opening rules")
            self._db.execute(
                """INSERT INTO cash_accounts (id, name, currency, kind)
                   VALUES (?, ?, ?, ?)""",
                (account.id, account.name, account.currency, account.kind.value),
            )

    def get_account(self, account_id: str) -> CashAccount | None:
        row = self._db.execute(
            "SELECT id, name, currency, kind FROM cash_accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        if row is None:
            return None
        return CashAccount(row["id"], row["name"], row["currency"], AccountKind(row["kind"]))

    def list_accounts(self) -> tuple[CashAccount, ...]:
        rows = self._db.execute("SELECT id FROM cash_accounts ORDER BY id").fetchall()
        return tuple(self.get_account(row["id"]) for row in rows)

    def save_opening_snapshot(self, snapshot: OpeningSnapshot) -> None:
        with self._db:
            # Require an explicit opening amount (including zero) for every
            # account; do not invent balances for missing rows.
            actual_balances(
                self.list_accounts(),
                snapshot,
                (),
                as_of=snapshot.as_of,
            )
            self._db.execute(
                "INSERT INTO opening_snapshot (id, as_of) VALUES (1, ?)",
                (snapshot.as_of.isoformat(),),
            )
            for balance in snapshot.balances:
                self._db.execute(
                    """INSERT INTO opening_balances (account_id, amount, currency)
                       VALUES (?, ?, ?)""",
                    (
                        balance.account_id,
                        str(balance.amount.amount),
                        balance.amount.currency,
                    ),
                )

    def get_opening_snapshot(self) -> OpeningSnapshot | None:
        row = self._db.execute("SELECT as_of FROM opening_snapshot WHERE id = 1").fetchone()
        if row is None:
            return None
        balances = self._db.execute(
            "SELECT account_id, amount, currency FROM opening_balances ORDER BY account_id"
        ).fetchall()
        return OpeningSnapshot(
            date.fromisoformat(row["as_of"]),
            tuple(
                OpeningBalance(item["account_id"], _money(item["amount"], item["currency"]))
                for item in balances
            ),
        )

    def add_cash_event(self, event: CashEvent) -> None:
        """Post cash exactly once; linked receipts must agree on date and amount."""
        with self._db:
            snapshot = self.get_opening_snapshot()
            if snapshot is None:
                raise ValueError("Create an opening snapshot before posting cash")
            if event.effective_on <= snapshot.as_of:
                raise ValueError("Cash event must be after the opening closing date")

            for leg in event.legs:
                account = self.get_account(leg.account_id)
                if account is None:
                    raise ValueError("Cash event references an unknown account")
                if leg.change.currency != account.currency:
                    raise CurrencyMismatchError("Cash event currency does not match account")

            if event.kind == CashEventKind.RECEIPT:
                receipt = self._db.execute(
                    """SELECT received_on, amount, currency
                       FROM actual_receipts WHERE id = ?""",
                    (event.receipt_id,),
                ).fetchone()
                if receipt is None:
                    raise ValueError("Cash receipt event must link an existing actual receipt")
                expected = _money(receipt["amount"], receipt["currency"])
                if event.effective_on != date.fromisoformat(receipt["received_on"]):
                    raise ValueError("Cash receipt posting date must match actual receipt date")
                if event.legs[0].change != expected:
                    raise ValueError("Cash receipt posting amount must match actual receipt")

            self._db.execute(
                """INSERT INTO cash_events (id, effective_on, kind, receipt_id, note)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    event.id,
                    event.effective_on.isoformat(),
                    event.kind.value,
                    event.receipt_id,
                    event.note,
                ),
            )
            for leg in event.legs:
                self._db.execute(
                    """INSERT INTO cash_legs (event_id, account_id, amount, currency)
                       VALUES (?, ?, ?, ?)""",
                    (
                        event.id,
                        leg.account_id,
                        str(leg.change.amount),
                        leg.change.currency,
                    ),
                )

    def list_cash_events(self) -> tuple[CashEvent, ...]:
        rows = self._db.execute(
            """SELECT id, effective_on, kind, receipt_id, note
               FROM cash_events ORDER BY effective_on, id"""
        ).fetchall()
        result: list[CashEvent] = []
        for row in rows:
            legs = self._db.execute(
                """SELECT account_id, amount, currency
                   FROM cash_legs WHERE event_id = ? ORDER BY account_id""",
                (row["id"],),
            ).fetchall()
            result.append(
                CashEvent(
                    id=row["id"],
                    effective_on=date.fromisoformat(row["effective_on"]),
                    kind=CashEventKind(row["kind"]),
                    legs=tuple(
                        CashLeg(leg["account_id"], _money(leg["amount"], leg["currency"]))
                        for leg in legs
                    ),
                    receipt_id=row["receipt_id"],
                    note=row["note"],
                )
            )
        return tuple(result)

    def balances_on(self, as_of: date) -> dict[str, Money]:
        """Actual cash only. Never auto-add future coupons, principal or interest."""
        opening = self.get_opening_snapshot()
        if opening is None:
            raise ValueError("Opening snapshot is required for cash calculation")
        return actual_balances(
            self.list_accounts(),
            opening,
            self.list_cash_events(),
            as_of=as_of,
        )
