"""Monthly end-of-day account observations and explicit, auditable corrections."""

import sqlite3
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
from fin.domain.money import CurrencyMismatchError, Money
from fin.persistence import SQLiteCashRepository
from fin.persistence.sqlite_bank import _SCHEMA, _SCHEMA_V2, _SCHEMA_V3
from fin.web import create_app


def money(value: str, currency: str = "UAH") -> Money:
    return Money(Decimal(value), currency)


@pytest.fixture
def store(tmp_path):
    with SQLiteCashRepository(tmp_path / "fin.sqlite3") as repo:
        repo.add_account(CashAccount("bank", "Test bank", "UAH", AccountKind.BANK))
        repo.add_account(CashAccount("wallet", "Test cash", "USD", AccountKind.CASH))
        repo.save_opening_snapshot(
            OpeningSnapshot(
                date(2027, 1, 1),
                (
                    OpeningBalance("bank", money("100")),
                    OpeningBalance("wallet", money("20", "USD")),
                ),
            )
        )
        yield repo


def observe(store, bank="110", wallet="20", day=date(2027, 1, 31)):
    return store.record_monthly_control(
        "control-jan",
        day,
        {"bank": money(bank), "wallet": money(wallet, "USD")},
        note="Manual month-end observation",
    )


def test_record_is_read_only_and_keeps_every_currency_and_original_calculation(store):
    stored = observe(store)
    assert stored.as_of == date(2027, 1, 31)
    assert len(stored.balances) == 2
    assert stored.balances[0].recorded_difference == money("10")
    assert stored.balances[1].recorded_difference == money("0", "USD")
    assert store.balances_on(date(2027, 1, 31))["bank"] == money("100")
    assert store.list_cash_events() == ()
    assert store.list_monthly_controls() == (stored,)


def test_reconciliation_is_explicit_and_applied_once_from_month_end(store):
    observe(store, bank="90")
    event = store.confirm_monthly_correction(
        "control-jan",
        "bank",
        expected_difference=money("-10"),
        event_id="correction1",
        reason="Missing bank fee confirmed against statement",
    )
    assert event.kind == CashEventKind.CORRECTION
    assert event.legs[0].change == money("-10")
    assert store.balances_on(date(2027, 1, 30))["bank"] == money("100")
    assert store.balances_on(date(2027, 1, 31))["bank"] == money("90")
    assert store.balances_on(date(2027, 1, 31))["wallet"] == money("20", "USD")
    saved = store.list_monthly_controls()[0]
    assert saved.balances[0].recorded_difference == money("-10")
    assert saved.balances[0].correction_event_id == event.id
    with pytest.raises(ValueError, match="already corrected"):
        store.confirm_monthly_correction(
            "control-jan",
            "bank",
            expected_difference=money("-10"),
            event_id="correction2",
            reason="Duplicate",
        )
    assert len(store.list_cash_events()) == 1


def test_stale_confirmation_rejected_then_recalculated(store):
    observe(store)
    store.add_cash_event(
        CashEvent(
            "late-entry",
            date(2027, 1, 12),
            CashEventKind.INFLOW,
            (CashLeg("bank", money("5")),),
        )
    )
    with pytest.raises(ValueError, match="Balance changed"):
        store.confirm_monthly_correction(
            "control-jan",
            "bank",
            expected_difference=money("10"),
            event_id="wrong",
            reason="Bank difference",
        )
    assert len(store.list_cash_events()) == 1
    store.confirm_monthly_correction(
        "control-jan",
        "bank",
        expected_difference=money("5"),
        event_id="correct",
        reason="Bank difference verified",
    )
    assert store.balances_on(date(2027, 1, 31))["bank"] == money("110")
    assert store.list_monthly_controls()[0].balances[0].calculated_at_capture == money("100")


def test_reconciliation_guarded_and_immutable(store):
    observe(store)
    with pytest.raises(ValueError, match="Explain"):
        store.confirm_monthly_correction(
            "control-jan",
            "bank",
            expected_difference=money("10"),
            event_id="empty",
            reason="",
        )
    with pytest.raises(ValueError, match="No difference"):
        store.confirm_monthly_correction(
            "control-jan",
            "wallet",
            expected_difference=money("0", "USD"),
            event_id="zero",
            reason="No issue",
        )
    event = store.confirm_monthly_correction(
        "control-jan",
        "bank",
        expected_difference=money("10"),
        event_id="confirmed",
        reason="Previously unrecorded cash",
    )
    with pytest.raises(ValueError, match="immutable"):
        store.update_cash_event(event)
    with pytest.raises(ValueError, match="cannot change"):
        store.update_opening_snapshot(store.get_opening_snapshot())
    assert len(store.list_cash_events()) == 1


