"""End-to-end local Bank browser tests with temporary, fictional SQLite data."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from fin.persistence import SQLiteCashRepository
from fin.web import create_app


@pytest.fixture
def bank_ui(tmp_path):
    path = tmp_path / "local-fin.sqlite3"
    with SQLiteCashRepository(path):
        pass
    with TestClient(create_app(path), base_url="http://localhost") as client:
        yield client, path, client.app.state.csrf_token


def post(bank_ui, route: str, **data):
    client, _, csrf = bank_ui
    return client.post(f"/bank/{route}", data={"csrf": csrf, **data}, follow_redirects=False)


def add_account(bank_ui, account_id="bank", currency="UAH", name="Test bank"):
    return post(
        bank_ui,
        "account",
        id=account_id,
        name=name,
        kind="bank",
        currency=currency,
    )


def add_opening(bank_ui, **amounts):
    return post(bank_ui, "opening", as_of="2027-01-01", **amounts)


def test_missing_database_screen_does_not_create_file(tmp_path):
    path = tmp_path / "missing" / "fin.db"
    with TestClient(create_app(path), base_url="http://localhost") as client:
        page = client.get("/bank")
        assert page.status_code == 200
        assert "Fin: DB Init" in page.text
        assert not path.exists()
        assert not path.parent.exists()
        denied = client.post("/bank/account", data={"csrf": client.app.state.csrf_token})
        assert denied.status_code == 409
        assert not path.exists()


def test_read_only_page_and_security_headers(bank_ui):
    client, path, token = bank_ui
    page = client.get("/bank")
    assert page.status_code == 200
    assert f'name="csrf" value="{token}"' in page.text
    assert "no-store" == page.headers["cache-control"]
    assert "DENY" == page.headers["x-frame-options"]
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert path.is_file()
    assert client.get("/", follow_redirects=False).status_code in (302, 307)
    assert client.get("/bank?as_of=not-a-date").status_code == 422
    assert client.get("/bank", headers={"host": "example.org"}).status_code == 400


def test_csrf_rejects_missing_or_wrong_token_without_writes(bank_ui):
    client, path, _ = bank_ui
    assert client.post("/bank/account", data={"id": "a"}).status_code == 403
    assert client.post("/bank/account", data={"csrf": "wrong", "id": "a"}).status_code == 403
    with SQLiteCashRepository(path) as repo:
        assert repo.list_accounts() == ()


def test_accounts_and_opening_can_be_entered_only_in_order(bank_ui):
    client, path, _ = bank_ui
    assert add_opening(bank_ui, balance_bank="100").status_code == 422
    assert add_account(bank_ui).status_code == 303
    assert add_account(bank_ui, account_id="wallet", name="Cash").status_code == 303
    assert add_opening(bank_ui, balance_bank="100.000", balance_wallet="25.10").status_code == 303
    with SQLiteCashRepository(path) as repo:
        assert repo.get_opening_snapshot().as_of == date(2027, 1, 1)
        assert repo.balances_on(date(2027, 1, 1))["bank"].amount == Decimal("100.000")
    assert "100.000" in client.get("/bank?as_of=2027-01-01").text
    assert add_account(bank_ui, account_id="later").status_code == 422
    assert add_opening(bank_ui, balance_bank="100", balance_wallet="25").status_code == 422


def test_missing_opening_balance_cannot_be_invented(bank_ui):
    _, path, _ = bank_ui
    assert add_account(bank_ui).status_code == 303
    assert add_account(bank_ui, account_id="wallet").status_code == 303
    assert add_opening(bank_ui, balance_bank="0").status_code == 422
    with SQLiteCashRepository(path) as repo:
        assert repo.get_opening_snapshot() is None


def test_cash_movements_are_dated_and_own_transfer_balanced(bank_ui):
    _, path, _ = bank_ui
    assert add_account(bank_ui).status_code == 303
    assert add_account(bank_ui, account_id="wallet").status_code == 303
    assert add_opening(bank_ui, balance_bank="1000", balance_wallet="100").status_code == 303
    assert (
        post(
            bank_ui,
            "event",
            kind="transfer",
            account_id="bank",
            to_account_id="wallet",
            amount="300",
            effective_on="2027-01-04",
            note="",
        ).status_code
        == 303
    )
    assert (
        post(
            bank_ui,
            "event",
            kind="outflow",
            account_id="wallet",
            amount="25",
            effective_on="2027-01-05",
            note="Test groceries",
        ).status_code
        == 303
    )
    with SQLiteCashRepository(path) as repo:
        assert repo.balances_on(date(2027, 1, 3))["bank"].amount == Decimal("1000")
        balances = repo.balances_on(date(2027, 1, 5))
        assert balances["bank"].amount == Decimal("700")
        assert balances["wallet"].amount == Decimal("375")
        assert sum(item.amount for item in balances.values()) == Decimal("1075")


def test_deposit_and_bond_forms_do_not_automatically_book_cash(bank_ui):
    client, path, _ = bank_ui
    assert add_account(bank_ui).status_code == 303
    assert add_opening(bank_ui, balance_bank="10000").status_code == 303
    assert (
        post(
            bank_ui,
            "bond",
            id="bond1",
            isin="UA1234567890",
            currency="UAH",
            face_value="1000",
            quantity="2",
            acquired_on="2027-01-02",
            matures_on="2028-12-01",
            unit_price="",
            paid_fees="0.00",
        ).status_code
        == 303
    )
    assert (
        post(
            bank_ui,
            "deposit",
            id="dep1",
            bank="Example bank",
            currency="UAH",
            principal="5000",
            opened_on="2027-01-02",
            matures_on="2027-12-31",
            annual_rate="0.15",
        ).status_code
        == 303
    )
    with SQLiteCashRepository(path) as repo:
        assert repo.get_bond_lot("bond1").cost_basis is None
        assert repo.get_deposit("dep1").annual_rate == Decimal("0.15")
        assert repo.balances_on(date(2027, 3, 1))["bank"].amount == Decimal("10000")
    page = client.get("/bank")
    assert "bond1" in page.text and "dep1" in page.text


def test_coupon_receipt_requires_separate_cash_credit_and_cannot_repeat(bank_ui):
    client, path, _ = bank_ui
    assert add_account(bank_ui).status_code == 303
    assert add_opening(bank_ui, balance_bank="100").status_code == 303
    assert (
        post(
            bank_ui,
            "bond",
            id="bond1",
            isin="UA1234567890",
            currency="UAH",
            face_value="1000",
            quantity="2",
            acquired_on="2027-01-02",
            matures_on="2028-12-01",
            unit_price="990",
            paid_fees="0",
        ).status_code
        == 303
    )
    assert (
        post(
            bank_ui,
            "payment",
            id="march",
            holding_id="bond1",
            kind="coupon",
            due_on="2027-03-01",
            expected="80",
        ).status_code
        == 303
    )
    with SQLiteCashRepository(path) as repo:
        assert repo.balances_on(date(2027, 3, 4))["bank"].amount == Decimal("100")
    assert (
        post(
            bank_ui,
            "receipt",
            payment_id="march",
            amount="50",
            received_on="2027-03-03",
        ).status_code
        == 303
    )
    with SQLiteCashRepository(path) as repo:
        receipts = repo.list_receipts("march")
        assert len(receipts) == 1
        assert repo.balances_on(date(2027, 3, 4))["bank"].amount == Decimal("100")
    assert (
        post(
            bank_ui,
            "credit",
            payment_id="march",
            receipt_id=receipts[0].id,
            account_id="bank",
        ).status_code
        == 303
    )
    assert (
        post(
            bank_ui,
            "credit",
            payment_id="march",
            receipt_id=receipts[0].id,
            account_id="bank",
        ).status_code
        == 422
    )
    with SQLiteCashRepository(path) as repo:
        assert repo.balances_on(date(2027, 3, 4))["bank"].amount == Decimal("150")
        assert len(repo.list_cash_events()) == 1
    assert "Зачислено на счёт" in client.get("/bank?as_of=2027-03-04").text


def test_errors_escaped_and_invalid_amount_does_not_persist(bank_ui):
    client, path, _ = bank_ui
    response = post(
        bank_ui,
        "account",
        id="bad!",
        name="<img src=x onerror=alert(1)>",
        currency="UAH",
        kind="bank",
    )
    assert response.status_code == 422
    assert "<img" not in response.text
    assert add_account(bank_ui).status_code == 303
    assert add_opening(bank_ui, balance_bank="NaN").status_code == 422
    assert (
        post(
            bank_ui,
            "event",
            kind="inflow",
            account_id="bank",
            effective_on="2027-01-05",
            amount="123",
            note="",
        ).status_code
        == 422
    )
    with SQLiteCashRepository(path) as repo:
        assert repo.get_opening_snapshot() is None


def test_user_supplied_account_name_is_html_escaped(bank_ui):
    client, _, _ = bank_ui
    assert (
        post(
            bank_ui,
            "account",
            id="user",
            name="<script>alert(1)</script>",
            currency="UAH",
            kind="bank",
        ).status_code
        == 303
    )
    page = client.get("/bank")
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;" in page.text


def test_fin_web_command_requires_existing_db_and_launches_loopback(tmp_path, monkeypatch, capsys):
    from fin.cli import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "private"))
    assert main(["web"]) == 1
    assert "Run 'fin db init'" in capsys.readouterr().out
    assert not (tmp_path / "data").exists()

    path = tmp_path / "fin.sqlite3"
    with SQLiteCashRepository(path):
        pass

    calls = []

    def fake_runner(db_path, *, port):
        calls.append((db_path, port))

    monkeypatch.setattr("fin.web.server.run_local", fake_runner)
    assert main(["web", "--path", str(path), "--port", "8766"]) == 0
    assert calls == [(path, 8766)]


def test_web_runner_binds_only_to_loopback(monkeypatch, tmp_path):
    from fin.web.server import run_local

    calls = []

    def fake_uvicorn(app, *, host, port, access_log):
        calls.append((app, host, port, access_log))

    monkeypatch.setattr("uvicorn.run", fake_uvicorn)
    run_local(tmp_path / "fin.sqlite3", port=8766)
    assert len(calls) == 1
    assert calls[0][1:] == ("127.0.0.1", 8766, False)


def test_generated_ids_without_manual_input(bank_ui):
    from fin.domain.accounts import AccountKind

    client, path, token = bank_ui
    result = client.post(
        "/bank/account",
        data={"csrf": token, "name": "Wallet", "kind": "cash", "currency": "UAH"},
        follow_redirects=False,
    )
    assert result.status_code == 303
    with SQLiteCashRepository(path) as repo:
        account = repo.list_accounts()[0]
        assert account.id.startswith("account_")
        assert account.kind == AccountKind.CASH
    page = client.get("/bank")
    assert "ID" not in page.text.split("Добавить счёт")[0]


def test_javascript_asset_is_available_and_csp_allows_self(bank_ui):
    client, _, _ = bank_ui
    js = client.get("/assets/bank.js")
    assert js.status_code == 200
    assert "scrollTo" in js.text
    assert "event.preventDefault()" in js.text
    assert "inline-error" in js.text
    page = client.get("/bank")
    assert "script-src 'self'" in page.headers["content-security-policy"]


def test_money_formatter_groups_and_abbreviates_without_losing_value():
    from fin.web.server import _format_amount

    assert _format_amount(120000000) == "120 000 000 (120 млн)"
    assert _format_amount(1200000) == "1 200 000 (1.2 млн)"
    assert _format_amount(1200) == "1 200 (1.2 тыс.)"
    assert _format_amount(1200000000) == "1 200 000 000 (1.2 млрд)"
    assert _format_amount(Decimal("123456.789")) == "123 456.789 (123 тыс.)"


def test_csp_permits_same_origin_ajax_form_requests(bank_ui):
    client, _, _ = bank_ui
    page = client.get("/bank")
    csp = page.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "connect-src 'self'" in csp
    assert "form-action 'self'" in csp
    assert "frame-ancestors 'none'" in csp
