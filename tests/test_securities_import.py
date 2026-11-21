"""Securities PDF import uses only wholly fictional, sanitized fixture values."""

import hashlib
import re
import sqlite3
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from fin.domain.bank import BondInstrument, BondLot, PaymentKind, ScheduledPayment
from fin.domain.money import Money
from fin.domain.securities_import import (
    SecuritiesEntry,
    parse_securities_pdf,
    parse_securities_text,
)
from fin.persistence import SQLiteCashRepository
from fin.persistence.sqlite_bank import _SCHEMA, _SCHEMA_V2, _SCHEMA_V3, _SCHEMA_V4
from fin.web import create_app
from fin.web.import_preview import sign_preview, verify_preview

FAKE_ISIN = "UA9876543210"
FAKE_ISIN_2 = "UA9876543211"
DAY = date(2028, 2, 29)


def fingerprint(label):
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def sample(kind="movement", *, source="example", provider="Example Custodian", quantity=7):
    return SecuritiesEntry(
        fingerprint(source),
        fingerprint("event:" + source),
        provider,
        FAKE_ISIN,
        DAY,
        kind,
        quantity,
        Money(Decimal("123"), "UAH") if kind == "closing" else None,
    )


def test_synthetic_operation_statement_does_not_capture_personal_fields():
    text = """ВИПИСКА ПРО ОПЕРАЦІЇ З ЦІННИМИ ПАПЕРАМИ
ТОВ "СМАРТ БОНД.ЮА"
Депонент: FICTIONAL_PERSON_DO_NOT_STORE
Номер рахунку: PRIVATE_ACCOUNT_DO_NOT_STORE
Операції по рахунку у цінних паперах
UA9876543210 Міністерство фінансів України
1
28.02.2028
427000
TEST COUNTERPARTY
7
Зарахування ЦП (біржа)
Дог.№ FICTIONAL-CONTRACT-1 від 28.02.2028
"""
    entry = parse_securities_text(text, fingerprint("fictional pdf"))
    assert entry.custodian == "СМАРТ БОНД.ЮА"
    assert entry.isin == FAKE_ISIN
    assert entry.as_of == date(2028, 2, 28)
    assert entry.quantity == 7
    assert entry.kind == "movement"
    assert entry.nominal_per_unit is None
    assert "FICTIONAL_PERSON" not in repr(entry)
    assert "PRIVATE_ACCOUNT" not in repr(entry)
    assert "FICTIONAL-CONTRACT" not in repr(entry)


def test_synthetic_closing_position_recognizes_nominal_and_count():
    text = """Депозитарна установа АТ «Сенс Банк»
ВИПИСКА ПРО СТАН РАХУНКУ В ЦІННИХ ПАПЕРАХ
НА 29.02.2028
Портфель ЦП Депонента
UA9876543210
Міністерство фінансів України
410000 не обтяжені
зобов'язаннями, 00000011 11 1 250,00 UAH 13 750,00 UAH
"""
    row = parse_securities_text(text, fingerprint("synthetic closing"))
    assert row.kind == "closing"
    assert row.quantity == 11
    assert row.as_of == DAY
    assert row.nominal_per_unit == Money(Decimal("1250.00"), "UAH")
    broken = text.replace("13 750,00", "13 740,00")
    with pytest.raises(ValueError, match="не совпадают"):
        parse_securities_text(broken, fingerprint("broken"))


def test_synthetic_trading_report_prefers_final_balance_not_operation_sum():
    text = """Виписка про операції з цінними паперами
за період з 29.02.2028 по 29.02.2028
Депозитарна установа ІНВЕСТИЦІЙНИЙ КАПІТАЛ УКРАЇНА
Міністерство фінансів України, ISIN: UA9876543210, номінальна
вартість ЦП: 1 000 UAH
Початковий залишок:
29.02.2028
Зарахування ЦП (біржа)
9
Кінцевий залишок:
9
"""
    row = parse_securities_text(text, fingerprint("fictional trading report"))
    assert row.kind == "closing"
    assert row.quantity == 9
    assert row.nominal_per_unit == Money(Decimal("1000"), "UAH")


