"""Synthetic Babki XLSX import tests; no user's financial records in source control."""

import sqlite3
from datetime import datetime

from fastapi.testclient import TestClient
from openpyxl import Workbook

from fin.cli import main
from fin.importers.babki import import_babki, staged_babki_rows
from fin.persistence import SQLiteCashRepository
from fin.web import create_app


def make_sample_xlsx(path):
    wb = Workbook()
    bank = wb.active
    bank.title = "Bank"
    wb.create_sheet("Overview")
    names = ["Deposit", "Bond A", "Bond B", "Bond C", "Bond D", "Bond E"]
    for row, name in enumerate(names, 2):
        bank.cell(row, 1, name)
        bank.cell(row, 2, datetime(2028, 12, 20))
        bank.cell(row, 3, 1050.0)
        bank.cell(row, 4, 1)
        bank.cell(row, 5, 0.13)
    bank["A9"] = "Bond A"
    bank["B9"] = datetime(2026, 11, 17)
    bank["C9"] = 1010.0
    bank["D9"] = 200
    overview = wb["Overview"]
    overview["B2"] = 250000
    overview["C1"] = 15000
    overview["D1"] = 0
    overview["C3"] = 42
    overview["D3"] = 49
    overview["B7"] = 0.15
    wb.save(path)


def test_import_preview_is_visible_but_never_posts_balances(tmp_path):
    source = tmp_path / "Babki.xlsx"
    make_sample_xlsx(source)
    db_path = tmp_path / "finance.sqlite3"
    with SQLiteCashRepository(db_path) as db:
        assert import_babki(source, db._db) == 12
        assert import_babki(source, db._db) == 12
        rows = staged_babki_rows(db._db)
        assert len(rows) == 12
        assert any(r["kind"] == "purchase" and r["values"]["quantity"] == "200" for r in rows)
        assert db.list_bond_lots() == ()
        assert db.list_accounts() == ()
    with TestClient(create_app(db_path), base_url="http://localhost") as client:
        page = client.get("/bank")
        assert page.status_code == 200
        assert "Импортировано из Babki" in page.text
        assert "Bond A" in page.text
        assert "12" in page.text
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2


def test_cli_import_uses_external_db_and_no_sql_intermediate(tmp_path, monkeypatch):
    path = tmp_path / "Babki.xlsx"
    make_sample_xlsx(path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "private"))
    assert main(["db", "init"]) == 0
    assert main(["babki", "import", str(path)]) == 0
    assert main(["babki", "import", str(path)]) == 0
    with SQLiteCashRepository(tmp_path / "private" / "fin" / "fin.sqlite3") as db:
        assert len(staged_babki_rows(db._db)) == 12
