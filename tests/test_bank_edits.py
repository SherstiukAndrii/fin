"""Editing existing Bank records keeps integrity and takes a backup first."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from fin.persistence import SQLiteCashRepository
from fin.web import create_app


@pytest.fixture
def edit_browser(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "private"))
    db_path = tmp_path / "fin.sqlite3"
    with SQLiteCashRepository(db_path):
        pass
    with TestClient(create_app(db_path), base_url="http://localhost") as client:
        yield client, db_path, client.app.state.csrf_token, tmp_path / "private"


def post(browser, route, **values):
    client, _, token, _ = browser
    return client.post(
        f"/bank/{route}",
        data={"csrf": token, **values},
        follow_redirects=False,
    )


def sample_bond(browser):
    return post(
        browser,
        "bond",
        isin="UA1234567890",
        currency="UAH",
        face_value="1000",
        quantity="2",
        acquired_on="2027-01-01",
        matures_on="2028-12-01",
        unit_price="975.12",
        paid_fees="1.00",
    )


def test_bond_edits_auto_backup_and_roundtrip(edit_browser):
    _, path, _, user_dir = edit_browser
    assert sample_bond(edit_browser).status_code == 303
    with SQLiteCashRepository(path) as db:
        lot = db.list_bond_lots()[0]
    response = post(
        edit_browser,
        "edit_bond",
        id=lot.id,
        currency="UAH",
        acquired_on="2027-01-02",
        quantity="3",
        unit_price="999.12",
        paid_fees="7.50",
    )
    assert response.status_code == 303
    with SQLiteCashRepository(path) as db:
        updated = db.get_bond_lot(lot.id)
        assert updated.quantity == 3
        assert updated.unit_price.amount == Decimal("999.12")
        assert updated.paid_fees.amount == Decimal("7.50")
    backup_dir = user_dir / "fin" / "backups"
    assert any(backup_dir.glob("*.sqlite3"))


def test_bond_quantity_locked_after_payment(edit_browser):
    _, path, _, _ = edit_browser
    assert sample_bond(edit_browser).status_code == 303
    with SQLiteCashRepository(path) as db:
        lot = db.list_bond_lots()[0]
    assert (
        post(
            edit_browser,
            "payment",
            holding_id=lot.id,
            kind="coupon",
            due_on="2027-06-01",
            expected="80",
        ).status_code
        == 303
    )
    response = post(
        edit_browser,
        "edit_bond",
        id=lot.id,
        currency="UAH",
        acquired_on="2027-01-01",
        quantity="3",
        unit_price="975",
        paid_fees="0",
    )
    assert response.status_code == 422
    assert "Quantity cannot change" in response.text
    with SQLiteCashRepository(path) as db:
        assert db.get_bond_lot(lot.id).quantity == 2


def test_payment_edits_keep_receipts_when_fact_exceeds_new_plan(edit_browser):
    _, path, _, _ = edit_browser
    assert sample_bond(edit_browser).status_code == 303
    with SQLiteCashRepository(path) as db:
        lot = db.list_bond_lots()[0]
    assert (
        post(
            edit_browser,
            "payment",
            holding_id=lot.id,
            kind="coupon",
            due_on="2027-06-01",
            expected="80",
        ).status_code
        == 303
    )
    with SQLiteCashRepository(path) as db:
        payment = db.list_payments(lot.id)[0]
    assert (
        post(
            edit_browser,
            "receipt",
            payment_id=payment.id,
            received_on="2027-06-02",
            amount="50",
        ).status_code
        == 303
    )
    changed = post(
        edit_browser,
        "edit_payment",
        id=payment.id,
        holding_id=lot.id,
        kind="coupon",
        due_on="2027-06-01",
        expected="40",
    )
    assert changed.status_code == 303
    with SQLiteCashRepository(path) as db:
        assert db.get_payment(payment.id).expected.amount == Decimal("40")
        assert db.list_receipts(payment.id)[0].amount.amount == Decimal("50")


def test_opening_account_cash_event_are_editable(edit_browser):
    _, path, _, _ = edit_browser
    assert (
        post(edit_browser, "account", name="Bank", kind="bank", currency="UAH").status_code == 303
    )
    with SQLiteCashRepository(path) as db:
        aid = db.list_accounts()[0].id
    assert (
        post(edit_browser, "opening", as_of="2027-01-01", **{f"balance_{aid}": "100"}).status_code
        == 303
    )
    assert (
        post(
            edit_browser,
            "edit_account",
            id=aid,
            currency="UAH",
            name="Updated bank",
            kind="bank",
        ).status_code
        == 303
    )
    assert (
        post(
            edit_browser,
            "event",
            effective_on="2027-01-02",
            kind="inflow",
            account_id=aid,
            amount="50",
            note="",
        ).status_code
        == 303
    )
    with SQLiteCashRepository(path) as db:
        eid = db.list_cash_events()[0].id
    assert (
        post(
            edit_browser,
            "edit_event",
            id=eid,
            effective_on="2027-01-03",
            kind="outflow",
            account_id=aid,
            amount="20",
            note="",
        ).status_code
        == 303
    )
    assert (
        post(
            edit_browser,
            "edit_opening",
            as_of="2027-01-01",
            **{f"balance_{aid}": "200"},
        ).status_code
        == 303
    )
    with SQLiteCashRepository(path) as db:
        assert db.get_account(aid).name == "Updated bank"
        assert db.balances_on(date(2027, 1, 4))[aid].amount == Decimal("180")