def test_rejects_unsupported_or_ambiguous_documents():
    with pytest.raises(ValueError, match="PDF"):
        parse_securities_pdf(b"not a PDF")
    with pytest.raises(ValueError, match="Неизвестный"):
        parse_securities_text(
            "ВИПИСКА ПРО СТАН РАХУНКУ\nUNKNOWN",
            fingerprint("unknown"),
        )
    with pytest.raises(ValueError, match="одним"):
        parse_securities_text(
            "ВИПИСКА про операції\nСМАРТ БОНД.ЮА\nUA9876543210 UA9876543211",
            fingerprint("ambiguous"),
        )


def test_signature_guards_preview_against_tampering():
    entry = sample()
    secret = b"synthetic-test-only-key"
    token = sign_preview((entry,), secret)
    assert verify_preview(token, secret) == (entry,)
    modified = token[:-3] + "123"
    with pytest.raises(ValueError, match="signature"):
        verify_preview(modified, secret)
    with pytest.raises(ValueError, match="signature"):
        verify_preview(token, b"different-secret")


def test_migrates_v4_database_without_losing_other_records(tmp_path):
    db_path = tmp_path / "old.sqlite3"
    with sqlite3.connect(db_path) as db:
        for sql in (*_SCHEMA, *_SCHEMA_V2, *_SCHEMA_V3, *_SCHEMA_V4):
            db.execute(sql)
        db.execute("PRAGMA user_version = 4")
        db.execute(
            "INSERT INTO cash_accounts (id, name, currency, kind) VALUES ('old', 'Demo', 'UAH', 'bank')"
        )
    with SQLiteCashRepository(db_path) as repo:
        assert repo.schema_version() == 5
        assert repo.get_account("old").name == "Demo"
        assert repo.list_securities_entries() == ()
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_snapshot_supersedes_earlier_movements_and_later_changes_apply_once(tmp_path):
    with SQLiteCashRepository(tmp_path / "portfolio.sqlite3") as repo:
        earlier = SecuritiesEntry(
            fingerprint("earlier"),
            fingerprint("event-earlier"),
            "Demo",
            FAKE_ISIN,
            date(2028, 1, 1),
            "movement",
            5,
            None,
        )
        closing = SecuritiesEntry(
            fingerprint("closing"),
            fingerprint("event-closing"),
            "Demo",
            FAKE_ISIN,
            DAY,
            "closing",
            10,
            Money(Decimal("250"), "UAH"),
        )
        same_day = SecuritiesEntry(
            fingerprint("same-day"),
            fingerprint("event-same-day"),
            "Demo",
            FAKE_ISIN,
            DAY,
            "movement",
            10,
            None,
        )
        later = SecuritiesEntry(
            fingerprint("later"),
            fingerprint("event-later"),
            "Demo",
            FAKE_ISIN,
            date(2028, 3, 1),
            "movement",
            -2,
            None,
        )
        assert repo.import_securities_entries((earlier, closing, same_day, later)) == 4
        assert repo.import_securities_entries((earlier,)) == 0
        before = repo.securities_positions_on(date(2028, 1, 31))[0]
        assert before["quantity"] == 5 and not before["confirmed"]
        on_day = repo.securities_positions_on(DAY)[0]
        assert on_day["quantity"] == 10 and on_day["confirmed"]
        assert on_day["nominal"] == Money(Decimal("2500"), "UAH")
        after = repo.securities_positions_on(date(2028, 3, 1))[0]
        assert after["quantity"] == 8
        assert after["nominal"] == Money(Decimal("2000"), "UAH")


def test_same_isin_in_different_custodians_is_not_merged(tmp_path):
    with SQLiteCashRepository(tmp_path / "demo.sqlite3") as repo:
        a = sample(provider="Fictional A", source="a")
        b = sample(provider="Fictional B", source="b")
        assert repo.import_securities_entries((a, b)) == 2
        positions = repo.securities_positions_on(DAY)
        assert len(positions) == 2
        assert all(row["quantity"] == 7 for row in positions)
        assert all(not row["confirmed"] for row in positions)
        assert all(row["nominal"] is None for row in positions)


