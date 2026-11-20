"""Submitted Bank forms keep user input and show validation errors in place."""

import re
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from fin.persistence import SQLiteCashRepository
from fin.web import create_app


@pytest.fixture
def browser(tmp_path):
    path = tmp_path / "finance.sqlite3"
    with SQLiteCashRepository(path):
        pass
    with TestClient(create_app(path), base_url="http://localhost") as client:
        yield client, path, client.app.state.csrf_token


def bond_fields(**changes):
    values = {
        "id": "lot_new",
        "isin": "UA1234567890",
        "currency": "UAH",
        "face_value": "1000.00",
        "quantity": "200",
        "acquired_on": "2026-11-17",
        "matures_on": "2028-11-17",
        "unit_price": "975.35",
        "paid_fees": "0.00",
    }
    values.update(changes)
    return values


def send(browser, kind, values):
    client, _, token = browser
    return client.post(f"/bank/{kind}", data={"csrf": token, **values}, follow_redirects=False)


def input_value(response, name, value):
    expected = re.escape(value)
    assert re.search(
        rf'<input\b[^>]*\bname="{re.escape(name)}"[^>]*\bvalue="{expected}"',
        response.text,
    ), f"Lost value for {name}"


def test_invalid_bond_isin_preserves_every_field_and_reopens_form(browser):
    client, path, _ = browser
    entered = bond_fields(isin="not-an-isin")
    response = send(browser, "bond", entered)
    assert response.status_code == 422
    assert "Запись не сохранена" in response.text
    assert "ISIN" in response.text
    assert re.search(r'<details id="form-bond"[^>]*\bopen\b', response.text)
    for name, value in entered.items():
        if name == "id":
            continue
        input_value(response, name, value)
    assert "<h1>Entry rejected</h1>" not in response.text
    with SQLiteCashRepository(path) as db:
        assert db.get_bond_lot("lot_new") is None


def test_duplicate_lot_keeps_second_attempt_without_overwriting_first(browser):
    _, path, _ = browser
    assert send(browser, "bond", bond_fields()).status_code == 303
    second = bond_fields(unit_price="999.123", paid_fees="7.50")
    response = send(browser, "bond", second)
    assert response.status_code == 422
    assert "UNIQUE" in response.text
    for field in ("isin", "quantity", "unit_price", "paid_fees"):
        input_value(response, field, second[field])
    with SQLiteCashRepository(path) as db:
        original = db.get_bond_lot("lot_new")
        assert original.quantity == 200
        assert original.unit_price.amount == Decimal("975.35")
        assert original.paid_fees.amount == Decimal("0.00")


def test_bond_quantity_error_preserves_values_and_explains_problem(browser):
    _, path, _ = browser
    response = send(browser, "bond", bond_fields(quantity="two hundred"))
    assert response.status_code == 422
    assert "Количество должно быть целым числом" in response.text
    input_value(response, "quantity", "two hundred")
    input_value(response, "isin", "UA1234567890")
    with SQLiteCashRepository(path) as db:
        assert db.list_bond_lots() == ()


def test_bond_invalid_date_does_not_clear_form(browser):
    _, path, _ = browser
    response = send(browser, "bond", bond_fields(matures_on="2025-11-17"))
    assert response.status_code == 422
    assert "maturity" in response.text.lower()
    input_value(response, "acquired_on", "2026-11-17")
    input_value(response, "matures_on", "2025-11-17")
    with SQLiteCashRepository(path) as db:
        assert db.list_bond_lots() == ()


def test_escaped_failed_bond_value_is_safe_in_html(browser):
    _, path, _ = browser
    entered = bond_fields(id="lot_bad", isin='"><script>alert(1)</script>')
    response = send(browser, "bond", entered)
    assert response.status_code == 422
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    with SQLiteCashRepository(path) as db:
        assert db.list_bond_lots() == ()


def test_invalid_deposit_restores_entered_data_and_opens_deposit_form(browser):
    _, path, _ = browser
    fields = {
        "id": "dep_1",
        "bank": "Test bank",
        "currency": "UAH",
        "principal": "5000.00",
        "opened_on": "2028-01-15",
        "matures_on": "2027-01-15",
        "annual_rate": "0.12",
    }
    response = send(browser, "deposit", fields)
    assert response.status_code == 422
    assert re.search(r'<details id="form-deposit"[^>]*\bopen\b', response.text)
    for name, value in fields.items():
        if name == "id":
            continue
        if name == "bank":
            assert '<option value="Test bank" selected>' in response.text
        else:
            input_value(response, name, value)
    with SQLiteCashRepository(path) as db:
        assert db.list_deposits() == ()


def test_success_redirects_and_stores_the_lot(browser):
    _, path, _ = browser
    response = send(browser, "bond", bond_fields())
    assert response.status_code == 303
    assert response.headers["location"] == "/bank"
    with SQLiteCashRepository(path) as db:
        lot = db.get_bond_lot("lot_new")
        assert lot.acquired_on == date(2026, 11, 17)
        assert lot.quantity == 200
        assert lot.cost_basis.amount == Decimal("195070.00")


def test_bad_csrf_never_reflects_submitted_financial_data(browser):
    client, path, _ = browser
    response = client.post(
        "/bank/bond", data={"csrf": "invalid", **bond_fields(unit_price="111111.999")}
    )
    assert response.status_code == 403
    assert "111111.999" not in response.text
    with SQLiteCashRepository(path) as db:
        assert db.list_bond_lots() == ()
