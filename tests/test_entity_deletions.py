"""Safe deletion of fictional Bank entities and all linked cash effects."""

import hashlib
import json
import re
import sqlite3
from datetime import date
from decimal import Decimal
from html import unescape

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
    ScheduledPayment,
)
from fin.domain.money import Money
from fin.domain.securities_import import SecuritiesEntry
from fin.persistence import SQLiteCashRepository
from fin.persistence.deletions import sign_deletion, verify_deletion
from fin.web import create_app


def money(value: str) -> Money:
    return Money(Decimal(value), "UAH")


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "fictional-fin.sqlite3"
    with SQLiteCashRepository(path) as repo:
        repo.add_account(CashAccount("bank", "Fictional bank", "UAH", AccountKind.BANK))
        repo.save_opening_snapshot(
            OpeningSnapshot(
                date(2028, 1, 1),
                (OpeningBalance("bank", money("100")),),
            )
        )
        yield repo
    with sqlite3.connect(path) as raw:
        assert raw.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert raw.execute("PRAGMA foreign_key_check").fetchall() == []


def add_bond(repo):
    repo.add_bond_lot(
        BondLot(
            "lot",
            BondInstrument("UA9876543210", money("1000"), date(2029, 12, 31)),
            date(2028, 1, 2),
            2,
            None,
            None,
            "Fictional Custodian",
        )
    )
    repo.add_payment(
        ScheduledPayment(
            "coupon",
            "lot",
            PaymentKind.COUPON,
            date(2028, 3, 1),
            money("60"),
        )
    )


def add_paid_coupon(repo):
    add_bond(repo)
    repo.add_receipt(ActualReceipt("receipt", "coupon", date(2028, 3, 2), money("40")))
    repo.add_cash_event(
        CashEvent(
            "credit",
            date(2028, 3, 2),
            CashEventKind.RECEIPT,
            (CashLeg("bank", money("40")),),
            receipt_id="receipt",
        )
    )


def add_import(repo, name, kind, quantity, day):
    repo.import_securities_entries(
        (
            SecuritiesEntry(
                sha("file-" + name),
                sha("event-" + name),
                "Fictional Custodian",
                "UA9876543210",
                day,
                kind,
                quantity,
                money("1000") if kind == "closing" else None,
            ),
        )
    )


def apply(repo, kind, identifier):
    preview = repo.plan_delete(kind, identifier)
    removed = repo.delete_with_plan(kind, identifier, preview.digest)
    assert removed == preview
    return preview


def test_cash_event_deletion_reverses_ledger_without_affecting_opening(db):
    db.add_cash_event(
        CashEvent(
            "inflow",
            date(2028, 1, 5),
            CashEventKind.INFLOW,
            (CashLeg("bank", money("20")),),
        )
    )
    preview = db.plan_delete("cash_event", "inflow")
    assert ("Денежные операции", 1) in preview.counts
    assert preview.warnings
    apply(db, "cash_event", "inflow")
    assert db.list_cash_events() == ()
    assert db.balances_on(date(2028, 2, 1))["bank"] == money("100")
    assert db.get_opening_snapshot() is not None


def test_receipt_removal_undoes_credit_but_retains_plan(db):
    add_paid_coupon(db)
    assert db.balances_on(date(2028, 3, 3))["bank"] == money("140")
    preview = apply(db, "receipt", "receipt")
    assert ("Денежные операции", 1) in preview.counts
    assert db.list_receipts("coupon") == ()
    assert db.get_payment("coupon") is not None
    assert db.list_cash_events() == ()
    assert db.balances_on(date(2028, 3, 3))["bank"] == money("100")


def test_payment_removal_cascades_receipts_and_posted_credit(db):
    add_paid_coupon(db)
    apply(db, "payment", "coupon")
    assert db.get_payment("coupon") is None
    assert db.list_receipts("coupon") == ()
    assert db.list_cash_events() == ()
    assert db.get_bond_lot("lot") is not None


