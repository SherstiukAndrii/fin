"""Offline Bank UX regression tests: navigation, date controls and safe deletion.

All financial examples in this file are synthetic.
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
    db_path = tmp_path / "synthetic-ui.sqlite3"
    with SQLiteCashRepository(db_path) as repo:
        repo.add_account(CashAccount("wallet", "Fictional wallet", "UAH", AccountKind.CASH))
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
    with TestClient(create_app(db_path), base_url="http://localhost") as client:
        yield client, db_path


def test_workspace_nav_is_compact_and_has_stable_ids(browser):
    client, _ = browser
    response = client.get("/bank?as_of=2028-02-29")
    assert response.status_code == 200
    html = response.text
    assert 'id="workspace-nav"' in html
    for view in (
        "overview",
        "accounts",
        "investments",
        "payments",
        "reconciliation",
        "transactions",
    ):
        assert f'data-view="{view}"' in html
    assert 'id="page-messages"' in html
    assert html.count('id="as-of-form"') == 1
    assert html.count('id="as-of-field"') == 1
    assert html.count('id="fin-dialog"') == 1
    assert html.count('id="fin-toast"') == 1
    for section in (
        "cash-outlook",
        "accounts",
        "holdings",
        "imported-securities",
        "payments",
        "monthly-controls",
        "events",
    ):
        assert f'id="{section}"' in html


def test_date_is_explicit_and_history_is_preserved(browser):
    client, db_path = browser
    page = client.get("/bank?as_of=2028-02-01")
    assert 'name="as_of" value="2028-02-01"' in page.text
    assert ">01.02.2028</strong>" in page.text
    assert 'data-month-step="-1"' in page.text
    assert 'data-month-step="1"' in page.text
    assert 'data-date-preset="today"' in page.text
    assert 'data-date-preset="previous-month-end"' in page.text
    assert 'data-today="' in page.text
    assert "Изменяет только просмотр, не записи" in page.text
    assert "200</strong>" in page.text
    future = client.get("/bank?as_of=2028-03-01")
    assert ">01.03.2028</strong>" in future.text
    assert "225</strong>" in future.text
    with SQLiteCashRepository(db_path) as repo:
        assert repo.balances_on(date(2028, 3, 1))["wallet"] == Money(Decimal("225"), "UAH")
        assert len(repo.list_cash_events()) == 1


def test_modal_preview_does_not_create_an_actual_deletion(browser):
    client, db_path = browser
    csrf = client.app.state.csrf_token
    page = client.post(
        "/bank/delete/preview?as_of=2028-03-01",
        data={"csrf": csrf, "kind": "cash_event", "identifier": "inflow"},
    )
    assert page.status_code == 200
    assert 'id="confirm-deletion"' in page.text
    assert "Подтверждение удаления" in page.text
    assert 'id="fin-dialog"' in page.text
    assert 'id="dialog-body"' in page.text
    assert 'name="deletion_token"' in page.text
    assert 'action="/bank/delete/confirm?as_of=2028-03-01"' in page.text
    with SQLiteCashRepository(db_path) as repo:
        assert len(repo.list_cash_events()) == 1


def test_date_and_deletion_ux_assets_load_offline(browser):
    client, _ = browser
    css = client.get("/assets/bank.css")
    js = client.get("/assets/bank.js")
    assert css.status_code == 200
    assert js.status_code == 200
    assert "period-controls" in css.text
    assert ".workspace-nav" in css.text
    assert "#fin-dialog::backdrop" in css.text
    assert "scrollbar-gutter: stable" in css.text
    assert "function activateView(view, updateHistory = false)" in js.text
    assert 'const isDeletePreview = url.pathname === "/bank/delete/preview"' in js.text
    assert "showDeleteDialog(preview)" in js.text
    assert "existing.replaceWith(incoming)" in js.text
    assert "page.replaceWith(next)" not in js.text
    assert "prepareForms(next)" in js.text
    assert "window.scrollTo" in js.text


def test_security_policy_allows_local_css_without_external_dependencies(browser):
    client, _ = browser
    page = client.get("/bank")
    assert 'href="/assets/bank.css"' in page.text
    assert 'src="/assets/bank.js"' in page.text
    policy = page.headers["Content-Security-Policy"]
    assert "style-src 'self' 'unsafe-inline'" in policy
    assert "script-src 'self'" in policy
    assert "connect-src 'self'" in policy
    assert "default-src 'none'" in policy


def test_fin_no_script_fallback_still_has_delete_confirmation_form(browser):
    client, _ = browser
    csrf = client.app.state.csrf_token
    preview = client.post(
        "/bank/delete/preview",
        data={"csrf": csrf, "kind": "cash_event", "identifier": "inflow"},
    )
    assert preview.status_code == 200
    assert 'id="confirm-deletion"' in preview.text
    token = re.search(r'name="deletion_token" value="([^"]+)"', preview.text)
    assert token is not None
    assert 'name="confirmation"' in preview.text
