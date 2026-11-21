"""SQLite persistence for cash accounts, one opening snapshot and dated cash entries.

The Cash repository extends the existing Bank repository on the same connection.
No money enters an account merely because a payment is scheduled or recorded.
"""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path

from fin.domain.accounts import (
    AccountKind,
    Bank,
    SecuritiesAccount,
    CashAccount,
    CashEvent,
    CashEventKind,
    CashLeg,
    OpeningBalance,
    OpeningSnapshot,
    actual_balances,
)
from fin.domain.money import CurrencyMismatchError, Money
from fin.domain.monthly_control import MonthlyControl, MonthlyControlBalance, is_month_end
from fin.persistence.deletions import (
    DeletionPlan,
    execute_deletion,
    plan_deletion,
)
from fin.persistence.sqlite_bank import SQLiteBankRepository


def _money(value: str, currency: str) -> Money:
    return Money(Decimal(value), currency)


class SQLiteCashRepository(SQLiteBankRepository):
    """Access Bank and Cash records in one versioned offline SQLite database.

    One closing opening snapshot defines the cutoff. Monthly observations are
    read-only evidence until the user confirms a separate correction event.
    """

    def __init__(self, path: str | Path) -> None:
        super().__init__(path)

    def plan_delete(self, kind: str, identifier: str) -> DeletionPlan:
        """Preview dependencies without modifying data."""
        return plan_deletion(self._db, kind, identifier)

    def delete_with_plan(self, kind: str, identifier: str, expected_digest: str) -> DeletionPlan:
        """Delete only after a signed user review; one SQLite transaction."""
        return execute_deletion(self._db, kind, identifier, expected_digest)

    def add_bank(self, bank: Bank) -> None:
        """A bank is an institution; it owns accounts but contains no money itself."""
        with self._db:
            if any(item.name.casefold() == bank.name.casefold() for item in self.list_banks()):
                raise ValueError("Bank already exists")
            self._db.execute(
                "INSERT INTO banks (id, name, website_url, cabinet_url) VALUES (?, ?, ?, ?)",
                (bank.id, bank.name.strip(), bank.website_url, bank.cabinet_url),
            )

    def update_bank(self, bank: Bank) -> None:
        with self._db:
            if self.get_bank(bank.id) is None:
                raise ValueError("Bank not found")
            if any(
                item.id != bank.id and item.name.casefold() == bank.name.casefold()
                for item in self.list_banks()
            ):
                raise ValueError("Bank already exists")
            self._db.execute(
                "UPDATE banks SET name=?, website_url=?, cabinet_url=? WHERE id=?",
                (bank.name.strip(), bank.website_url, bank.cabinet_url, bank.id),
            )

    def get_bank(self, bank_id: str) -> Bank | None:
        row = self._db.execute(
            "SELECT id, name, website_url, cabinet_url FROM banks WHERE id=?", (bank_id,)
        ).fetchone()
        if row is None:
            return None
        return Bank(row["id"], row["name"], row["website_url"], row["cabinet_url"])

    def list_banks(self) -> tuple[Bank, ...]:
        rows = self._db.execute("SELECT id FROM banks ORDER BY name COLLATE NOCASE, id").fetchall()
        return tuple(self.get_bank(row["id"]) for row in rows)

    def add_securities_account(self, account: SecuritiesAccount) -> None:
        """Custody accounts hold securities, not cash or ledger balances."""
        with self._db:
            if self.get_bank(account.bank_id) is None:
                raise ValueError("Unknown bank")
            self._db.execute(
                "INSERT INTO securities_accounts (id, bank_id, name) VALUES (?, ?, ?)",
                (account.id, account.bank_id, account.name),
            )

    def update_securities_account(self, account: SecuritiesAccount) -> None:
        with self._db:
            if self.get_securities_account(account.id) is None:
                raise ValueError("Securities account not found")
            if self.get_bank(account.bank_id) is None:
                raise ValueError("Unknown bank")
            self._db.execute(
                "UPDATE securities_accounts SET bank_id=?, name=? WHERE id=?",
                (account.bank_id, account.name, account.id),
            )

    def get_securities_account(self, account_id: str) -> SecuritiesAccount | None:
        row = self._db.execute(
            "SELECT id, bank_id, name FROM securities_accounts WHERE id=?", (account_id,)
        ).fetchone()
        return (
            SecuritiesAccount(row["id"], row["bank_id"], row["name"])
            if row is not None
            else None
        )

    def list_securities_accounts(self) -> tuple[SecuritiesAccount, ...]:
        rows = self._db.execute(
            "SELECT id FROM securities_accounts ORDER BY bank_id, name COLLATE NOCASE, id"
        ).fetchall()
        return tuple(self.get_securities_account(row["id"]) for row in rows)

    def update_account(self, account: CashAccount) -> None:
        """Protect identity, currency and activation history from silent changes."""
        with self._db:
            old = self.get_account(account.id)
            if old is None:
                raise ValueError("Account not found")
            if old.currency != account.currency:
                raise ValueError("Account currency cannot change")
            if old.kind != account.kind:
                raise ValueError("Bank account and physical cash cannot be converted")
            if account.opened_on is not None and account.opened_on != old.opened_on:
                raise ValueError("Account activation date cannot change")
            bank_id = account.bank_id if account.bank_id is not None else old.bank_id
            if bank_id is not None and self.get_bank(bank_id) is None:
                raise ValueError("Unknown bank")
            if account.kind == AccountKind.CASH and bank_id is not None:
                raise ValueError("Physical cash cannot belong to a bank")
            self._db.execute(
                "UPDATE cash_accounts SET name=?, bank_id=? WHERE id=?",
                (account.name, bank_id, account.id),
            )

    def update_opening_snapshot(self, snapshot: OpeningSnapshot) -> None:
        """Debug correction of the single opening snapshot, with strict cut-off."""
        with self._db:
            old = self.get_opening_snapshot()
            if old is None:
                raise ValueError("Opening snapshot not found")
            if self._db.execute("SELECT 1 FROM monthly_controls LIMIT 1").fetchone():
                raise ValueError("Opening snapshot cannot change after monthly controls")
            if self._db.execute(
                "SELECT 1 FROM cash_events WHERE effective_on <= ? LIMIT 1",
                (snapshot.as_of.isoformat(),),
            ).fetchone():
                raise ValueError("Opening date overlaps existing cash events")
            actual_balances(self.list_accounts(), snapshot, (), as_of=snapshot.as_of)
            self._db.execute(
                "UPDATE opening_snapshot SET as_of=? WHERE id=1",
                (snapshot.as_of.isoformat(),),
            )
            self._db.execute("DELETE FROM opening_balances")
            for item in snapshot.balances:
                self._db.execute(
                    """INSERT INTO opening_balances (account_id, amount, currency)
                       VALUES (?, ?, ?)""",
                    (item.account_id, str(item.amount.amount), item.amount.currency),
                )

    def update_cash_event(self, event: CashEvent) -> None:
        with self._db:
            existing = next((item for item in self.list_cash_events() if item.id == event.id), None)
            if existing is None:
                raise ValueError("Cash event not found")
            if existing.receipt_id is not None or event.receipt_id is not None:
                raise ValueError("Credited investment receipts cannot be edited here")
            if self._db.execute(
                "SELECT 1 FROM monthly_control_balances WHERE correction_event_id=?", (event.id,)
            ).fetchone():
                raise ValueError("Confirmed monthly corrections are immutable")
            opening = self.get_opening_snapshot()
            if opening is None or event.effective_on <= opening.as_of:
                raise ValueError("Cash event date must follow the opening snapshot")
            for leg in event.legs:
                account = self.get_account(leg.account_id)
                if account is None:
                    raise ValueError("Unknown cash account")
                if leg.change.currency != account.currency:
                    raise CurrencyMismatchError("Cash event currency must match the account")
                if account.opened_on is not None and event.effective_on < account.opened_on:
                    raise ValueError("Cash event predates account activation")
            self._db.execute(
                """UPDATE cash_events SET effective_on=?, kind=?, note=? WHERE id=?""",
                (event.effective_on.isoformat(), event.kind.value, event.note, event.id),
            )
            self._db.execute("DELETE FROM cash_legs WHERE event_id=?", (event.id,))
            for leg in event.legs:
                self._db.execute(
                    """INSERT INTO cash_legs (event_id, account_id, amount, currency)
                       VALUES (?, ?, ?, ?)""",
                    (event.id, leg.account_id, str(leg.change.amount), leg.change.currency),
                )

    def add_account(self, account: CashAccount) -> None:
        """New accounts after opening start at zero on a selected activation day.

        A new balance is NEVER income: fund it via an explicit dated transfer
        or manually reviewed correction, not by rewriting the opening snapshot.
        """
        with self._db:
            snapshot = self.get_opening_snapshot()
            if snapshot is None:
                if account.opened_on is not None:
                    raise ValueError("Set activation date only after the opening snapshot")
            elif account.opened_on is None or account.opened_on <= snapshot.as_of:
                raise ValueError("New accounts must have activation dates after opening snapshot")
            if account.bank_id is not None:
                if account.kind != AccountKind.BANK:
                    raise ValueError("Cash cannot be linked to a bank")
                if self.get_bank(account.bank_id) is None:
                    raise ValueError("Unknown bank")
            self._db.execute(
                """INSERT INTO cash_accounts (id, name, currency, kind, bank_id, opened_on)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    account.id,
                    account.name,
                    account.currency,
                    account.kind.value,
                    account.bank_id,
                    account.opened_on.isoformat() if account.opened_on is not None else None,
                ),
            )

    def get_account(self, account_id: str) -> CashAccount | None:
        row = self._db.execute(
            "SELECT id, name, currency, kind, bank_id, opened_on FROM cash_accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        if row is None:
            return None
        return CashAccount(
            row["id"],
            row["name"],
            row["currency"],
            AccountKind(row["kind"]),
            row["bank_id"],
            date.fromisoformat(row["opened_on"]) if row["opened_on"] else None,
        )

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
                if account.opened_on is not None and event.effective_on < account.opened_on:
                    raise ValueError("Cash event predates account activation")

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

    def record_monthly_control(
        self,
        control_id: str,
        as_of: date,
        observed: Mapping[str, Money],
        *,
        note: str | None = None,
    ) -> MonthlyControl:
        """Save a complete monthly observation; do not post cash or hide mismatches."""
        if (
            not isinstance(control_id, str)
            or not control_id.isascii()
            or not control_id.replace("_", "").replace("-", "").isalnum()
        ):
            raise ValueError("Invalid monthly control ID")
        if not is_month_end(as_of):
            raise ValueError("Monthly control must be dated on the last calendar day")
        if note is not None and (not isinstance(note, str) or not note.strip()):
            raise ValueError("Control note must be nonblank or omitted")
        with self._db:
            opening = self.get_opening_snapshot()
            if opening is None or as_of <= opening.as_of:
                raise ValueError("Monthly control must follow the opening snapshot")
            calculated = self.balances_on(as_of)
            accounts = tuple(
                account for account in self.list_accounts() if account.id in calculated
            )
            if set(observed) != set(calculated):
                raise ValueError("Supply an observed balance for every active account")
            for account in accounts:
                value = observed[account.id]
                if not isinstance(value, Money):
                    raise TypeError("Observed balance must be Money")
                if value.currency != account.currency:
                    raise CurrencyMismatchError("Monthly control currency mismatch")
            self._db.execute(
                "INSERT INTO monthly_controls (id, as_of, note) VALUES (?, ?, ?)",
                (control_id, as_of.isoformat(), note),
            )
            for account in accounts:
                self._db.execute(
                    """INSERT INTO monthly_control_balances
                       (control_id, account_id, observed_amount, calculated_amount, currency)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        control_id,
                        account.id,
                        str(observed[account.id].amount),
                        str(calculated[account.id].amount),
                        account.currency,
                    ),
                )
        return next(item for item in self.list_monthly_controls() if item.id == control_id)

    def list_monthly_controls(self) -> tuple[MonthlyControl, ...]:
        """Return immutable observations and their linked correction event IDs."""
        snapshots = self._db.execute(
            "SELECT id, as_of, note FROM monthly_controls ORDER BY as_of, id"
        ).fetchall()
        result = []
        for snapshot in snapshots:
            rows = self._db.execute(
                """SELECT account_id, observed_amount, calculated_amount, currency,
                          correction_event_id
                   FROM monthly_control_balances WHERE control_id=? ORDER BY account_id""",
                (snapshot["id"],),
            ).fetchall()
            result.append(
                MonthlyControl(
                    snapshot["id"],
                    date.fromisoformat(snapshot["as_of"]),
                    tuple(
                        MonthlyControlBalance(
                            row["account_id"],
                            _money(row["observed_amount"], row["currency"]),
                            _money(row["calculated_amount"], row["currency"]),
                            row["correction_event_id"],
                        )
                        for row in rows
                    ),
                    snapshot["note"],
                )
            )
        return tuple(result)

    def confirm_monthly_correction(
        self,
        control_id: str,
        account_id: str,
        *,
        expected_difference: Money,
        event_id: str,
        reason: str,
    ) -> CashEvent:
        """Atomically post an explicitly approved delta, linked to its evidence.

        Expected delta is an optimistic form guard: reject when another
        transaction changed the observed month's computed balance meanwhile.
        """
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Explain the monthly correction before confirming it")
        with self._db:
            row = self._db.execute(
                """SELECT c.as_of, b.observed_amount, b.currency, b.correction_event_id
                   FROM monthly_controls c
                   JOIN monthly_control_balances b ON b.control_id=c.id
                   WHERE c.id=? AND b.account_id=?""",
                (control_id, account_id),
            ).fetchone()
            if row is None:
                raise ValueError("Monthly control account not found")
            if row["correction_event_id"] is not None:
                raise ValueError("Monthly control difference was already corrected")
            as_of = date.fromisoformat(row["as_of"])
            current = self.balances_on(as_of)[account_id]
            observed = _money(row["observed_amount"], row["currency"])
            difference = observed - current
            if difference != expected_difference:
                raise ValueError("Balance changed since review; reload and confirm again")
            if difference.amount == 0:
                raise ValueError("No difference to correct")
            event = CashEvent(
                event_id,
                as_of,
                CashEventKind.CORRECTION,
                (CashLeg(account_id, difference),),
                note=reason,
            )
            self._db.execute(
                """INSERT INTO cash_events (id, effective_on, kind, receipt_id, note)
                   VALUES (?, ?, ?, NULL, ?)""",
                (event.id, event.effective_on.isoformat(), event.kind.value, event.note),
            )
            self._db.execute(
                """INSERT INTO cash_legs (event_id, account_id, amount, currency)
                   VALUES (?, ?, ?, ?)""",
                (event.id, account_id, str(difference.amount), difference.currency),
            )
            self._db.execute(
                """UPDATE monthly_control_balances SET correction_event_id=?
                   WHERE control_id=? AND account_id=? AND correction_event_id IS NULL""",
                (event.id, control_id, account_id),
            )
        return event

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