def test_holding_removal_cascades_schedule_facts_and_unshared_instrument(db):
    add_paid_coupon(db)
    preview = apply(db, "holding", "lot")
    assert ("Лоты ОВГЗ", 1) in preview.counts
    assert ("Справочники выпусков ОВГЗ", 1) in preview.counts
    assert db.list_bond_lots() == ()
    assert db.get_payment("coupon") is None
    assert db.list_cash_events() == ()
    assert db.balances_on(date(2028, 3, 3))["bank"] == money("100")


def test_shared_instrument_not_deleted_with_only_one_lot(db):
    add_bond(db)
    other = BondLot(
        "other",
        BondInstrument("UA9876543210", money("1000"), date(2029, 12, 31)),
        date(2028, 1, 3),
        3,
        None,
        None,
        "Other fictional custodian",
    )
    db.add_bond_lot(other)
    preview = apply(db, "holding", "lot")
    assert all(name != "Справочники выпусков ОВГЗ" for name, _ in preview.counts)
    assert db.get_bond_lot("other") == other
    assert len(db.list_bond_lots()) == 1


def test_deposit_removal_deletes_its_schedule_and_keeps_cash(db):
    db.add_deposit(
        Deposit("term", "Fictional bank", money("500"), date(2028, 1, 2), date(2028, 12, 31), None)
    )
    db.add_payment(
        ScheduledPayment("interest", "term", PaymentKind.INTEREST, date(2028, 3, 1), money("20"))
    )
    apply(db, "holding", "term")
    assert db.get_deposit("term") is None
    assert db.get_payment("interest") is None
    assert db.balances_on(date(2028, 3, 3))["bank"] == money("100")


def test_linked_monthly_correction_can_be_undone_without_erasing_observation(db):
    db.record_monthly_control("march", date(2028, 3, 31), {"bank": money("105")})
    db.confirm_monthly_correction(
        "march",
        "bank",
        expected_difference=money("5"),
        event_id="correction",
        reason="Fictional correction",
    )
    assert db.balances_on(date(2028, 4, 1))["bank"] == money("105")
    preview = apply(db, "cash_event", "correction")
    assert any("сверка" in warning for warning in preview.warnings)
    controls = db.list_monthly_controls()
    assert len(controls) == 1
    assert controls[0].balances[0].correction_event_id is None
    assert db.balances_on(date(2028, 4, 1))["bank"] == money("100")


def test_deleting_monthly_control_also_removes_its_confirmed_correction(db):
    db.record_monthly_control("march", date(2028, 3, 31), {"bank": money("105")})
    db.confirm_monthly_correction(
        "march",
        "bank",
        expected_difference=money("5"),
        event_id="correction",
        reason="Fictional correction",
    )
    preview = apply(db, "control", "march")
    assert ("Месячные сверки", 1) in preview.counts
    assert db.list_monthly_controls() == ()
    assert db.list_cash_events() == ()
    assert db.balances_on(date(2028, 4, 1))["bank"] == money("100")


def test_account_and_opening_dependencies_are_blocked_until_resolved(db):
    db.add_cash_event(
        CashEvent(
            "inflow",
            date(2028, 1, 5),
            CashEventKind.INFLOW,
            (CashLeg("bank", money("20")),),
        )
    )
    with pytest.raises(ValueError, match="денежные операции"):
        db.plan_delete("account", "bank")
    with pytest.raises(ValueError, match="денежные операции"):
        db.plan_delete("opening", "1")
    apply(db, "cash_event", "inflow")
    preview = apply(db, "account", "bank")
    assert ("Начальные снимки", 1) in preview.counts
    assert db.get_account("bank") is None
    assert db.get_opening_snapshot() is None


