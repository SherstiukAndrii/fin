"""Create a holding and its contractual payment calendar in one atomic form."""

from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from fin.domain.bank import Deposit, PaymentKind, ScheduledPayment
from fin.domain.money import Money
from fin.persistence import SQLiteCashRepository
from fin.web import create_app


@pytest.fixture
def browser(tmp_path):
    db_path = tmp_path / "fin.sqlite3"
    with SQLiteCashRepository(db_path):
        pass
    with TestClient(create_app(db_path), base_url="http://localhost") as client:
        yield client, db_path, client.app.state.csrf_token


def bond(**changes):
    row = {
        "isin": "UA1234567890",
        "currency": "UAH",
        "face_value": "1 000",
        "quantity": "200",
        "acquired_on": "2026-11-20",
        "matures_on": "2028-11-20",
        "unit_price": "980.00",
        "paid_fees": "100",
    }
    row.update(changes)
    return row


def deposit(**changes):
    row = {
        "bank": "__other__",
        "bank_other": "Sample bank",
        "currency": "UAH",
        "principal": "100 000.00",
        "opened_on": "2026-11-20",
        "matures_on": "2027-11-20",
        "annual_rate": "0.15",
    }
    row.update(changes)
    return row


def post(browser, kind, fields, rows):
    client, _, token = browser
    pairs = [("csrf", token), *fields.items()]
    for payment_kind, day, value in rows:
        pairs.extend(
            (
                ("schedule_kind", payment_kind),
                ("schedule_on", day),
                ("schedule_amount", value),
            )
        )
    return client.post(
        f"/bank/{kind}",
        content=urlencode(pairs),
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )


def test_bond_and_multiple_coupon_payments_created_together(browser):
    _, path, _ = browser
    response = post(
        browser,
        "bond",
        bond(),
        [
            ("coupon", "2027-05-20", "8 000.25"),
            ("coupon", "2027-11-20", "8 000.25"),
        ],
    )
    assert response.status_code == 303
    with SQLiteCashRepository(path) as repo:
        lots = repo.list_bond_lots()
        assert len(lots) == 1
        payments = repo.list_payments(lots[0].id)
        assert [p.kind for p in payments] == [
            PaymentKind.COUPON,
            PaymentKind.COUPON,
            PaymentKind.PRINCIPAL,
        ]
        assert [p.expected.amount for p in payments] == [
            Decimal("8000.25"),
            Decimal("8000.25"),
            Decimal("200000"),
        ]
        assert all(p.holding_id == lots[0].id for p in payments)
        assert repo.list_cash_events() == ()


def test_deposit_and_interest_principal_created_together(browser):
    _, path, _ = browser
    response = post(
        browser,
        "deposit",
        deposit(),
        [
            ("interest", "2027-05-20", "7 500.50"),
        ],
    )
    assert response.status_code == 303
    with SQLiteCashRepository(path) as repo:
        holdings = repo.list_deposits()
        assert len(holdings) == 1
        payments = repo.list_payments(holdings[0].id)
        assert [p.kind for p in payments] == [PaymentKind.INTEREST, PaymentKind.PRINCIPAL]
        assert [p.expected.amount for p in payments] == [Decimal("7500.50"), Decimal("100000.00")]
        assert repo.list_cash_events() == ()


def test_no_schedule_and_one_untouched_row_still_allow_holding(browser):
    _, path, _ = browser
    assert post(browser, "bond", bond(), [("coupon", "", "")]).status_code == 303
    assert post(browser, "deposit", deposit(), []).status_code == 303
    with SQLiteCashRepository(path) as repo:
        assert len(repo.list_bond_lots()) == 1
        assert len(repo.list_deposits()) == 1
        bond_payments = repo.list_payments(repo.list_bond_lots()[0].id)
        deposit_payments = repo.list_payments(repo.list_deposits()[0].id)
        assert len(bond_payments) == len(deposit_payments) == 1
        assert bond_payments[0].kind == PaymentKind.PRINCIPAL
        assert bond_payments[0].expected.amount == Decimal("200000")
        assert deposit_payments[0].kind == PaymentKind.PRINCIPAL
        assert deposit_payments[0].expected.amount == Decimal("100000.00")
        assert repo.is_auto_principal(bond_payments[0].id)
        assert repo.is_auto_principal(deposit_payments[0].id)


