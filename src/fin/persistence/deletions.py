"""Explicit, dependency-aware and auditable deletion of local Fin entities.

No delete is performed during preview. Confirmations use a content fingerprint
to reject changes since review. SQL tables and predicates are code-owned,
never constructed from submitted identifiers.
"""

import base64
import hashlib
import hmac
import json
import sqlite3
from dataclasses import dataclass

_TYPES = {
    "account": "Банковский счёт",
    "cash": "Наличные",
    "opening": "Начальный снимок",
    "cash_event": "Денежная операция",
    "control": "Месячная сверка",
    "holding": "Вложение",
    "payment": "Плановая выплата",
    "receipt": "Фактическое поступление",
    "securities_import": "Запись из выписки",
    "import_position": "Импортированная позиция ОВГЗ",
}
_LABELS = {
    "cash_legs": "Проводки по счетам",
    "monthly_control_balances": "Строки месячных сверок",
    "cash_events": "Денежные операции",
    "actual_receipts": "Фактические поступления",
    "scheduled_payments": "Плановые выплаты",
    "bond_lots": "Лоты ОВГЗ",
    "deposits": "Депозиты",
    "holdings": "Вложения",
    "bond_instruments": "Справочники выпусков ОВГЗ",
    "opening_balances": "Начальные остатки счетов",
    "opening_snapshot": "Начальные снимки",
    "monthly_controls": "Месячные сверки",
    "cash_accounts": "Счета",
    "securities_imports": "Записи из выписок",
}
# Children before parents: SQLite foreign keys are RESTRICT, not CASCADE.
_DELETE_ORDER = tuple(_LABELS)


@dataclass(frozen=True, slots=True)
class DeletionPlan:
    kind: str
    identifier: str
    title: str
    digest: str
    counts: tuple[tuple[str, int], ...]
    warnings: tuple[str, ...]


