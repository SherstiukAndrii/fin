"""CLI integration tests for offline SQLite initialization and read-only status."""

import sqlite3

from fin.cli import main
from fin.persistence import SCHEMA_VERSION


def test_default_db_init_and_status(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "data" / "fin.sqlite3"
    assert main(["db", "init"]) == 0
    assert path.is_file()
    init_output = capsys.readouterr().out
    assert str(path) in init_output
    assert f"Schema version: {SCHEMA_VERSION}" in init_output

    assert main(["db", "status"]) == 0
    status_output = capsys.readouterr().out
    assert "Integrity: ok" in status_output
    assert "Status: ok" in status_output
    assert main(["db", "init"]) == 0
    assert main(["db", "status"]) == 0


def test_custom_path_and_missing_status_does_not_create_file(tmp_path, capsys) -> None:
    path = tmp_path / "private" / "finance.sqlite3"
    assert main(["db", "status", "--path", str(path)]) == 1
    assert not path.exists()
    assert not path.parent.exists()
    assert "not initialized" in capsys.readouterr().out

    assert main(["db", "init", "--path", str(path)]) == 0
    assert path.is_file()
    assert main(["db", "status", "--path", str(path)]) == 0


def test_status_does_not_change_existing_database(tmp_path, capsys) -> None:
    path = tmp_path / "fin.db"
    assert main(["db", "init", "--path", str(path)]) == 0
    before = path.read_bytes()
    assert main(["db", "status", "--path", str(path)]) == 0
    assert path.read_bytes() == before
    assert "Status: ok" in capsys.readouterr().out


def test_future_schema_version_is_reported_without_migration(tmp_path, capsys) -> None:
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version = 99")
    before = path.read_bytes()
    assert main(["db", "status", "--path", str(path)]) == 1
    assert "Schema version: 99" in capsys.readouterr().out
    assert path.read_bytes() == before
    assert main(["db", "init", "--path", str(path)]) == 1
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 99


def test_empty_file_and_missing_tables_are_not_reported_healthy(tmp_path, capsys) -> None:
    path = tmp_path / "empty.db"
    path.write_bytes(b"")
    assert main(["db", "status", "--path", str(path)]) == 1
    assert "unsupported or uninitialized" in capsys.readouterr().out
    with sqlite3.connect(path) as db:
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    assert main(["db", "status", "--path", str(path)]) == 1
    assert "incomplete schema" in capsys.readouterr().out


def test_status_reports_invalid_sqlite_file(tmp_path, capsys) -> None:
    path = tmp_path / "corrupted.db"
    path.write_bytes(b"not actually a sqlite file")
    assert main(["db", "status", "--path", str(path)]) == 1
    assert "unable to read database" in capsys.readouterr().out


def test_status_detects_foreign_key_violations(tmp_path, capsys) -> None:
    path = tmp_path / "broken.db"
    assert main(["db", "init", "--path", str(path)]) == 0
    with sqlite3.connect(path) as db:
        db.execute(
            """INSERT INTO actual_receipts
               (id, payment_id, received_on, amount, currency)
               VALUES ('orphan', 'missing', '2028-03-01', '10', 'UAH')"""
        )
    assert main(["db", "status", "--path", str(path)]) == 1
    assert "integrity check failed" in capsys.readouterr().out