@pytest.mark.parametrize(
    ("kind", "values", "rows", "expected"),
    [
        ("bond", bond(), [("coupon", "2029-01-01", "100")], "outside holding dates"),
        ("deposit", deposit(), [("interest", "2028-01-01", "100")], "outside holding dates"),
        ("bond", bond(), [("interest", "2027-05-20", "100")], "не подходит"),
        ("deposit", deposit(), [("coupon", "2027-05-20", "100")], "не подходит"),
        ("bond", bond(), [("coupon", "2027-05-20", "")], "Укажи дату и сумму"),
        ("deposit", deposit(), [("interest", "", "50")], "Укажи дату и сумму"),
        ("deposit", deposit(), [("principal", "2027-11-20", "100 000.01")], "не подходит"),
        (
            "bond",
            bond(),
            [
                ("coupon", "2027-05-20", "8000"),
                ("principal", "2028-11-20", "200001"),
            ],
            "не подходит",
        ),
    ],
)
def test_invalid_schedule_rolls_back_entire_investment(browser, kind, values, rows, expected):
    _, path, _ = browser
    response = post(browser, kind, values, rows)
    assert response.status_code == 422
    assert expected in response.text
    with SQLiteCashRepository(path) as repo:
        assert repo.list_bond_lots() == ()
        assert repo.list_deposits() == ()
        assert repo._db.execute("SELECT COUNT(*) FROM holdings").fetchone()[0] == 0
        assert repo._db.execute("SELECT COUNT(*) FROM scheduled_payments").fetchone()[0] == 0


def test_invalid_later_row_preserves_all_entered_schedule_rows(browser):
    _, path, _ = browser
    response = post(
        browser,
        "bond",
        bond(),
        [
            ("coupon", "2027-05-20", "8 000"),
            ("coupon", "2029-05-20", "9 000"),
        ],
    )
    assert response.status_code == 422
    assert 'value="2027-05-20"' in response.text
    assert 'value="2029-05-20"' in response.text
    assert 'value="8 000"' in response.text
    assert 'value="9 000"' in response.text
    assert 'id="form-bond" open' in response.text
    with SQLiteCashRepository(path) as repo:
        assert repo.list_bond_lots() == ()


def test_repository_composite_rollback_on_wrong_holding_id(tmp_path):
    path = tmp_path / "db.sqlite3"
    invest = Deposit(
        "deposit1",
        "Sample",
        Money(Decimal("100"), "UAH"),
        date(2026, 11, 20),
        date(2027, 11, 20),
        Decimal("0.1"),
    )
    bad = ScheduledPayment(
        "payment1",
        "another_id",
        PaymentKind.INTEREST,
        date(2027, 5, 20),
        Money(Decimal("5"), "UAH"),
    )
    with SQLiteCashRepository(path) as repo:
        with pytest.raises(ValueError, match="belong"):
            repo.add_holding_with_schedule(invest, [bad])
        assert repo.get_deposit("deposit1") is None
        assert repo.get_payment("payment1") is None


def test_browser_shows_schedule_builder_for_both_investment_forms(browser):
    client, _, _ = browser
    page = client.get("/bank/forms")
    assert page.status_code == 200
    assert page.text.count("data-schedule-builder") == 2
    assert page.text.count("data-schedule-template") == 2
    assert "Сохранить лот и график" in page.text
    assert "Сохранить депозит и график" in page.text
    assert page.text.count("data-auto-principal") == 2
    assert 'name="custodian"' in page.text
    assert 'name="schedule_kind"' in page.text
    assert 'name="schedule_on"' in page.text
    assert 'name="schedule_amount"' in page.text
    js = client.get("/assets/workspace.js").text
    assert "data-schedule-add" in js
    assert "data-schedule-remove" in js
    assert 'button[type="submit"],button:not([type])' in js
