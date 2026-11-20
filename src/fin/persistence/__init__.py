"""Offline SQLite repositories; no live financial data is bundled."""

from fin.persistence.sqlite_bank import SCHEMA_VERSION, SQLiteBankRepository

__all__ = ["SCHEMA_VERSION", "SQLiteBankRepository"]
