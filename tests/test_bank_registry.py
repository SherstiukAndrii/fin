"""Bank registry, multiple accounts and safe SQLite v5 -> v6 migration.

Every name, amount, bank URL, and security identifier here is synthetic.
"""

import os
import sqlite3
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from fin.domain.accounts import (
    AccountKind,
    Bank,
    CashAccount,
    CashEvent,
    CashEventKind,
    CashLeg,
    OpeningBalance,
    OpeningSnapshot,
    SecuritiesAccount,
)
from fin.domain.money import CurrencyMismatchError, Money
from fin.persistence import SCHEMA_VERSION, SQLiteCashRepository
from fin.persistence.sqlite_bank import (
    _SCHEMA,
    _SCHEMA_V2,
    _SCHEMA_V3,
    _SCHEMA_V4,
    _SCHEMA_V5,
)
from fin.web import create_app


def money(amount: str, currency: str = "UAH") -> Money:
    return Money(Decimal(amount), currency)


def test_bank_links_are_strictly_https_and_no_password_in_urls():
    valid = Bank(
        "fictional",
        "Imaginary Bank",
        "https://example.org",
        "https://online.example.org/login",
    )
    assert valid.website_url == "https://example.org"
    for url in (
        "javascript:alert(1)",
        "data:text/html,hello",
        "file:///tmp/secrets",
        "http://example.org",
        "https://name:pass@example.org",
        "https://example.org/#insecure-fragment",
        "https://example.org\nalert(1)",
        "/relative",
    ):
        with pytest.raises(ValueError, match="HTTPS"):
            Bank("fictional", "Imaginary Bank", website_url=url)


def test_many_cash_accounts_same_bank_currency_and_securities_are_distinct(tmp_path):
    path = tmp_path / "invented.sqlite3"
    with SQLiteCashRepository(path) as repo:
        repo.add_bank(Bank("fictional", "Imaginary Bank", "https://example.org"))
        repo.add_account(CashAccount("daily", "Everyday", "UAH", AccountKind.BANK, "fictional"))
        repo.add_account(CashAccount("savings", "Savings", "UAH", AccountKind.BANK, "fictional"))
        repo.add_account(CashAccount("dollars", "USD pocket", "USD", AccountKind.BANK, "fictional"))
        repo.add_account(CashAccount("cash", "On hand", "UAH", AccountKind.CASH))
        repo.add_securities_account(SecuritiesAccount("bonds", "fictional", "OVGZ custody"))
        repo.save_opening_snapshot(
            OpeningSnapshot(
                date(2028, 1, 1),
                (
                    OpeningBalance("daily", money("1000")),
                    OpeningBalance("savings", money("500")),
                    OpeningBalance("dollars", money("150", "USD")),
                    OpeningBalance("cash", money("40")),
                ),
            )
        )
        assert len(repo.list_banks()) == 1
        assert len(repo.list_securities_accounts()) == 1
        assert repo.balances_on(date(2028, 1, 1)) == {
            "cash": money("40"),
            "daily": money("1000"),
            "dollars": money("150", "USD"),
            "savings": money("500"),
        }
        assert repo.get_securities_account("bonds").bank_id == "fictional"
        with pytest.raises(ValueError, match="cash"):
            CashAccount("wrong", "Cash", "UAH", AccountKind.CASH, "fictional")
    with SQLiteCashRepository(path) as repo:
        assert repo.schema_version() == SCHEMA_VERSION == 6
        assert repo.get_account("savings").bank_id == "fictional"
        assert repo.get_bank("fictional").website_url == "https://example.org"


