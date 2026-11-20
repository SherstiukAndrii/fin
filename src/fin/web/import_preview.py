"""Short-lived signed import preview: no PDF bytes or personal identifiers stored."""

import base64
import hashlib
import hmac
import json
from datetime import date
from decimal import Decimal

from fin.domain.money import Money
from fin.domain.securities_import import SecuritiesEntry


def sign_preview(entries: tuple[SecuritiesEntry, ...], secret: bytes) -> str:
    if not 1 <= len(entries) <= 16:
        raise ValueError("Preview must contain 1–16 records")
    rows = [
        {
            "source_hash": e.source_hash,
            "event_key": e.event_key,
            "custodian": e.custodian,
            "isin": e.isin,
            "as_of": e.as_of.isoformat(),
            "kind": e.kind,
            "quantity": e.quantity,
            "nominal": str(e.nominal_per_unit.amount) if e.nominal_per_unit else None,
            "currency": e.nominal_per_unit.currency if e.nominal_per_unit else None,
        }
        for e in entries
    ]
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii")
    signature = hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return encoded + "." + signature


def verify_preview(token: str, secret: bytes) -> tuple[SecuritiesEntry, ...]:
    if not isinstance(token, str) or len(token) > 16000 or "." not in token:
        raise ValueError("Invalid or expired securities preview")
    encoded, signature = token.rsplit(".", 1)
    correct = hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, correct):
        raise ValueError("Securities preview signature mismatch")
    try:
        raw = base64.b64decode(encoded, altchars=b"-_", validate=True)
        rows = json.loads(raw)
        if not isinstance(rows, list) or not 1 <= len(rows) <= 16:
            raise ValueError("Invalid import record count")
        entries = tuple(
            SecuritiesEntry(
                row["source_hash"],
                row["event_key"],
                row["custodian"],
                row["isin"],
                date.fromisoformat(row["as_of"]),
                row["kind"],
                row["quantity"],
                Money(Decimal(row["nominal"]), row["currency"])
                if row["nominal"] is not None
                else None,
            )
            for row in rows
        )
    except (TypeError, KeyError, ValueError, UnicodeDecodeError, OverflowError) as exc:
        raise ValueError("Invalid securities preview contents") from exc
    return entries
