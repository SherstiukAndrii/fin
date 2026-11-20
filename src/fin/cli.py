"""Fin command-line entry point: scaffold checks and local SQLite management."""

import argparse
import os
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from importlib.metadata import version
from pathlib import Path

from fin.persistence import SCHEMA_VERSION, SQLiteBankRepository


def _default_db() -> Path:
    """Keep real finance data outside Git and git clean -fdx."""
    if explicit := os.environ.get("FIN_DB_PATH"):
        return Path(explicit).expanduser()
    root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "fin" / "fin.sqlite3"


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
        legacy = Path("data/fin.sqlite3").resolve()
        if path == _default_db().expanduser().resolve() and not path.exists() and legacy.is_file():
            # Copy safely, including active WAL. Leave the legacy source untouched.
            with closing(sqlite3.connect(legacy)) as old:
                with closing(sqlite3.connect(path)) as copied:
                    old.backup(copied)
            print(f"Copied legacy database to protected location: {path}")
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
            default=_default_db(),
            metavar="FILE",
            help="SQLite file (default: ~/.local/share/fin/fin.sqlite3)",
        )

    browser = commands.add_parser("web", help="Run the local-only Bank browser UI")
    browser.add_argument(
        "--path",
        type=Path,
        default=_default_db(),
        metavar="FILE",
        help="Existing SQLite file (default: ~/.local/share/fin/fin.sqlite3)",
    )
    browser.add_argument(
        "--port",
        type=int,
        default=8765,
        metavar="PORT",
        help="Local HTTP port (default: 8765)",
    )

    args = parser.parse_args(argv)
    if args.command == "web":
        if not 1024 <= args.port <= 65535:
            parser.error("Port must be between 1024 and 65535")
        if _db_status(args.path) != 0:
            print("Run 'fin db init' first to initialize or migrate this database.")
            return 1
        from fin.web.server import run_local

        run_local(args.path, port=args.port)
        return 0

    if args.command == "db":
        if args.db_command == "init":
            return _db_init(args.path)
        return _db_status(args.path)

    print("Fin scaffold installed. Overview not implemented yet; run 'fin web' for Bank.")
    return 0