def test_new_accounts_after_opening_are_zero_and_history_is_not_rewritten(tmp_path):
    path = tmp_path / "fictional.sqlite3"
    with SQLiteCashRepository(path) as repo:
        repo.add_bank(Bank("demo", "Fictional Bank"))
        repo.add_account(CashAccount("main", "Main", "UAH", AccountKind.BANK, "demo"))
        repo.save_opening_snapshot(
            OpeningSnapshot(date(2027, 1, 1), (OpeningBalance("main", money("1000")),))
        )
        # Two UAH accounts in the same bank can be added after opening.
        repo.add_account(
            CashAccount(
                "second",
                "Second",
                "UAH",
                AccountKind.BANK,
                "demo",
                date(2027, 3, 1),
            )
        )
        repo.add_account(
            CashAccount(
                "cash",
                "Pocket",
                "UAH",
                AccountKind.CASH,
                opened_on=date(2027, 3, 2),
            )
        )
        repo.add_account(
            CashAccount(
                "foreign",
                "Foreign",
                "USD",
                AccountKind.BANK,
                "demo",
                date(2027, 4, 1),
            )
        )
        assert repo.balances_on(date(2027, 2, 28)) == {"main": money("1000")}
        assert repo.balances_on(date(2027, 3, 1)) == {"main": money("1000"), "second": money("0")}
        assert "cash" not in repo.balances_on(date(2027, 3, 1))
        assert "foreign" not in repo.balances_on(date(2027, 3, 2))
        with pytest.raises(ValueError, match="activation"):
            repo.add_account(CashAccount("bad", "Bad", "UAH", AccountKind.BANK, "demo"))
        with pytest.raises(ValueError, match="predates"):
            repo.add_cash_event(
                CashEvent(
                    "bad",
                    date(2027, 2, 28),
                    CashEventKind.INFLOW,
                    (CashLeg("second", money("10")),),
                )
            )
        repo.add_cash_event(
            CashEvent(
                "transfer",
                date(2027, 3, 2),
                CashEventKind.TRANSFER,
                (CashLeg("main", money("-70")), CashLeg("second", money("70"))),
            )
        )
        amounts = repo.balances_on(date(2027, 3, 2))
        assert amounts["main"] == money("930")
        assert amounts["second"] == money("70")
        assert amounts["cash"] == money("0")
        assert sum(v.amount for v in amounts.values()) == Decimal("1000")
        assert repo.get_opening_snapshot().balances == (OpeningBalance("main", money("1000")),)
        with pytest.raises(CurrencyMismatchError):
            repo.add_cash_event(
                CashEvent(
                    "wrong",
                    date(2027, 4, 2),
                    CashEventKind.TRANSFER,
                    (
                        CashLeg("main", money("-2")),
                        CashLeg("foreign", money("2", "USD")),
                    ),
                )
            )
    with SQLiteCashRepository(path) as repo:
        assert repo.balances_on(date(2027, 2, 28)) == {"main": money("1000")}
        assert repo.balances_on(date(2027, 3, 2))["second"] == money("70")
        assert repo.get_account("second").opened_on == date(2027, 3, 1)


def test_monthly_control_does_not_include_future_accounts(tmp_path):
    with SQLiteCashRepository(tmp_path / "controls.sqlite3") as repo:
        repo.add_account(CashAccount("old", "Existing", "UAH", AccountKind.CASH))
        repo.save_opening_snapshot(
            OpeningSnapshot(date(2027, 1, 1), (OpeningBalance("old", money("100")),))
        )
        repo.add_account(
            CashAccount(
                "future",
                "Later",
                "UAH",
                AccountKind.CASH,
                opened_on=date(2027, 4, 1),
            )
        )
        january = repo.record_monthly_control("jan", date(2027, 1, 31), {"old": money("100")})
        assert len(january.balances) == 1
        with pytest.raises(ValueError, match="active"):
            repo.record_monthly_control(
                "bad",
                date(2027, 2, 28),
                {"old": money("100"), "future": money("0")},
            )
        april = repo.record_monthly_control(
            "apr",
            date(2027, 4, 30),
            {"old": money("100"), "future": money("0")},
        )
        assert len(april.balances) == 2


def test_v5_migration_preserves_ledger_and_creates_private_backup(tmp_path, monkeypatch):
    private = tmp_path / "private"
    monkeypatch.setenv("XDG_DATA_HOME", str(private))
    path = tmp_path / "old-v5.sqlite3"
    with sqlite3.connect(path) as db:
        for schema in (_SCHEMA, _SCHEMA_V2, _SCHEMA_V3, _SCHEMA_V4, _SCHEMA_V5):
            for statement in schema:
                db.execute(statement)
        db.execute("PRAGMA user_version = 5")
        db.execute(
            "INSERT INTO cash_accounts(id,name,currency,kind) VALUES"
            " ('u1','Imaginary Bank','UAH','bank'),"
            " ('u2','Imaginary Bank','USD','bank'),"
            " ('hand','Pocket','UAH','cash')"
        )
        db.execute("INSERT INTO opening_snapshot (id,as_of) VALUES (1,'2027-01-01')")
        db.execute(
            "INSERT INTO opening_balances(account_id,amount,currency) VALUES"
            " ('u1','1000.25','UAH'),('u2','10.50','USD'),('hand','40','UAH')"
        )
    with SQLiteCashRepository(path) as repo:
        assert repo.schema_version() == 6
        assert len(repo.list_banks()) == 1
        assert repo.get_account("u1").bank_id == repo.get_account("u2").bank_id
        assert repo.get_account("hand").bank_id is None
        assert repo.balances_on(date(2027, 1, 1)) == {
            "u1": money("1000.25"),
            "u2": money("10.50", "USD"),
            "hand": money("40"),
        }
    saved = list((private / "fin" / "backups").glob("fin-pre-v6-*.sqlite3"))
    assert len(saved) == 1
    assert os.stat(saved[0]).st_mode & 0o077 == 0
    with sqlite3.connect(saved[0]) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 5
        assert backup.execute("SELECT COUNT(*) FROM opening_balances").fetchone()[0] == 3
    with SQLiteCashRepository(path) as repo:
        assert repo.schema_version() == 6
    assert len(list((private / "fin" / "backups").glob("fin-pre-v6-*.sqlite3"))) == 1