def test_incomplete_invalid_or_duplicate_observations_are_atomic(store):
    with pytest.raises(ValueError, match="last calendar day"):
        observe(store, day=date(2027, 1, 30))
    with pytest.raises(ValueError, match="every account"):
        store.record_monthly_control("partial", date(2027, 1, 31), {"bank": money("5")})
    with pytest.raises(CurrencyMismatchError):
        store.record_monthly_control(
            "wrong",
            date(2027, 1, 31),
            {"bank": money("5", "USD"), "wallet": money("2", "USD")},
        )
    assert store.list_monthly_controls() == ()
    observe(store)
    with pytest.raises(sqlite3.IntegrityError):
        store.record_monthly_control(
            "other-id",
            date(2027, 1, 31),
            {"bank": money("1"), "wallet": money("3", "USD")},
        )
    assert len(store.list_monthly_controls()) == 1


def test_existing_v3_database_migrates_without_rewriting_balances(tmp_path):
    path = tmp_path / "v3.sqlite3"
    with sqlite3.connect(path) as db:
        for sql in (*_SCHEMA, *_SCHEMA_V2, *_SCHEMA_V3):
            db.execute(sql)
        db.execute("PRAGMA user_version = 3")
        db.execute(
            "INSERT INTO cash_accounts (id, name, currency, kind) VALUES ('a', 'Old', 'UAH', 'bank')"
        )
        db.execute("INSERT INTO opening_snapshot (id, as_of) VALUES (1, '2027-01-01')")
        db.execute(
            """INSERT INTO opening_balances (account_id, amount, currency)
               VALUES ('a', '123.456789', 'UAH')"""
        )
    with SQLiteCashRepository(path) as repo:
        assert repo.schema_version() == 4
        assert repo.balances_on(date(2027, 1, 31))["a"] == money("123.456789")
        assert repo.list_monthly_controls() == ()
    with SQLiteCashRepository(path) as repo:
        assert repo.schema_version() == 4
        assert repo.balances_on(date(2027, 1, 31))["a"] == money("123.456789")
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.fixture
def browser(tmp_path):
    path = tmp_path / "web.sqlite3"
    with SQLiteCashRepository(path):
        pass
    with TestClient(create_app(path), base_url="http://localhost") as client:
        yield client, path, client.app.state.csrf_token


def send(browser, route, **fields):
    client, _, token = browser
    return client.post(
        f"/bank/{route}?as_of=2027-01-31",
        data={"csrf": token, **fields},
        follow_redirects=False,
    )


def test_web_control_snapshot_and_separate_confirmation(browser):
    client, path, _ = browser
    assert (
        send(browser, "account", id="bank", name="Sample", currency="UAH", kind="bank").status_code
        == 303
    )
    assert send(browser, "opening", as_of="2027-01-01", balance_bank="100").status_code == 303
    page = client.get("/bank?as_of=2027-01-31")
    assert page.status_code == 200
    assert "Ежемесячная сверка остатков" in page.text
    assert 'name="as_of" value="2027-01-31"' in page.text
    assert (
        send(
            browser,
            "control",
            as_of="2027-01-31",
            balance_bank="112.50",
            note="Verified",
        ).status_code
        == 303
    )
    with SQLiteCashRepository(path) as repo:
        snapshots = repo.list_monthly_controls()
        assert len(snapshots) == 1
        assert len(repo.list_cash_events()) == 0
        assert repo.balances_on(date(2027, 1, 31))["bank"] == money("100")
    html = client.get("/bank?as_of=2027-01-31").text
    assert "Подтвердить корректировку" in html
    assert 'name="expected_difference" value="12.50"' in html
    assert (
        send(
            browser,
            "confirm_control",
            control_id=snapshots[0].id,
            account_id="bank",
            expected_difference="12.50",
            reason="Manually verified difference",
        ).status_code
        == 303
    )
    with SQLiteCashRepository(path) as repo:
        assert repo.balances_on(date(2027, 1, 31))["bank"] == money("112.50")
        assert len(repo.list_cash_events()) == 1
        assert repo.list_monthly_controls()[0].balances[0].correction_event_id is not None
    html = client.get("/bank?as_of=2027-01-31").text
    assert "Корректировка подтверждена" in html
    assert (
        send(
            browser,
            "confirm_control",
            control_id=snapshots[0].id,
            account_id="bank",
            expected_difference="12.50",
            reason="Duplicate",
        ).status_code
        == 422
    )


def test_no_opening_does_not_offer_monthly_capture(browser):
    page = browser[0].get("/bank")
    assert page.status_code == 200
    assert "Сначала укажи счета и начальные остатки" in page.text
    assert 'action="/bank/control?' not in page.text
