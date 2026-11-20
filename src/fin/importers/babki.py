"""Import a user's local Babki XLSX as auditable staging, never as cash."""

import hashlib
import json
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook


def _value(item):
    if isinstance(item, (date, datetime)):
        return item.date().isoformat() if isinstance(item, datetime) else item.isoformat()
    if isinstance(item, (int, float)) and not isinstance(item, bool):
        return str(Decimal(str(item)))
    return "" if item is None else str(item)


def _calendar(item, row):
    if not isinstance(item, (date, datetime)):
        raise ValueError(f"Bank row {row}: missing date")
    return _value(item)


def _decimal(item, row):
    try:
        result = Decimal(str(item))
        if not result.is_finite():
            raise ValueError("nonfinite")
        return str(result)
    except (ValueError, ArithmeticError) as exc:
        raise ValueError(f"Bank row {row}: invalid monetary value") from exc


def _entries(path):
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        if not {"Bank", "Overview"} <= set(wb.sheetnames):
            raise ValueError("Babki.xlsx must contain Bank and Overview sheets")
        bank, overview = wb["Bank"], wb["Overview"]
        reference = {}
        for row in range(2, 8):
            name, maturity, proceeds, quantity, rate, *_ = (x.value for x in bank[row][:6])
            if not name:
                continue
            values = {
                "name": str(name),
                "maturity": _calendar(maturity, row),
                "projected_proceeds_per_unit_uah": _decimal(proceeds, row),
                "total_quantity": str(int(quantity)),
                "target_annual_yield": _value(rate),
                "needs_identification": True,
            }
            reference[str(name)] = values
            yield ("Bank", row, "instrument", str(name), values)
        for row, cells in enumerate(
            bank.iter_rows(min_row=9, max_col=8, values_only=True), start=9
        ):
            name, acquired, unit_price, quantity, *_ = cells
            if not name:
                continue
            label = str(name)
            if label not in reference:
                raise ValueError(f"Bank row {row}: unknown instrument {label}")
            values = {
                "name": label,
                "acquired_on": _calendar(acquired, row),
                "unit_purchase_price_uah": _decimal(unit_price, row),
                "quantity": str(int(quantity)),
                "maturity": reference[label]["maturity"],
                "projected_proceeds_per_unit_uah": (
                    reference[label]["projected_proceeds_per_unit_uah"]
                ),
                "needs_identification": True,
            }
            yield ("Bank", row, "purchase", label, values)
        for row, kind in ((1, "cash"), (2, "bank")):
            for col, currency in ((2, "UAH"), (3, "USD"), (4, "EUR")):
                val = overview.cell(row=row, column=col).value
                if val is not None:
                    yield (
                        "Overview",
                        row,
                        "account_value",
                        currency,
                        {
                            "account_type": kind,
                            "currency": currency,
                            "amount": _decimal(val, row),
                            "as_of_confirmed": False,
                        },
                    )
        yield (
            "Overview",
            3,
            "rates",
            "fx",
            {
                "usd_uah_reference": _decimal(overview["C3"].value, 3),
                "eur_uah_reference": _decimal(overview["D3"].value, 3),
                "rate_date": None,
            },
        )
        yield (
            "Overview",
            7,
            "inflation_assumption",
            "inflation",
            {"annual_fraction": _decimal(overview["B7"].value, 7)},
        )
    finally:
        wb.close()


def import_babki(source: str | Path, db: sqlite3.Connection) -> int:
    """One immutable, idempotent local source snapshot in staging tables."""
    source = Path(source).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Babki.xlsx not found: {source}")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    entries = list(_entries(source))
    with db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS babki_import_batches (
               source_hash TEXT PRIMARY KEY, source_name TEXT NOT NULL,
               source_url TEXT NOT NULL,
               imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, note TEXT NOT NULL)"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS babki_import_rows (
               source_hash TEXT NOT NULL REFERENCES babki_import_batches(source_hash),
               source_sheet TEXT NOT NULL, source_row INTEGER NOT NULL,
               record_type TEXT NOT NULL, source_key TEXT NOT NULL,
               payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
               PRIMARY KEY (source_hash, source_sheet, source_row, record_type, source_key))"""
        )
        db.execute(
            """INSERT OR IGNORE INTO babki_import_batches
               (source_hash, source_name, source_url, note) VALUES (?, ?, ?, ?)""",
            (digest, source.name, "Local workbook", "Staging only; no ledger posts"),
        )
        for sheet, row, kind, key, values in entries:
            db.execute(
                """INSERT OR IGNORE INTO babki_import_rows
                   (source_hash, source_sheet, source_row, record_type, source_key,
                    payload_json) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    digest,
                    sheet,
                    row,
                    kind,
                    key,
                    json.dumps(values, ensure_ascii=False, sort_keys=True),
                ),
            )
    return len(entries)


def staged_babki_rows(db: sqlite3.Connection) -> tuple[dict, ...]:
    """Read the earlier SQL staging imports and XLSX staging imports alike."""
    found = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='babki_import_rows'"
    ).fetchone()
    if not found:
        return ()
    rows = db.execute(
        """SELECT source_sheet, source_row, record_type, payload_json
           FROM babki_import_rows ORDER BY source_sheet, source_row, record_type"""
    ).fetchall()
    return tuple(
        {
            "sheet": row["source_sheet"],
            "row": row["source_row"],
            "kind": row["record_type"],
            "values": json.loads(row["payload_json"]),
        }
        for row in rows
    )