def test_conflicting_closing_statement_is_rejected_without_overwriting(tmp_path):
    with SQLiteCashRepository(tmp_path / "demo.sqlite3") as repo:
        original = sample(kind="closing", source="first", provider="Fictional Institution")
        changed = SecuritiesEntry(
            fingerprint("second"),
            fingerprint("distinct-closing-event"),
            "Fictional Institution",
            FAKE_ISIN,
            DAY,
            "closing",
            8,
            Money(Decimal("123"), "UAH"),
        )
        assert repo.import_securities_entries((original,)) == 1
        with pytest.raises(ValueError, match="Conflicting closing"):
            repo.import_securities_entries((changed,))
        assert repo.list_securities_entries() == (original,)


def test_sensitive_text_is_not_stored_in_sqlite(tmp_path):
    synthetic = """ВИПИСКА ПРО ОПЕРАЦІЇ З ЦІННИМИ ПАПЕРАМИ
ТОВ "СМАРТ БОНД.ЮА"
ПІБ: FAKE_TEST_PERSON_NOT_PERSISTED
Податковий номер: FAKE_TEST_TAX_NOT_PERSISTED
UA9876543210
1
28.02.2028
427000
TEST
7
Зарахування ЦП (біржа)
Дог.№ FAKE_CONTRACT_NOT_PERSISTED
"""
    parsed = parse_securities_text(synthetic, fingerprint("sensitive synthetic"))
    db_path = tmp_path / "demo.sqlite3"
    with SQLiteCashRepository(db_path) as repo:
        repo.import_securities_entries((parsed,))
    with sqlite3.connect(db_path) as db:
        dump = "\n".join(db.iterdump())
    for token in (
        "FAKE_TEST_PERSON_NOT_PERSISTED",
        "FAKE_TEST_TAX_NOT_PERSISTED",
        "FAKE_CONTRACT_NOT_PERSISTED",
    ):
        assert token not in dump


@pytest.fixture
def browser(tmp_path):
    path = tmp_path / "private-demo.sqlite3"
    with SQLiteCashRepository(path):
        pass
    with TestClient(create_app(path), base_url="http://localhost") as client:
        yield client, path


def test_browser_preview_confirmation_and_dedup(monkeypatch, browser):
    from fin.web import server

    client, db_path = browser
    monkeypatch.setattr(
        server,
        "parse_securities_pdf",
        lambda payload: SecuritiesEntry(
            fingerprint(payload.decode("ascii")),
            fingerprint("event" + payload.decode("ascii")),
            "Example Custodian",
            FAKE_ISIN_2,
            DAY,
            "closing",
            4,
            Money(Decimal("123"), "UAH"),
        ),
    )
    csrf = client.app.state.csrf_token
    files = [("documents", ("demo.pdf", b"%PDF-fictional", "application/pdf"))]
    page = client.post("/bank/securities/preview", data={"csrf": csrf}, files=files)
    assert page.status_code == 200
    assert "Проверь распознанные записи" in page.text
    assert FAKE_ISIN_2 in page.text
    with SQLiteCashRepository(db_path) as repo:
        assert repo.list_securities_entries() == ()
    signed = re.search(r'name="preview_token" value="([^"]+)"', page.text)
    assert signed is not None
    token = signed[1]
    forged = client.post(
        "/bank/securities/confirm",
        data={"csrf": csrf, "preview_token": token[:-3] + "123"},
    )
    assert forged.status_code == 422
    assert client.post("/bank/securities/confirm", data={"preview_token": token}).status_code == 403
    result = client.post(
        "/bank/securities/confirm",
        data={"csrf": csrf, "preview_token": token},
        follow_redirects=False,
    )
    assert result.status_code == 303
    assert "imported=1" in result.headers["location"]
    position_page = client.get("/bank?as_of=2028-02-29")
    assert "Example Custodian" in position_page.text
    assert "Подтверждённый остаток" in position_page.text
    with SQLiteCashRepository(db_path) as repo:
        assert len(repo.list_securities_entries()) == 1
        assert repo.list_cash_events() == ()
        assert repo.list_bond_lots() == ()
    again = client.post(
        "/bank/securities/confirm",
        data={"csrf": csrf, "preview_token": token},
        follow_redirects=False,
    )
    assert again.status_code == 303
    assert "imported=0" in again.headers["location"]


def test_browser_rejects_empty_upload_without_writing(browser):
    client, path = browser
    response = client.post(
        "/bank/securities/preview",
        data={"csrf": client.app.state.csrf_token},
    )
    assert response.status_code == 422
    with SQLiteCashRepository(path) as repo:
        assert repo.list_securities_entries() == ()