def _select(db: sqlite3.Connection, kind: str, identifier: str):
    if kind not in _TYPES or not isinstance(identifier, str) or not identifier:
        raise ValueError("Неизвестный тип или ID для удаления")
    if len(identifier) > 2048:
        raise ValueError("Слишком длинный идентификатор")

    selected: dict[str, dict[int, sqlite3.Row]] = {table: {} for table in _LABELS}

    def add(table: str, clause: str, params: tuple = ()) -> None:
        for row in db.execute(
            f"SELECT rowid AS _rowid, * FROM {table} WHERE {clause} ORDER BY rowid",
            params,
        ).fetchall():
            selected[table][row["_rowid"]] = row

    def add_ids(table: str, column: str, values) -> None:
        ids = tuple(values)
        if ids:
            placeholders = ",".join("?" for _ in ids)
            add(table, f"{column} IN ({placeholders})", ids)

    def follow_events() -> None:
        event_ids = [row["id"] for row in selected["cash_events"].values()]
        add_ids("cash_legs", "event_id", event_ids)

    def follow_payments() -> None:
        payment_ids = [row["id"] for row in selected["scheduled_payments"].values()]
        add_ids("actual_receipts", "payment_id", payment_ids)
        receipt_ids = [row["id"] for row in selected["actual_receipts"].values()]
        add_ids("cash_events", "receipt_id", receipt_ids)
        follow_events()

    def follow_holding() -> None:
        add("holdings", "id=?", (identifier,))
        add("bond_lots", "id=?", (identifier,))
        add("deposits", "id=?", (identifier,))
        add("scheduled_payments", "holding_id=?", (identifier,))
        follow_payments()
        for row in selected["bond_lots"].values():
            isin = row["isin"]
            # An instrument is shared by any remaining bond lots.
            if not db.execute(
                "SELECT 1 FROM bond_lots WHERE isin=? AND id<>? LIMIT 1",
                (isin, identifier),
            ).fetchone():
                add("bond_instruments", "isin=?", (isin,))

    if kind in ("account", "cash"):
        expected_kind = "cash" if kind == "cash" else "bank"
        add("cash_accounts", "id=? AND kind=?", (identifier, expected_kind))
        if not selected["cash_accounts"]:
            raise ValueError("Запись не найдена")
        if db.execute(
            "SELECT 1 FROM cash_legs WHERE account_id=? LIMIT 1", (identifier,)
        ).fetchone():
            raise ValueError(
                "Для этого источника есть денежные операции. Удали связанные операции перед удалением."
            )
        if db.execute(
            "SELECT 1 FROM monthly_control_balances WHERE account_id=? LIMIT 1",
            (identifier,),
        ).fetchone():
            raise ValueError(
                "Этот источник денег участвует в месячных сверках. Удали связанные сверки до удаления."
            )
        add("opening_balances", "account_id=?", (identifier,))
        if (
            selected["opening_balances"]
            and db.execute("SELECT COUNT(*) FROM cash_accounts").fetchone()[0] == 1
        ):
            add("opening_snapshot", "id=1")

    elif kind == "opening":
        add("opening_snapshot", "id=1")
        if db.execute("SELECT 1 FROM cash_events LIMIT 1").fetchone():
            raise ValueError("Сначала удали денежные операции: они зависят от начального снимка.")
        if db.execute("SELECT 1 FROM monthly_controls LIMIT 1").fetchone():
            raise ValueError("Сначала удали месячные сверки: они зависят от начального снимка.")
        add("opening_balances", "1=1")

    elif kind == "cash_event":
        add("cash_events", "id=?", (identifier,))
        follow_events()

    elif kind == "control":
        add("monthly_controls", "id=?", (identifier,))
        add("monthly_control_balances", "control_id=?", (identifier,))
        event_ids = [
            row["correction_event_id"]
            for row in selected["monthly_control_balances"].values()
            if row["correction_event_id"] is not None
        ]
        add_ids("cash_events", "id", event_ids)
        follow_events()

    elif kind == "holding":
        follow_holding()

    elif kind == "payment":
        add("scheduled_payments", "id=?", (identifier,))
        follow_payments()

    elif kind == "receipt":
        add("actual_receipts", "id=?", (identifier,))
        receipt_ids = [row["id"] for row in selected["actual_receipts"].values()]
        add_ids("cash_events", "receipt_id", receipt_ids)
        follow_events()

    elif kind == "securities_import":
        add("securities_imports", "source_hash=?", (identifier,))

    elif kind == "import_position":
        try:
            fields = json.loads(identifier)
        except (ValueError, TypeError) as exc:
            raise ValueError("Некорректный идентификатор импортированной позиции") from exc
        if (
            not isinstance(fields, list)
            or len(fields) != 2
            or any(not isinstance(x, str) or not x for x in fields)
        ):
            raise ValueError("Некорректный идентификатор импортированной позиции")
        add("securities_imports", "custodian=? AND isin=?", tuple(fields))

    if not any(selected.values()):
        raise ValueError("Запись уже удалена либо не найдена")

    detached = []
    if selected["cash_events"] and not selected["monthly_controls"]:
        # Removing a correction event undoes its ledger effect, but the original
        # observation remains and may be corrected again if necessary.
        event_ids = [row["id"] for row in selected["cash_events"].values()]
        placeholders = ",".join("?" for _ in event_ids)
        detached = db.execute(
            "SELECT rowid AS _rowid, * FROM monthly_control_balances "
            f"WHERE correction_event_id IN ({placeholders}) ORDER BY rowid",
            event_ids,
        ).fetchall()

    records = [
        (
            table,
            [
                tuple((key, row[key]) for key in row.keys())
                for row in sorted(values.values(), key=lambda item: item["_rowid"])
            ],
        )
        for table, values in selected.items()
        if values
    ]
    records.append(
        (
            "detached",
            [tuple((k, row[k]) for k in row.keys()) for row in detached],
        )
    )
    digest = hashlib.sha256(
        json.dumps([kind, identifier, records], ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    counts = tuple(
        (
            "Наличные"
            if table == "cash_accounts" and kind == "cash"
            else "Банковские счета"
            if table == "cash_accounts"
            else _LABELS[table],
            len(rows),
        )
        for table, rows in selected.items()
        if rows
    )
    warnings = []
    if selected["cash_events"]:
        warnings.append("Удаление денежных операций изменит реальные остатки с дат этих операций.")
    if selected["actual_receipts"]:
        warnings.append(
            "Будут удалены фактические поступления и их отдельные зачисления, если они есть."
        )
    if selected["scheduled_payments"]:
        warnings.append("График выплат и связанные факты будут пересчитаны.")
    if selected["monthly_controls"]:
        warnings.append("При удалении сверки исчезнут также подтверждённые ею корректировки.")
    if selected["opening_snapshot"]:
        warnings.append(
            "Удаление начального снимка отключит расчёт остатков до его повторного ввода."
        )
    if kind == "import_position":
        warnings.append("Будут удалены все импортированные документы этой позиции.")
    if detached:
        warnings.append(
            "Связь с подтверждённой месячной корректировкой будет снята; сама сверка останется."
        )
    return (
        DeletionPlan(kind, identifier, _TYPES[kind], digest, counts, tuple(warnings)),
        selected,
        detached,
    )


def plan_deletion(db: sqlite3.Connection, kind: str, identifier: str) -> DeletionPlan:
    """Read-only preview, including exact counts and a stale-review guard."""
    return _select(db, kind, identifier)[0]


def execute_deletion(
    db: sqlite3.Connection, kind: str, identifier: str, expected_digest: str
) -> DeletionPlan:
    """Delete only after an unchanged review, in one atomic SQLite transaction."""
    if not isinstance(expected_digest, str) or len(expected_digest) != 64:
        raise ValueError("Сначала открой предпросмотр удаления")
    db.execute("BEGIN IMMEDIATE")
    try:
        plan, selected, detached = _select(db, kind, identifier)
        if not hmac.compare_digest(plan.digest, expected_digest):
            raise ValueError("Данные изменились после предпросмотра. Проверь удаление заново.")
        if detached:
            db.executemany(
                "UPDATE monthly_control_balances SET correction_event_id=NULL WHERE rowid=?",
                [(row["_rowid"],) for row in detached],
            )
        for table in _DELETE_ORDER:
            rows = selected[table]
            if rows:
                db.executemany(
                    f"DELETE FROM {table} WHERE rowid=?",
                    [(row_id,) for row_id in sorted(rows)],
                )
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Удаление нарушает связи SQLite; изменения отменены")
        db.commit()
        return plan
    except BaseException:
        db.rollback()
        raise


def sign_deletion(plan: DeletionPlan, secret: bytes) -> str:
    """Signed, opaque-to-server-form review; no private record contents included."""
    data = json.dumps(
        [plan.kind, plan.identifier, plan.digest], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    payload = base64.urlsafe_b64encode(data).decode("ascii")
    signature = hmac.new(secret, payload.encode("ascii"), hashlib.sha256).hexdigest()
    return payload + "." + signature


def verify_deletion(token: str, secret: bytes) -> tuple[str, str, str]:
    if not isinstance(token, str) or len(token) > 4096 or "." not in token:
        raise ValueError("Некорректное подтверждение удаления")
    payload, signature = token.rsplit(".", 1)
    expected = hmac.new(secret, payload.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Подтверждение удаления было изменено")
    try:
        result = json.loads(base64.b64decode(payload, altchars=b"-_", validate=True))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Некорректное подтверждение удаления") from exc
    if (
        not isinstance(result, list)
        or len(result) != 3
        or any(not isinstance(x, str) for x in result)
    ):
        raise ValueError("Некорректное подтверждение удаления")
    return result[0], result[1], result[2]
