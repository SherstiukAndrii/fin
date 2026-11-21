"""Task-first workspace UX: real data, object selection and safe legacy actions.

The amounts, entities, dates and identifiers here are completely fictional.
"""

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
from fin.domain.bank import BondInstrument, BondLot, PaymentKind, ScheduledPayment
from fin.domain.money import Money
from fin.persistence import SQLiteCashRepository
from fin.web import create_app


def amount(number: str) -> Money:
    return Money(Decimal(number), "UAH")


@pytest.fixture
def browser(tmp_path):
    path = tmp_path / "fake-task-first.sqlite3"
    with SQLiteCashRepository(path) as repo:
        repo.add_account(CashAccount("bank", "Imaginary Bank", "UAH", AccountKind.BANK))
        repo.add_account(CashAccount("pocket", "Pocket money", "UAH", AccountKind.CASH))
        repo.save_opening_snapshot(
            OpeningSnapshot(
                date(2028, 1, 1),
                (
                    OpeningBalance("bank", amount("1000")),
                    OpeningBalance("pocket", amount("200")),
                ),
            )
        )
        repo.add_cash_event(
            CashEvent(
                "transfer",
                date(2028, 2, 1),
                CashEventKind.TRANSFER,
                (CashLeg("bank", amount("-150")), CashLeg("pocket", amount("150"))),
            )
        )
        repo.add_bond_lot(
            BondLot(
                "fictional-lot",
                BondInstrument("UA9999999999", amount("1000"), date(2029, 12, 31)),
                date(2028, 1, 15),
                2,
                None,
                None,
                "Imaginary Custodian",
            )
        )
        repo.add_payment(
            ScheduledPayment(
                "fictional-coupon",
                "fictional-lot",
                PaymentKind.COUPON,
                date(2028, 3, 20),
                amount("50"),
            )
        )
    with TestClient(create_app(path), base_url="http://localhost") as client:
        yield client, path


def test_workspace_home_is_task_hub_not_full_page_of_forms(browser):
    client, _ = browser
    response = client.get("/bank?as_of=2028-02-15")
    assert response.status_code == 200
    assert 'id="workspace-main"' in response.text
    assert "Финансовая картина" in response.text
    assert "Что нужно сделать?" in response.text
    assert "Доступные деньги" in response.text
    assert "1 200" in response.text
    assert "Imaginary Custodian" in response.text
    assert 'data-form-id="form-event"' in response.text
    assert 'data-form-id="form-bond"' in response.text
    assert 'data-form-id="form-securities-import"' in response.text
    assert 'id="workspace-dialog"' in response.text
    assert 'id="bank-main"' not in response.text
    assert '<details id="form-event"' not in response.text


def test_money_master_detail_separates_bank_and_cash(browser):
    client, _ = browser
    page = client.get("/bank?area=money&as_of=2028-02-15")
    assert page.status_code == 200
    assert "БАНКОВСКИЕ СЧЕТА" in page.text
    assert "НАЛИЧНЫЕ" in page.text
    assert "Imaginary Bank" in page.text
    assert "Pocket money" in page.text
    assert "Выбери запись слева" in page.text
    assert 'id="object-search"' in page.text
    assert 'data-form-id="form-account"' not in page.text
    assert 'data-form-id="form-cash"' not in page.text
    assert "Новые счета после начального снимка пока не поддерживаются" in page.text
    assert '<details id="form-account"' not in page.text

    detail = client.get("/bank?area=money&target=bank&as_of=2028-02-15")
    assert detail.status_code == 200
    assert "850" in detail.text
    assert 'data-edit-action="/bank/edit_account"' in detail.text
    assert 'data-delete-kind="account"' in detail.text
    assert "Последние операции" in detail.text
    assert "01.02.2028" in detail.text
    cash = client.get("/bank?area=money&target=pocket&as_of=2028-02-15")
    assert cash.status_code == 200
    assert "350" in cash.text
    assert 'data-delete-kind="cash"' in cash.text
    assert "НАЛИЧНЫЕ НА РУКАХ" in cash.text


def test_portfolio_detail_shows_specific_investment_and_its_payout(browser):
    client, _ = browser
    list_page = client.get("/bank?area=assets&as_of=2028-02-15")
    assert list_page.status_code == 200
    assert "UA9999999999" in list_page.text
    assert "Выбери запись слева" in list_page.text
    item = client.get("/bank?area=assets&target=0&as_of=2028-02-15")
    assert item.status_code == 200
    assert "31.12.2029" in item.text or "2029-12-31" in item.text
    assert 'data-delete-kind="holding"' in item.text
    assert 'data-edit-action="/bank/edit_bond"' in item.text
    assert "Выплаты по этому вложению" in item.text
    assert "20.03.2028" in item.text


def test_payment_detail_separates_plan_and_fact(browser):
    client, _ = browser
    response = client.get("/bank?area=due&target=fictional-coupon&as_of=2028-03-21")
    assert response.status_code == 200
    assert "ПЛАНОВАЯ ВЫПЛАТА" in response.text
    assert "Фактически получено" in response.text
    assert "Осталось" in response.text
    assert 'data-edit-action="/bank/receipt"' in response.text
    assert 'data-match-field="payment_id"' in response.text
    assert 'data-delete-kind="payment"' in response.text
    assert "не зачислено" not in response.text
    assert "UA9999999999" in response.text