def _manual_lot(lot_id: str, *, quantity: int, custodian: str) -> BondLot:
    return BondLot(
        lot_id,
        BondInstrument(FAKE_ISIN, Money(Decimal("123"), "UAH"), date(2029, 12, 31)),
        date(2028, 1, 10),
        quantity,
        Money(Decimal("120"), "UAH"),
        Money(Decimal("2"), "UAH"),
        custodian,
    )


def _investments_html(client, day: str = "2028-02-29") -> str:
    response = client.get(f"/bank?as_of={day}")
    assert response.status_code == 200
    return response.text.split('<section class="card" id="holdings">', 1)[1].split("</section>", 1)[
        0
    ]


def test_confirmed_pdf_position_appears_in_investments_and_not_twice(browser):
    client, path = browser
    with SQLiteCashRepository(path) as repo:
        assert repo.import_securities_entries((sample("closing"),)) == 1

    holdings = _investments_html(client)
    assert '<h2>Вложения <span class="pill">1</span></h2>' in holdings
    primary_rows = holdings.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    assert primary_rows.count("<tr>") == 1
    assert FAKE_ISIN in primary_rows
    assert "Подтверждённый остаток" in primary_rows
    assert "7 шт." in primary_rows
    assert "861 UAH" in primary_rows
    assert "неизвестно" in primary_rows
    before = _investments_html(client, "2028-02-28")
    assert '<h2>Вложения <span class="pill">0</span></h2>' in before


def test_existing_bond_lots_are_not_double_counted_and_remain_editable(browser):
    client, path = browser
    with SQLiteCashRepository(path) as repo:
        repo.add_bond_lot(_manual_lot("lot-one", quantity=3, custodian="Example Custodian"))
        repo.add_bond_lot(_manual_lot("lot-two", quantity=4, custodian="Example Custodian"))
        repo.add_payment(
            ScheduledPayment(
                "coupon-example",
                "lot-one",
                PaymentKind.COUPON,
                date(2028, 6, 1),
                Money(Decimal("10"), "UAH"),
            )
        )
        repo.import_securities_entries((sample("closing"),))

    holdings = _investments_html(client)
    assert '<h2>Вложения <span class="pill">1</span></h2>' in holdings
    main_table = holdings.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    assert main_table.count("<tr>") == 1
    assert "7 шт." in main_table
    assert "Подтверждённый остаток" in main_table
    assert 'id="manual-matched-lots"' in holdings
    assert 'name="id" value="lot-one"' in holdings
    assert 'name="id" value="lot-two"' in holdings
    assert 'action="/bank/edit_bond"' in holdings
    # Contractual payouts from manually defined lots remain available.
    assert "coupon-example" not in main_table
    response = client.get("/bank?as_of=2028-06-01")
    assert "10 UAH" in response.text
    with SQLiteCashRepository(path) as repo:
        assert len(repo.list_bond_lots()) == 2
        assert len(repo.list_securities_entries()) == 1
        assert repo.list_cash_events() == ()


def test_unverified_movement_does_not_override_manually_recorded_lot(browser):
    client, path = browser
    with SQLiteCashRepository(path) as repo:
        repo.add_bond_lot(_manual_lot("manual", quantity=5, custodian="Example Custodian"))
        repo.import_securities_entries((sample("movement"),))
    holdings = _investments_html(client)
    assert '<h2>Вложения <span class="pill">1</span></h2>' in holdings
    main_table = holdings.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    assert "5 × 123" in main_table
    assert "В выписке есть движение, но полный остаток не подтверждён" in main_table
    assert "Только изменение количества; общий остаток неизвестен" not in main_table


def test_different_custodian_keeps_independent_investment_positions(browser):
    client, path = browser
    with SQLiteCashRepository(path) as repo:
        repo.add_bond_lot(_manual_lot("manual", quantity=5, custodian="Another Custodian"))
        repo.import_securities_entries((sample("closing"),))
    holdings = _investments_html(client)
    assert '<h2>Вложения <span class="pill">2</span></h2>' in holdings
    main_table = holdings.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    assert main_table.count("<tr>") == 2
    assert "Another Custodian" in main_table
    assert "Example Custodian" in main_table
