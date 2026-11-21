"""Physical cash must be distinct from bank accounts in the Bank user interface.

Synthetic values only; SQLite ledger balances remain unchanged.
"""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from fin.domain.accounts import AccountKind
from fin.domain.money import Money
from fin.persistence import SQLiteCashRepository
from fin.web import create_app


@pytest.fixture
def browser(tmp_path):
    path = tmp_path / "fictional-money.sqlite3"
    with SQLiteCashRepository(path):
        pass
    with TestClient(create_app(path), base_url="http://localhost") as client:
        yield client, path


def post(client, route, **values):
    return client.post(
        f"/bank/{route}",
        data={"csrf": client.app.state.csrf_token, **values},
        follow_redirects=False,
    )


def assert_saved(client, route, **values):
    assert post(client, route, **values).status_code == 303


def test_cash_uses_separate_creation_and_distinct_display(browser):
    client, path = browser
    assert_saved(client, "bank", id="fictional_bank", name="Imaginary Institution")
    page = client.get("/bank/forms")
    assert 'data-view="accounts">Деньги</a>' in page.text
    assert 'id="bank-accounts"' in page.text
    assert 'id="cash-on-hand"' in page.text
    assert 'action="/bank/account"' in page.text
    assert 'action="/bank/cash"' in page.text
    assert '<summary class="entrypoint">+ Добавить наличные</summary>' in page.text

    assert_saved(client, "account", name="Fictional Bank", kind="bank", currency="UAH")
    assert_saved(client, "cash", currency="UAH")

    with SQLiteCashRepository(path) as repo:
        bank, cash = sorted(repo.list_accounts(), key=lambda a: a.kind.value)
        assert bank.kind == AccountKind.BANK
        assert cash.kind == AccountKind.CASH
        assert cash.name == "На руках"
        assert cash.currency == "UAH"

    html = client.get("/bank/forms").text
    bank_view = html.split('id="bank-accounts"', 1)[1].split('id="cash-on-hand"', 1)[0]
    cash_view = html.split('id="cash-on-hand"', 1)[1].split('id="form-opening"', 1)[0]
    assert "Fictional Bank" in bank_view
    assert "На руках" not in bank_view
    assert "На руках" in cash_view
    assert "Fictional Bank" not in cash_view
    assert 'value="bank"' in bank_view
    assert 'value="cash"' in cash_view
    assert "Удалить наличные" in cash_view
    assert "Удалить счёт" not in cash_view


def test_bank_and_cash_transfers_preserve_total_real_balance(browser):
    client, path = browser
    assert_saved(client, "account", name="Fictional Bank", kind="bank", currency="UAH")
    assert_saved(client, "cash", currency="UAH")
    with SQLiteCashRepository(path) as repo:
        bank = next(a for a in repo.list_accounts() if a.kind == AccountKind.BANK)
        cash = next(a for a in repo.list_accounts() if a.kind == AccountKind.CASH)
    assert_saved(
        client,
        "opening",
        as_of="2028-01-01",
        **{
            f"balance_{bank.id}": "1000",
            f"balance_{cash.id}": "200",
        },
    )
    earlier = client.get("/bank/forms?as_of=2028-01-01").text
    assert "1 000" in earlier
    assert "200" in earlier
    assert "Наличные на руках" in earlier

    assert_saved(
        client,
        "event",
        effective_on="2028-01-02",
        kind="transfer",
        account_id=bank.id,
        to_account_id=cash.id,
        amount="150",
    )

    with SQLiteCashRepository(path) as repo:
        start = repo.balances_on(date(2028, 1, 1))
        end = repo.balances_on(date(2028, 1, 2))
        assert start[bank.id] == Money(Decimal("1000"), "UAH")
        assert start[cash.id] == Money(Decimal("200"), "UAH")
        assert end[bank.id] == Money(Decimal("850"), "UAH")
        assert end[cash.id] == Money(Decimal("350"), "UAH")
        assert sum(m.amount for m in start.values()) == sum(m.amount for m in end.values())
        assert len(repo.list_cash_events()) == 1


def test_cash_deletion_has_separate_preview_and_account_type_guard(browser):
    client, path = browser
    assert_saved(client, "cash", currency="USD")
    with SQLiteCashRepository(path) as repo:
        cash = repo.list_accounts()[0]
        with pytest.raises(ValueError, match="не найдена"):
            repo.plan_delete("account", cash.id)
        plan = repo.plan_delete("cash", cash.id)
        assert plan.title == "Наличные"
        assert ("Наличные", 1) in plan.counts

    response = client.post(
        "/bank/delete/preview",
        data={
            "csrf": client.app.state.csrf_token,
            "kind": "cash",
            "identifier": cash.id,
        },
    )
    assert response.status_code == 200
    assert "Подтверждение удаления: Наличные" in response.text
    with SQLiteCashRepository(path) as repo:
        assert repo.get_account(cash.id) is not None


def test_bank_account_cannot_accidentally_be_deleted_as_cash(browser):
    client, path = browser
    assert_saved(client, "account", kind="bank", name="Fictional Bank", currency="UAH")
    with SQLiteCashRepository(path) as repo:
        bank = repo.list_accounts()[0]
        with pytest.raises(ValueError, match="не найдена"):
            repo.plan_delete("cash", bank.id)
        assert repo.plan_delete("account", bank.id).title == "Банковский счёт"


def test_cash_label_and_bank_label_preserved_in_transaction_forms(browser):
    client, _ = browser
    assert_saved(client, "account", name="Fictional Bank", currency="UAH", kind="bank")
    assert_saved(client, "cash", currency="UAH")
    html = client.get("/bank/forms").text
    assert "Банковские счета" in html
    assert "Наличные на руках" in html
    assert "Наличные · На руках (UAH)" in html
    assert "Fictional Bank (UAH)" in html
    assert "Между своими счетами" not in html