def test_activity_detail_uses_existing_cash_ledger_and_actions(browser):
    client, _ = browser
    page = client.get("/bank?area=activity&target=transfer&as_of=2028-02-15")
    assert page.status_code == 200
    assert "ФАКТИЧЕСКАЯ ОПЕРАЦИЯ" in page.text
    assert "Imaginary Bank" in page.text
    assert "Pocket money" in page.text
    assert 'data-edit-action="/bank/edit_event"' in page.text
    assert 'data-delete-kind="cash_event"' in page.text


def test_workspace_reads_are_non_mutating_even_when_dates_change(browser):
    client, path = browser
    for url in (
        "/bank",
        "/bank?area=home&as_of=2028-01-01",
        "/bank?area=money&target=bank&as_of=2028-02-01",
        "/bank?area=due&target=fictional-coupon&as_of=2028-03-21",
        "/bank?area=assets&target=0&as_of=2028-02-01",
        "/bank?area=activity&target=transfer&as_of=2028-02-15",
        "/bank?area=review&as_of=2028-02-15",
    ):
        assert client.get(url).status_code == 200
    with SQLiteCashRepository(path) as repo:
        assert len(repo.list_cash_events()) == 1
        assert len(repo.list_bond_lots()) == 1
        assert len(repo.list_payments("fictional-lot")) >= 1
        assert repo.balances_on(date(2028, 2, 15))["bank"] == amount("850")
        assert repo.balances_on(date(2028, 2, 15))["pocket"] == amount("350")


def test_unknown_workspace_and_invalid_date_rejected(browser):
    client, _ = browser
    assert client.get("/bank?area=secret").status_code == 404
    assert client.get("/bank?area=home&as_of=2028-99-40").status_code == 422
    page = client.get("/bank?area=assets&target=-50&as_of=2028-02-15")
    assert page.status_code == 200
    assert "Выбери запись слева" in page.text


def test_workspace_assets_and_security_controls_are_local(browser):
    client, _ = browser
    page = client.get("/bank")
    css = client.get("/assets/bank.css")
    js = client.get("/assets/bank.js")
    assert page.status_code == css.status_code == js.status_code == 200
    assert 'href="/assets/bank.css"' in page.text
    assert 'src="/assets/bank.js"' in page.text
    assert 'meta name="fin-csrf"' in page.text
    assert 'href="/bank?' in page.text
    assert "master-detail" in css.text
    assert "Task-first Fin workspace" in js.text
    assert "/bank/delete/preview" in js.text
    assert "/bank/securities/preview" in js.text
    assert "const url = new URL" in js.text
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert "script-src 'self'" in page.headers["content-security-policy"]


def test_bank_is_the_only_primary_ui_and_form_source_is_internal(browser):
    client, _ = browser
    bank = client.get("/bank?as_of=2028-02-15")
    assert bank.status_code == 200
    assert 'id="workspace-main"' in bank.text
    assert 'id="bank-main"' not in bank.text
    assert "Прежний интерфейс" not in bank.text
    assert 'href="/assets/workspace.css"' in bank.text
    assert 'src="/assets/workspace.js"' in bank.text
    forms = client.get("/bank/forms?as_of=2028-02-15")
    assert forms.status_code == 200
    assert 'action="/bank/deposit"' in forms.text
    assert 'action="/bank/event"' in forms.text
    assert 'href="/assets/bank.css"' not in forms.text
    assert 'src="/assets/bank.js"' not in forms.text
    assert client.get("/assets/bank.css").status_code == 404
    assert client.get("/assets/bank.js").status_code == 404
    assert "Imaginary Bank" in bank.text


def test_setup_uses_real_prerequisites_instead_of_broken_action_buttons(tmp_path):
    path = tmp_path / "fictional-setup.sqlite3"
    with SQLiteCashRepository(path):
        pass
    with TestClient(create_app(path), base_url="http://localhost") as client:
        home = client.get("/bank?as_of=2028-02-15")
        assert home.status_code == 200
        assert "Начни с начальных остатков" in home.text
        assert 'data-form-id="form-event"' not in home.text
        assert "Настроить деньги" in home.text

        before = client.get("/bank?area=money&as_of=2028-02-15")
        assert 'data-form-id="form-account"' in before.text
        assert 'data-form-id="form-cash"' in before.text
        assert 'data-form-id="form-opening"' not in before.text

        with SQLiteCashRepository(path) as repo:
            repo.add_account(CashAccount("bank", "Imaginary Bank", "UAH", AccountKind.BANK))
        with_account = client.get("/bank?area=money&as_of=2028-02-15")
        assert 'data-form-id="form-opening"' in with_account.text

        with SQLiteCashRepository(path) as repo:
            repo.save_opening_snapshot(
                OpeningSnapshot(
                    date(2028, 1, 1),
                    (OpeningBalance("bank", amount("100")),),
                )
            )
        after = client.get("/bank?area=money&as_of=2028-02-15")
        assert "Новые счета после начального снимка пока не поддерживаются" in after.text
        assert 'data-form-id="form-account"' not in after.text
        assert 'data-form-id="form-cash"' not in after.text
        activity = client.get("/bank?area=activity&as_of=2028-02-15")
        assert 'data-form-id="form-event"' in activity.text
