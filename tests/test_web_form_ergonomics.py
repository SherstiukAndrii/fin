"""Bank form ergonomics: selectable banks, exact grouped numbers and sensible dates."""

import re
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from fin.domain.accounts import AccountKind, CashAccount
from fin.persistence import SQLiteCashRepository
from fin.web import create_app


@pytest.fixture
def browser(tmp_path):
    path = tmp_path / "fin.sqlite3"
    with SQLiteCashRepository(path) as repo:
        repo.add_account(CashAccount("mono", "mono", "UAH", AccountKind.BANK))
        repo.add_account(CashAccount("ukrsib", "UkrSib", "USD", AccountKind.BANK))
        repo.add_account(CashAccount("wallet", "Pocket", "UAH", AccountKind.CASH))
    with TestClient(create_app(path), base_url="http://localhost") as client:
        yield client, path, client.app.state.csrf_token


def send(browser, route: str, **data):
    client, _, csrf = browser
    return client.post(
        f"/bank/{route}",
        data={"csrf": csrf, **data},
        follow_redirects=False,
    )


def deposit(bank="mono", **overrides):
    fields = {
        "bank": bank,
        "currency": "UAH",
        "principal": "1 000 000.50",
        "opened_on": "2026-11-20",
        "matures_on": "2027-11-20",
        "annual_rate": "0.15",
    }
    fields.update(overrides)
    return fields


def test_bank_dropdown_uses_existing_bank_accounts_but_not_cash_accounts(browser):
    client, _, _ = browser
    page = client.get("/bank")
    assert page.status_code == 200
    match = re.search(r'<form method="post" action="/bank/deposit">(.*?)</form>', page.text, re.S)
    assert match is not None
    content = match.group(1)
    assert '<select name="bank"' in content
    assert '<option value="mono"' in content
    assert '<option value="UkrSib"' in content
    assert '<option value="Pocket"' not in content
    assert '<option value="__other__"' in content
    assert 'name="bank_other"' in content


def test_selecting_existing_bank_and_grouped_amount_preserves_exact_decimal(browser):
    _, path, _ = browser
    assert send(browser, "deposit", **deposit()).status_code == 303
    with SQLiteCashRepository(path) as repo:
        saved = repo.list_deposits()[0]
        assert saved.bank == "mono"
        assert saved.principal.amount == Decimal("1000000.50")
        assert saved.annual_rate == Decimal("0.15")


def test_select_other_bank_uses_entered_name_and_works_without_bank_account(browser):
    client, path, _ = browser
    assert (
        send(
            browser,
            "deposit",
            **deposit(bank="__other__", bank_other="Savings cooperative", principal="12 500,25"),
        ).status_code
        == 303
    )
    with SQLiteCashRepository(path) as repo:
        saved = repo.list_deposits()[0]
        assert saved.bank == "Savings cooperative"
        assert saved.principal.amount == Decimal("12500.25")
    page = client.get("/bank")
    assert '<option value="Savings cooperative"' in page.text


def test_missing_other_bank_name_is_validation_error_and_keeps_amount(browser):
    _, path, _ = browser
    response = send(
        browser,
        "deposit",
        **deposit(bank="__other__", bank_other="", principal="1 234 567.89"),
    )
    assert response.status_code == 422
    assert "Required field: bank_other" in response.text
    assert 'value="1 234 567.89"' in response.text
    assert 'value="__other__" selected' in response.text
    with SQLiteCashRepository(path) as repo:
        assert repo.list_deposits() == ()


def test_existing_deposit_bank_is_selected_for_edit(browser):
    client, _, _ = browser
    assert send(browser, "deposit", **deposit()).status_code == 303
    page = client.get("/bank")
    match = re.search(
        r'<form method="post" action="/bank/edit_deposit">(.*?)</form>', page.text, re.S
    )
    assert match is not None
    assert 'value="mono" selected' in match.group(1)


def test_grouped_bond_quantity_and_price_are_parsed_without_loss(browser):
    _, path, _ = browser
    response = send(
        browser,
        "bond",
        isin="UA1234567890",
        currency="UAH",
        face_value="1 000",
        quantity="1 200",
        acquired_on="2026-11-20",
        matures_on="2028-11-20",
        unit_price="1 234.5678",
        paid_fees="10 000.02",
    )
    assert response.status_code == 303
    with SQLiteCashRepository(path) as repo:
        lot = repo.list_bond_lots()[0]
        assert lot.quantity == 1200
        assert lot.unit_price.amount == Decimal("1234.5678")
        assert lot.paid_fees.amount == Decimal("10000.02")
        assert lot.cost_basis.amount == Decimal("1491481.3800")


def test_new_forms_default_to_today_but_maturities_remain_blank(browser):
    client, _, _ = browser
    page = client.get("/bank").text
    today = date.today().isoformat()
    for name in ("as_of", "acquired_on", "opened_on"):
        assert re.search(rf'<input[^>]*name="{name}"[^>]*value="{today}"', page)
    for name in ("matures_on", "due_on"):
        if name == "matures_on":
            assert re.search(r'<input[^>]*name="matures_on"[^>]*value=""', page)
        else:
            assert 'name="due_on" value=""' in page or 'name="due_on"' not in page
    assert (
        send(
            browser,
            "opening",
            as_of="2026-11-20",
            balance_mono="2 500 000",
            balance_ukrsib="0",
            balance_wallet="0",
        ).status_code
        == 303
    )
    page = client.get("/bank").text
    assert re.search(rf'<input[^>]*name="effective_on"[^>]*value="{today}"', page)


def test_grouping_marker_covers_monetary_inputs_without_turning_dates_into_text(browser):
    client, _, _ = browser
    html = client.get("/bank").text
    assert re.search(r'<input[^>]*name="principal"[^>]*data-grouped', html)
    assert re.search(r'<input[^>]*name="quantity"[^>]*data-grouped', html)
    assert re.search(r'<input[^>]*name="annual_rate"[^>]*data-grouped', html)
    date_field = re.search(r'<input[^>]*name="acquired_on"[^>]*>', html)
    assert date_field is not None
    assert 'type="date"' in date_field.group()
    assert "data-grouped" not in date_field.group()
    script = client.get("/assets/bank.js")
    assert script.status_code == 200
    assert "function groupDigits(value)" in script.text
    assert r"/^(-?)(\d+)([.,]\d*)?$/" in script.text
    assert r"/\B(?=(\d{3})+(?!\d))/" in script.text
    assert r"/\s/g" in script.text
    assert r"/\\s/g" not in script.text
    assert "prepareForms(next)" in script.text
