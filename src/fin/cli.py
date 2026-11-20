"""Fin command-line entry point: scaffold checks and local SQLite management."""

import argparse
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from importlib.metadata import version
from pathlib import Path

from fin.persistence import SCHEMA_VERSION, SQLiteBankRepository

_DEFAULT_DB = Path("data/fin.sqlite3")
_REQUIRED_TABLES = frozenset(
    {
        "bond_instruments",
        "holdings",
        "bond_lots",
        "deposits",
        "scheduled_payments",
        "actual_receipts",
        "cash_accounts",
        "opening_snapshot",
        "opening_balances",
        "cash_events",
        "cash_legs",
    }
)


def _db_init(path: Path) -> int:
    path = path.expanduser().resolve()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with SQLiteBankRepository(path) as repository:
            schema_version = repository.schema_version()
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        print(f"Database initialization failed: {exc}")
        return 1
    print(f"Database: {path}")
    print(f"Schema version: {schema_version}")
    print("Status: initialized")
    return 0


def _db_status(path: Path) -> int:
    """Inspect without creating or migrating a database file."""
    path = path.expanduser().resolve()
    print(f"Database: {path}")
    if not path.is_file():
        print("Status: not initialized (database file missing)")
        return 1

    try:
        # SQLite's URI mode=ro prevents status from accidentally creating a file.
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as db:
            schema_version = db.execute("PRAGMA user_version").fetchone()[0]
            integrity = db.execute("PRAGMA quick_check").fetchone()[0]
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            invalid_foreign_key = db.execute("PRAGMA foreign_key_check").fetchone()
    except (OSError, sqlite3.Error) as exc:
        print(f"Status: unable to read database ({exc})")
        return 1

    print(f"Schema version: {schema_version}")
    print(f"Integrity: {integrity}")
    if schema_version != SCHEMA_VERSION:
        print(f"Status: unsupported or uninitialized schema (expected {SCHEMA_VERSION})")
        return 1
    if integrity != "ok" or invalid_foreign_key is not None:
        print("Status: database integrity check failed")
        return 1
    missing = _REQUIRED_TABLES - tables
    if missing:
        print(f"Status: incomplete schema (missing: {', '.join(sorted(missing))})")
        return 1
    print("Status: ok")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run offline Fin CLI commands; no network access or server startup."""
    parser = argparse.ArgumentParser(prog="fin", description="Fin offline finance tools")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('fin-tools')}")
    commands = parser.add_subparsers(dest="command")
    database = commands.add_parser("db", help="Manage the local SQLite database")
    database_commands = database.add_subparsers(dest="db_command", required=True)
    for name, help_text in (
        ("init", "Create the database and apply schema migrations"),
        ("status", "Read database status without creating or modifying the file"),
    ):
        command = database_commands.add_parser(name, help=help_text)
        command.add_argument(
            "--path",
            type=Path,
            default=_DEFAULT_DB,
            metavar="FILE",
            help="SQLite file (default: data/fin.sqlite3, relative to the working directory)",
        )

    args = parser.parse_args(argv)
    if args.command == "db":
        if args.db_command == "init":
            return _db_init(args.path)
        return _db_status(args.path)

    print("Fin scaffold is installed. Bank and Overview are not implemented yet.")
    return 0