def test_opening_can_be_removed_without_deleting_account(db):
    apply(db, "opening", "1")
    assert db.list_accounts()[0].id == "bank"
    assert db.get_opening_snapshot() is None
    db.save_opening_snapshot(
        OpeningSnapshot(date(2028, 1, 2), (OpeningBalance("bank", money("200")),))
    )
    assert db.balances_on(date(2028, 2, 1))["bank"] == money("200")


def test_monthly_controls_block_account_and_opening_delete(db):
    db.record_monthly_control("march", date(2028, 3, 31), {"bank": money("100")})
    with pytest.raises(ValueError, match="месячных сверках"):
        db.plan_delete("account", "bank")
    with pytest.raises(ValueError, match="месячные сверки"):
        db.plan_delete("opening", "1")
    apply(db, "control", "march")
    apply(db, "account", "bank")
    assert db.list_accounts() == ()


def test_one_imported_record_or_entire_position_can_be_deleted(db):
    add_import(db, "movement", "movement", 5, date(2028, 2, 1))
    add_import(db, "closing", "closing", 10, date(2028, 3, 1))
    assert db.securities_positions_on(date(2028, 3, 2))[0]["quantity"] == 10
    apply(db, "securities_import", sha("file-closing"))
    assert db.securities_positions_on(date(2028, 3, 2))[0]["quantity"] == 5
    identifier = json.dumps(["Fictional Custodian", "UA9876543210"])
    preview = apply(db, "import_position", identifier)
    assert ("Записи из выписок", 1) in preview.counts
    assert db.list_securities_entries() == ()
    assert db.list_cash_events() == ()


def test_review_digest_rejects_new_dependent_records_atomically(db):
    add_bond(db)
    preview = db.plan_delete("holding", "lot")
    db.add_receipt(ActualReceipt("new-receipt", "coupon", date(2028, 3, 2), money("20")))
    with pytest.raises(ValueError, match="изменились"):
        db.delete_with_plan("holding", "lot", preview.digest)
    assert db.get_bond_lot("lot") is not None
    assert len(db.list_receipts("coupon")) == 1
    assert db.list_cash_events() == ()


def test_invalid_type_and_unknown_ids_never_mutate_database(db):
    with pytest.raises(ValueError, match="Неизвестный"):
        db.plan_delete("cash_accounts; DROP TABLE cash_accounts", "bank")
    with pytest.raises(ValueError, match="не найдена"):
        db.plan_delete("receipt", "unknown")
    assert len(db.list_accounts()) == 1


def test_signed_confirmation_blocks_changes_and_replay(db):
    preview = db.plan_delete("opening", "1")
    key = b"fictional-test-secret"
    token = sign_deletion(preview, key)
    assert verify_deletion(token, key) == ("opening", "1", preview.digest)
    with pytest.raises(ValueError, match="изменено"):
        verify_deletion(token[:-3] + "xyz", key)
    with pytest.raises(ValueError, match="изменено"):
        verify_deletion(token, b"different-key")


@pytest.fixture
def browser(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "local-private-data"))
    path = tmp_path / "fin-browser.sqlite3"
    with SQLiteCashRepository(path) as repo:
        repo.add_account(CashAccount("bank", "Test bank", "UAH", AccountKind.BANK))
        repo.save_opening_snapshot(
            OpeningSnapshot(date(2028, 1, 1), (OpeningBalance("bank", money("100")),))
        )
        add_paid_coupon(repo)
    with TestClient(create_app(path), base_url="http://localhost") as client:
        yield client, path, tmp_path / "local-private-data" / "fin" / "backups"


