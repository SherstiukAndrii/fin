"""Fin task-first UI is the only public dashboard; protected form handlers stay reusable.

All financial examples in this file are fictional.
"""

import re
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
from fin.domain.money import Money
from fin.persistence import SQLiteCashRepository
from fin.web import create_app


@pytest.fixture
def browser(tmp_path):
    path = tmp_path / "fictional-web-ux.sqlite3"
    with SQLiteCashRepository(path) as repo:
        repo.add_account(CashAccount("wallet", "Imaginary cash", "UAH", AccountKind.CASH))
        repo.save_opening_snapshot(
            OpeningSnapshot(
                date(2028, 1, 1),
                (OpeningBalance("wallet", Money(Decimal("200"), "UAH")),),
            )
        )
        repo.add_cash_event(
            CashEvent(
                "inflow",
                date(2028, 2, 15),
                CashEventKind.INFLOW,
                (CashLeg("wallet", Money(Decimal("25"), "UAH")),),
            )
        )
    with TestClient(create_app(path), base_url="http://localhost") as client:
        yield client, path


def test_bank_is_task_first_dashboard_not_legacy_long_page(browser):
    client, _ = browser
    page = client.get("/bank?as_of=2028-02-29")
    assert page.status_code == 200
    assert 'id="workspace-main"' in page.text
    assert 'id="period-form"' in page.text
    assert 'id="workspace-dialog"' in page.text
    assert 'id="workspace-toast"' in page.text
    assert "Финансовая картина" in page.text
    assert "Что нужно сделать?" in page.text
    assert 'id="bank-main"' not in page.text
    assert 'id="workspace-nav"' not in page.text
    assert "Прежний интерфейс" not in page.text
    assert 'href="/assets/bank.css"' not in page.text
    assert 'src="/assets/bank.js"' not in page.text
    for area in ("money", "assets", "due", "activity", "review"):
        assert f"area={area}" in page.text


def test_date_only_changes_read_model_not_original_history(browser):
    client, db_path = browser
    before = client.get("/bank?as_of=2028-02-01")
    after = client.get("/bank?as_of=2028-03-01")
    assert 'name="as_of" value="2028-02-01"' in before.text
    assert 'name="as_of" value="2028-03-01"' in after.text
    assert "200" in before.text
    assert "225" in after.text
    with SQLiteCashRepository(db_path) as repo:
        assert repo.balances_on(date(2028, 3, 1))["wallet"] == Money(
            Decimal("225"), "UAH"
        )
        assert len(repo.list_cash_events()) == 1


def test_form_library_is_server_side_only_and_old_style_files_are_gone(browser):
    client, _ = browser
    form_source = client.get("/bank/forms?as_of=2028-02-29")
    assert form_source.status_code == 200
    assert 'action="/bank/edit_account"' in form_source.text
    assert 'action="/bank/edit_event"' in form_source.text
    assert 'name="csrf"' in form_source.text
    assert 'href="/assets/bank.css"' not in form_source.text
    assert 'src="/assets/bank.js"' not in form_source.text
    assert client.get("/assets/bank.css").status_code == 404
    assert client.get("/assets/bank.js").status_code == 404
    assert client.get("/bank?as_of=bad-date").status_code == 422


def test_delete_preview_remains_read_only_and_explicit(browser):
    client, path = browser
    csrf = client.app.state.csrf_token
    response = client.post(
        "/bank/delete/preview?as_of=2028-03-01",
        data={"csrf": csrf, "kind": "cash_event", "identifier": "inflow"},
    )
    assert response.status_code == 200
    assert 'id="confirm-deletion"' in response.text
    assert 'name="deletion_token"' in response.text
    assert 'name="confirmation"' in response.text
    assert 'action="/bank/delete/confirm?as_of=2028-03-01"' in response.text
    with SQLiteCashRepository(path) as repo:
        assert len(repo.list_cash_events()) == 1


def test_new_assets_and_favicon_are_bundled_and_csp_restricted(browser):
    client, _ = browser
    main = client.get("/bank")
    css = client.get("/assets/workspace.css")
    js = client.get("/assets/workspace.js")
    icon = client.get("/assets/favicon-32.png")
    assert main.status_code == css.status_code == js.status_code == icon.status_code == 200
    assert 'href="/assets/workspace.css"' in main.text
    assert 'src="/assets/workspace.js"' in main.text
    assert 'href="/assets/favicon-32.png?v=ux17-4"' in main.text
    assert "master-detail" in css.text
    assert "groupDigits" in js.text
    assert 'fetch(asOfURL("/bank/forms")' in js.text
    assert 'form.closest("#workspace-dialog")' in js.text
    assert icon.content[:4] == bytes((137, 80, 78, 71))
    csp = main.headers["Content-Security-Policy"]
    for rule in (
        "default-src 'none'",
        "connect-src 'self'",
        "img-src 'self'",
        "script-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ):
        assert rule in csp


def test_delete_confirmation_requires_signed_preview(browser):
    client, _ = browser
    csrf = client.app.state.csrf_token
    preview = client.post(
        "/bank/delete/preview",
        data={"csrf": csrf, "kind": "cash_event", "identifier": "inflow"},
    )
    assert preview.status_code == 200
    token = re.search(r'name="deletion_token" value="([^"]+)"', preview.text)
    assert token is not None
    rejected = client.post(
        "/bank/delete/confirm",
        data={"csrf": csrf, "confirmation": "УДАЛИТЬ", "deletion_token": "bad"},
    )
    assert rejected.status_code == 422


def test_empty_ledger_opens_setup_instead_of_unavailable_operation(tmp_path):
    path = tmp_path / "new-ledger.sqlite3"
    with SQLiteCashRepository(path):
        pass
    with TestClient(create_app(path), base_url="http://localhost") as client:
        main = client.get("/bank")
        money = client.get("/bank?area=money")
    assert "Начни с начальных остатков" in main.text
    assert 'data-form-id="form-event"' not in main.text
    assert 'data-form-id="form-account"' in money.text
    assert 'data-form-id="form-cash"' in money.text
