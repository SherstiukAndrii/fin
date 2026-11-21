"""Cash-outlook accounting and browser coverage with fictional values."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from fin.domain.bank import ActualReceipt, PaymentKind, ScheduledPayment
from fin.domain.cash_outlook import cash_outlook
from fin.domain.money import Money
from fin.persistence import SQLiteCashRepository
from fin.web import create_app


def money(value: str, currency: str = "UAH") -> Money:
    return Money(Decimal(value), currency)


def scheduled(
    payment_id: str,
    kind: PaymentKind,
    due: str,
    amount: str,
    currency: str = "UAH",
) -> ScheduledPayment:
    return ScheduledPayment(
        payment_id, "lot1", kind, date.fromisoformat(due), money(amount, currency)
    )


def receipt(receipt_id: str, payment_id: str, received: str, amount: str) -> ActualReceipt:
    return ActualReceipt(receipt_id, payment_id, date.fromisoformat(received), money(amount))


def outlook_at(
    day: str,
    *,
    balances: tuple[Money, ...] = (Money(Decimal("1000"), "UAH"),),
    payments: tuple[ScheduledPayment, ...] = (),
    receipts: tuple[ActualReceipt, ...] = (),
    posted: tuple[str, ...] = (),
    opening: str = "2027-01-01",
):
    return cash_outlook(
        balances,
        payments,
        receipts,
        posted,
        opening_on=date.fromisoformat(opening),
        as_of=date.fromisoformat(day),
    )


def test_partial_receipts_and_multiple_coupons_are_not_added_twice():
    payments = (
        scheduled("march", PaymentKind.COUPON, "2027-03-01", "80"),
        scheduled("june", PaymentKind.COUPON, "2027-06-01", "80"),
        scheduled("maturity", PaymentKind.PRINCIPAL, "2027-12-01", "2000"),
    )
    receipts = (
        receipt("posted", "march", "2027-03-02", "30"),
        receipt("unposted", "march", "2027-03-03", "20"),
        receipt("future", "march", "2027-03-09", "30"),
    )
    # The posted 30 is already inside the account balance.
    march = outlook_at(
        "2027-03-05",
        balances=(money("1030"),),
        payments=payments,
        receipts=receipts,
        posted=("posted",),
    )[0]
    assert march.actual_cash == money("1030")
    assert march.uncredited == money("20")
    assert march.expected_income == money("30")
    assert march.expected_principal == money("0")
    assert march.potential_cash == money("1080")

    june = outlook_at(
        "2027-06-02",
        balances=(money("1030"),),
        payments=payments,
        receipts=receipts,
        posted=("posted",),
    )[0]
    assert june.expected_income == money("80")
    assert june.potential_cash == money("1160")

    december = outlook_at(
        "2027-12-02",
        balances=(money("1030"),),
        payments=payments,
        receipts=receipts,
        posted=("posted",),
    )[0]
    assert december.expected_principal == money("2000")
    assert december.potential_cash == money("3160")


def test_posted_redemption_replaces_scheduled_principal_in_cash_projection():
    principal = scheduled("maturity", PaymentKind.PRINCIPAL, "2027-12-01", "2000")
    actual = receipt("principal-posted", "maturity", "2027-12-01", "2000")
    pending = outlook_at("2027-12-02", payments=(principal,))
    assert pending[0].expected_principal == money("2000")
    assert pending[0].potential_cash == money("3000")
    completed = outlook_at(
        "2027-12-02",
        balances=(money("3000"),),
        payments=(principal,),
        receipts=(actual,),
        posted=("principal-posted",),
    )[0]
    assert completed.actual_cash == money("3000")
    assert completed.uncredited == money("0")
    assert completed.expected_principal == money("0")
    assert completed.potential_cash == money("3000")


def test_overpayment_and_multi_currency_are_not_mixed():
    coupon = scheduled("march", PaymentKind.COUPON, "2027-03-01", "80")
    usd_interest = scheduled("usd", PaymentKind.INTEREST, "2027-03-01", "10", "USD")
    result = outlook_at(
        "2027-03-02",
        balances=(money("1000"), money("20", "USD")),
        payments=(coupon, usd_interest),
        receipts=(receipt("more", "march", "2027-03-02", "95"),),
    )
    assert tuple(row.currency for row in result) == ("UAH", "USD")
    assert result[0].uncredited == money("95")
    assert result[0].expected_income == money("0")
    assert result[0].potential_cash == money("1095")
    assert result[1].expected_income == money("10", "USD")
    assert result[1].potential_cash == money("30", "USD")


def test_preopening_cash_flows_not_replayed_but_later_receipt_visible():
    old = scheduled("old", PaymentKind.COUPON, "2026-12-01", "80")
    result = outlook_at("2027-03-01", payments=(old,))
    assert result[0].expected_income == money("0")
    actual = receipt("late", "old", "2027-02-01", "20")
    result = outlook_at("2027-03-01", payments=(old,), receipts=(actual,))
    assert result[0].uncredited == money("20")
    assert result[0].expected_income == money("0")
    assert result[0].potential_cash == money("1020")


def test_missing_or_duplicate_payment_references_are_rejected():
    coupon = scheduled("march", PaymentKind.COUPON, "2027-03-01", "80")
    actual = receipt("r1", "march", "2027-03-02", "20")
    with pytest.raises(ValueError, match="Duplicate actual receipt"):
        outlook_at("2027-03-03", payments=(coupon,), receipts=(actual, actual))
    with pytest.raises(ValueError, match="unknown scheduled payment"):
        outlook_at("2027-03-03", receipts=(actual,))
    with pytest.raises(ValueError, match="opening snapshot"):
        outlook_at("2026-12-31")


@pytest.fixture
def browser(tmp_path):
    db = tmp_path / "cash-outlook.sqlite3"
    with SQLiteCashRepository(db):
        pass
    with TestClient(create_app(db), base_url="http://localhost") as client:
        yield client, db, client.app.state.csrf_token


def post(browser, action: str, **fields):
    client, _, csrf = browser
    return client.post(
        f"/bank/{action}",
        data={"csrf": csrf, **fields},
        follow_redirects=False,
    )


def outlook_html(browser, day: str) -> str:
    client, _, _ = browser
    page = client.get(f"/bank/forms?as_of={day}")
    assert page.status_code == 200
    return page.text.split('<section class="card" id="cash-outlook">', 1)[1].split("</section>", 1)[
        0
    ]


def test_outlook_is_read_only_and_tracks_explicit_credit(browser):
    client, db, _ = browser
    assert (
        post(browser, "account", id="bank", name="Sample", kind="bank", currency="UAH").status_code
        == 303
    )
    assert post(browser, "opening", as_of="2027-01-01", balance_bank="100").status_code == 303
    # The deposit was already owned at the opening snapshot.
    assert (
        post(
            browser,
            "deposit",
            id="deposit1",
            bank="Sample",
            currency="UAH",
            principal="1000",
            opened_on="2026-01-01",
            matures_on="2027-06-01",
            annual_rate="0.10",
        ).status_code
        == 303
    )
    assert (
        post(
            browser,
            "payment",
            id="interest1",
            holding_id="deposit1",
            kind="interest",
            due_on="2027-03-01",
            expected="80",
        ).status_code
        == 303
    )

    initially = outlook_html(browser, "2027-03-03")
    assert "Ожидаемый доход к дате" in initially
    assert "Потенциально" in initially
    assert "180" in initially

    assert (
        post(
            browser, "receipt", payment_id="interest1", amount="50", received_on="2027-03-02"
        ).status_code
        == 303
    )
    unposted = outlook_html(browser, "2027-03-03")
    assert "Получено, не зачислено" in unposted
    assert "180" in unposted
    with SQLiteCashRepository(db) as repo:
        saved = repo.list_receipts("interest1")
        assert len(saved) == 1
        assert repo.balances_on(date(2027, 3, 3))["bank"] == money("100")
    assert (
        post(
            browser,
            "credit",
            payment_id="interest1",
            receipt_id=saved[0].id,
            account_id="bank",
        ).status_code
        == 303
    )
    credited = outlook_html(browser, "2027-03-03")
    assert "180" in credited
    with SQLiteCashRepository(db) as repo:
        assert repo.balances_on(date(2027, 3, 3))["bank"] == money("150")
    before_maturity = outlook_html(browser, "2027-05-31")
    after_maturity = outlook_html(browser, "2027-06-02")
    assert "1 180" not in before_maturity
    assert "1 180" in after_maturity
    assert "Возврат тела к дате" in after_maturity
    assert client.get("/bank?as_of=2026-12-31").status_code == 200
    assert "расчёт недоступен" in outlook_html(browser, "2026-12-31")


def test_outlook_without_opening_is_explicitly_unavailable(browser):
    html = outlook_html(browser, "2027-03-01")
    assert "Зафиксируй начальный снимок" in html
    assert "Потенциально</th>" not in html