def test_bank_and_securities_deletion_requires_preview_and_no_linked_accounts(tmp_path):
    with SQLiteCashRepository(tmp_path / "entities.sqlite3") as repo:
        repo.add_bank(Bank("f", "Imaginary Bank"))
        repo.add_account(CashAccount("usd", "USD", "USD", AccountKind.BANK, "f"))
        repo.add_securities_account(SecuritiesAccount("custody", "f", "Custody"))
        with pytest.raises(ValueError, match="перенеси счета"):
            repo.plan_delete("bank", "f")
        with pytest.raises(ValueError, match="Bank"):
            repo.add_bank(Bank("another", "Imaginary Bank"))
        repo.update_bank(
            Bank("f", "Imaginary Bank", "https://example.org", "https://login.example.org")
        )
        assert repo.get_bank("f").cabinet_url == "https://login.example.org"
        custody_plan = repo.plan_delete("securities_account", "custody")
        repo.delete_with_plan("securities_account", "custody", custody_plan.digest)
        money_plan = repo.plan_delete("account", "usd")
        repo.delete_with_plan("account", "usd", money_plan.digest)
        bank_plan = repo.plan_delete("bank", "f")
        repo.delete_with_plan("bank", "f", bank_plan.digest)
        assert repo.list_banks() == ()


def test_task_first_web_supports_bank_many_accounts_and_external_links(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "private"))
    path = tmp_path / "fictional-ui.sqlite3"
    with SQLiteCashRepository(path):
        pass
    with TestClient(create_app(path), base_url="http://localhost") as client:
        token = client.app.state.csrf_token

        def submit(route, **fields):
            return client.post(
                f"/bank/{route}",
                data={"csrf": token, **fields},
                follow_redirects=False,
            )

        def created(route, **fields):
            response = submit(route, **fields)
            assert response.status_code == 303

        created(
            "bank",
            id="demo",
            name="Imaginary Bank",
            website_url="https://example.org",
            cabinet_url="https://login.example.org",
        )
        created(
            "account",
            id="ua1",
            kind="bank",
            bank_id="demo",
            name="Primary UAH",
            currency="UAH",
        )
        created(
            "account",
            id="ua2",
            kind="bank",
            bank_id="demo",
            name="Savings UAH",
            currency="UAH",
        )
        created("securities_account", id="custody", bank_id="demo", name="OVGZ custody")

        bank_page = client.get("/bank?area=money&target=bank:demo")
        assert bank_page.status_code == 200
        assert "Primary UAH" in bank_page.text
        assert "Savings UAH" in bank_page.text
        assert "OVGZ custody" in bank_page.text
        assert 'href="https://example.org"' in bank_page.text
        assert 'href="https://login.example.org"' in bank_page.text
        assert 'rel="noopener noreferrer"' in bank_page.text
        assert 'data-edit-action="/bank/edit_bank"' in bank_page.text

        forms = client.get("/bank/forms")
        assert 'id="form-bank"' in forms.text
        assert 'id="form-account"' in forms.text
        assert 'id="form-securities-account"' in forms.text
        assert 'name="bank_id"' in forms.text

        created("opening", as_of="2027-01-01", balance_ua1="400", balance_ua2="200")
        created(
            "account",
            id="ua3",
            kind="bank",
            bank_id="demo",
            name="Third UAH",
            currency="UAH",
            opened_on="2027-02-01",
        )
        created(
            "cash",
            id="new_cash",
            name="Pocket",
            currency="UAH",
            opened_on="2027-02-05",
        )
        before = client.get("/bank?area=money&as_of=2027-01-15")
        after = client.get("/bank?area=money&target=ua3&as_of=2027-02-01")
        assert "Third UAH" in before.text
        assert "Учёт с 2027-02-01" in after.text
        assert "с нуля" in after.text

        with SQLiteCashRepository(path) as repo:
            assert repo.balances_on(date(2027, 1, 15)) == {
                "ua1": money("400"),
                "ua2": money("200"),
            }
            assert repo.balances_on(date(2027, 2, 1))["ua3"] == money("0")

        invalid = submit(
            "edit_bank",
            id="demo",
            name="Imaginary Bank",
            website_url="javascript:alert(1)",
            cabinet_url="",
        )
        assert invalid.status_code == 422
        with SQLiteCashRepository(path) as repo:
            assert repo.get_bank("demo").website_url == "https://example.org"
        preview = client.post(
            "/bank/delete/preview",
            data={"csrf": token, "kind": "bank", "identifier": "demo"},
        )
        assert preview.status_code == 422