def test_web_delete_preview_requires_explicit_word_and_creates_backup(browser):
    client, path, backup_directory = browser
    csrf = client.app.state.csrf_token
    view = client.get("/bank/forms?as_of=2028-03-03")
    assert view.status_code == 200
    assert "Удалить вложение" in view.text
    assert "Удалить выплату" in view.text
    assert "Удалить факт" in view.text
    assert "Удалить операцию" in view.text

    forbidden = client.post(
        "/bank/delete/preview",
        data={"kind": "receipt", "identifier": "receipt"},
    )
    assert forbidden.status_code == 403
    page = client.post(
        "/bank/delete/preview?as_of=2028-03-03",
        data={"csrf": csrf, "kind": "receipt", "identifier": "receipt"},
    )
    assert page.status_code == 200
    assert "Подтверждение удаления" in page.text
    assert "Денежные операции" in page.text
    assert "Удаление денежных операций изменит реальные остатки" in page.text
    token = re.search(r'name="deletion_token" value="([^"]+)"', page.text)
    assert token is not None
    denied = client.post(
        "/bank/delete/confirm",
        data={"csrf": csrf, "deletion_token": token[1], "confirmation": "yes"},
    )
    assert denied.status_code == 422
    assert not backup_directory.exists()
    with SQLiteCashRepository(path) as repo:
        assert repo.balances_on(date(2028, 3, 3))["bank"] == money("140")
    done = client.post(
        "/bank/delete/confirm?as_of=2028-03-03",
        data={"csrf": csrf, "deletion_token": token[1], "confirmation": "УДАЛИТЬ"},
        follow_redirects=False,
    )
    assert done.status_code == 303
    assert done.headers["location"].endswith("#payments")
    assert list(backup_directory.glob("*.sqlite3"))
    with SQLiteCashRepository(path) as repo:
        assert repo.list_receipts("coupon") == ()
        assert repo.balances_on(date(2028, 3, 3))["bank"] == money("100")
    assert (
        client.post(
            "/bank/delete/confirm",
            data={"csrf": csrf, "deletion_token": token[1], "confirmation": "УДАЛИТЬ"},
        ).status_code
        == 422
    )


def test_web_shows_import_management_and_deletes_all_group_records(browser):
    client, path, _ = browser
    csrf = client.app.state.csrf_token
    with SQLiteCashRepository(path) as repo:
        add_import(repo, "movement", "movement", 5, date(2028, 2, 1))
        add_import(repo, "closing", "closing", 10, date(2028, 3, 1))
    html = client.get("/bank/forms?as_of=2028-03-03").text
    assert "Удалить позицию" in html
    assert "Удалить запись" in html
    assert 'id="import-records"' in html
    serialized = re.search(
        r'name="kind" value="import_position".*?name="identifier" value="([^"]+)"',
        html,
        re.DOTALL,
    )
    assert serialized is not None
    assert json.loads(unescape(serialized[1])) == [
        "Fictional Custodian",
        "UA9876543210",
    ]
    identifier = json.dumps(["Fictional Custodian", "UA9876543210"])
    response = client.post(
        "/bank/delete/preview?as_of=2028-03-03",
        data={"csrf": csrf, "kind": "import_position", "identifier": identifier},
    )
    assert response.status_code == 200
    token = re.search(r'name="deletion_token" value="([^"]+)"', response.text)
    assert token is not None
    assert (
        client.post(
            "/bank/delete/confirm?as_of=2028-03-03",
            data={"csrf": csrf, "deletion_token": token[1], "confirmation": "УДАЛИТЬ"},
            follow_redirects=False,
        ).status_code
        == 303
    )
    with SQLiteCashRepository(path) as repo:
        assert repo.list_securities_entries() == ()
        assert repo.get_bond_lot("lot") is not None


def test_preview_account_with_dependencies_explains_blocker_without_backup(browser):
    client, path, backup = browser
    csrf = client.app.state.csrf_token
    result = client.post(
        "/bank/delete/preview",
        data={"csrf": csrf, "kind": "account", "identifier": "bank"},
    )
    assert result.status_code == 422
    assert "Удали связанные операции" in result.text
    assert not backup.exists()
    with SQLiteCashRepository(path) as repo:
        assert len(repo.list_accounts()) == 1
