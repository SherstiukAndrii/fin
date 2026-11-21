"""Local-only browser interface for Fin Bank; no external service calls."""

import os
import secrets
import sqlite3
from calendar import monthrange
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile
from starlette.middleware.trustedhost import TrustedHostMiddleware

from fin.domain.accounts import (
    AccountKind,
    Bank,
    CashAccount,
    CashEvent,
    CashEventKind,
    CashLeg,
    OpeningBalance,
    OpeningSnapshot,
    SecuritiesAccount,
)
from fin.domain.bank import (
    ActualReceipt,
    BondInstrument,
    BondLot,
    Deposit,
    PaymentKind,
    ScheduledPayment,
    payment_balance,
)
from fin.domain.cash_outlook import cash_outlook
from fin.domain.money import Money
from fin.domain.securities_import import parse_securities_pdf
from fin.persistence import SQLiteCashRepository
from fin.persistence.deletions import sign_deletion, verify_deletion
from fin.web.import_preview import sign_preview, verify_preview

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_KINDS = {
    "bank",
    "edit_bank",
    "securities_account",
    "edit_securities_account",
    "account",
    "cash",
    "opening",
    "event",
    "bond",
    "deposit",
    "payment",
    "receipt",
    "credit",
    "control",
    "confirm_control",
    "edit_account",
    "edit_opening",
    "edit_event",
    "edit_bond",
    "edit_deposit",
    "edit_payment",
    "edit_receipt",
}


def _field(form, name: str, *, optional: bool = False) -> str:
    value = form.get(name)
    if value is None and optional:
        return ""
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError(f"Invalid field: {name}")
    value = value.strip()
    if not value and not optional:
        raise ValueError(f"Required field: {name}")
    return value


def _decimal(form, name: str, *, optional: bool = False) -> Decimal | None:
    value = _field(form, name, optional=optional)
    if not value and optional:
        return None
    # Grouped numbers from the browser (e.g. "1 000 000.50") must
    # round-trip to an exact Decimal without converting through float.
    normalized = "".join(value.split())
    if "," in normalized and "." not in normalized:
        normalized = normalized.replace(",", ".")
    try:
        result = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid number: {name}") from exc
    if not result.is_finite():
        raise ValueError(f"Amount must be finite: {name}")
    return result


def _date(form, name: str) -> date:
    try:
        return date.fromisoformat(_field(form, name))
    except ValueError as exc:
        raise ValueError(f"Invalid date: {name}") from exc


def _quantity(form, name: str) -> int:
    value = "".join(_field(form, name).split())
    try:
        quantity = int(value)
    except ValueError as exc:
        raise ValueError("Количество должно быть целым числом") from exc
    if not 1 <= quantity <= 2**63 - 1:
        raise ValueError("Количество должно быть от 1 до 9223372036854775807")
    return quantity


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _scheduled_from_form(
    form, holding_id: str, currency: str, *, is_bond: bool
) -> tuple[ScheduledPayment, ...]:
    """Read optional repeated payout rows, without inventing coupon amounts or dates."""
    kinds = form.getlist("schedule_kind")
    dates = form.getlist("schedule_on")
    amounts = form.getlist("schedule_amount")
    if len(kinds) != len(dates) or len(dates) != len(amounts):
        raise ValueError("Заполни тип, дату и сумму каждой строки выплат")
    if len(kinds) > 100:
        raise ValueError("Не больше 100 плановых выплат за один раз")
    allowed = (PaymentKind.COUPON,) if is_bond else (PaymentKind.INTEREST,)
    payments: list[ScheduledPayment] = []
    for index, (kind, due, amount) in enumerate(zip(kinds, dates, amounts, strict=True), 1):
        if not all(isinstance(v, str) and len(v) <= 2048 for v in (kind, due, amount)):
            raise ValueError(f"Недопустимые данные выплаты №{index}")
        if not due.strip() and not amount.strip():
            continue  # untouched optional row
        if not due.strip() or not amount.strip():
            raise ValueError(f"Укажи дату и сумму выплаты №{index}")
        try:
            payment_kind = PaymentKind(kind)
        except ValueError as exc:
            raise ValueError(f"Неизвестный тип выплаты №{index}") from exc
        if payment_kind not in allowed:
            raise ValueError(f"Тип выплаты №{index} не подходит для вложения")
        payments.append(
            ScheduledPayment(
                _new_id("payment"),
                holding_id,
                payment_kind,
                _date({"schedule_on": due}, "schedule_on"),
                Money(_decimal({"schedule_amount": amount}, "schedule_amount"), currency),
            )
        )
    return tuple(payments)


def _submit(repo: SQLiteCashRepository, kind: str, form) -> None:
    editing = kind.startswith("edit_")
    kind = kind.removeprefix("edit_")
    if kind == "bank":
        bank = Bank(
            _field(form, "id")
            if editing
            else (_field(form, "id", optional=True) or _new_id("bank")),
            _field(form, "name"),
            _field(form, "website_url", optional=True) or None,
            _field(form, "cabinet_url", optional=True) or None,
        )
        if editing:
            repo.update_bank(bank)
        else:
            repo.add_bank(bank)
    elif kind == "securities_account":
        account = SecuritiesAccount(
            _field(form, "id")
            if editing
            else (_field(form, "id", optional=True) or _new_id("securities")),
            _field(form, "bank_id"),
            _field(form, "name"),
        )
        if editing:
            repo.update_securities_account(account)
        else:
            repo.add_securities_account(account)
    elif kind in {"account", "cash"}:
        account_kind = (
            AccountKind.CASH if kind == "cash" else AccountKind(_field(form, "kind"))
        )
        if kind == "cash" and _field(form, "kind", optional=True) not in {"", "cash"}:
            raise ValueError("Наличные добавляются отдельно от банковских счетов")
        if kind == "account" and not editing and account_kind != AccountKind.BANK:
            raise ValueError("Для наличных используй отдельную форму")
        if account_kind == AccountKind.CASH and _field(form, "bank_id", optional=True):
            raise ValueError("Наличные не принадлежат банку")
        account = CashAccount(
            _field(form, "id")
            if editing
            else (
                _field(form, "id", optional=True)
                or _new_id("cash" if kind == "cash" else "account")
            ),
            _field(form, "name", optional=True) or "На руках"
            if kind == "cash"
            else _field(form, "name"),
            _field(form, "currency").upper(),
            account_kind,
            (
                _field(form, "bank_id", optional=True) or None
                if account_kind == AccountKind.BANK
                else None
            ),
            (
                date.fromisoformat(_field(form, "opened_on"))
                if _field(form, "opened_on", optional=True)
                else None
            )
            if not editing
            else None,
        )
        if editing:
            repo.update_account(account)
        else:
            repo.add_account(account)
    elif kind == "opening":
        opening_existing = repo.get_opening_snapshot() if editing else None
        accounts = (
            tuple(
                account
                for account in repo.list_accounts()
                if any(row.account_id == account.id for row in opening_existing.balances)
            )
            if opening_existing is not None
            else repo.list_accounts()
        )
        if not accounts:
            raise ValueError("Create an account first")
        snapshot = OpeningSnapshot(
            _date(form, "as_of"),
            tuple(
                OpeningBalance(
                    account.id, Money(_decimal(form, f"balance_{account.id}"), account.currency)
                )
                for account in accounts
            ),
        )
        if editing:
            repo.update_opening_snapshot(snapshot)
        else:
            repo.save_opening_snapshot(snapshot)
    elif kind == "event":
        event_kind = CashEventKind(_field(form, "kind"))
        if event_kind not in (
            CashEventKind.INFLOW,
            CashEventKind.OUTFLOW,
            CashEventKind.TRANSFER,
            CashEventKind.CORRECTION,
        ):
            raise ValueError("Use the dedicated form for investment receipts")
        from_account = repo.get_account(_field(form, "account_id"))
        if from_account is None:
            raise ValueError("Unknown account")
        amount = _decimal(form, "amount")
        if event_kind == CashEventKind.TRANSFER:
            to_account = repo.get_account(_field(form, "to_account_id"))
            if to_account is None:
                raise ValueError("Unknown destination account")
            if amount <= 0:
                raise ValueError("Transfer amount must be positive")
            legs = (
                CashLeg(from_account.id, Money(-amount, from_account.currency)),
                CashLeg(to_account.id, Money(amount, from_account.currency)),
            )
        else:
            if event_kind != CashEventKind.CORRECTION and amount <= 0:
                raise ValueError("Amount must be positive")
            signed = -amount if event_kind == CashEventKind.OUTFLOW else amount
            legs = (CashLeg(from_account.id, Money(signed, from_account.currency)),)
        event = CashEvent(
            _field(form, "id")
            if editing
            else (_field(form, "id", optional=True) or _new_id("event")),
            _date(form, "effective_on"),
            event_kind,
            legs,
            note=_field(form, "note", optional=True) or None,
        )
        if editing:
            repo.update_cash_event(event)
        else:
            repo.add_cash_event(event)
    elif kind == "bond":
        currency = _field(form, "currency").upper()
        if editing:
            original = repo.get_bond_lot(_field(form, "id"))
            if original is None:
                raise ValueError("Позиция ОВГЗ не найдена")
            instrument = original.instrument
            currency = instrument.face_value.currency
        else:
            instrument = BondInstrument(
                _field(form, "isin").upper(),
                Money(_decimal(form, "face_value"), currency),
                _date(form, "matures_on"),
            )
        price = _decimal(form, "unit_price", optional=True)
        fees = _decimal(form, "paid_fees", optional=True)
        provider = _field(form, "custodian", optional=True)
        if provider == "__other__":
            provider = _field(form, "custodian_other")
        if editing and "custodian" not in form:
            provider = original.custodian
        lot = BondLot(
            _field(form, "id")
            if editing
            else (_field(form, "id", optional=True) or _new_id("lot")),
            instrument,
            _date(form, "acquired_on"),
            _quantity(form, "quantity"),
            None if price is None else Money(price, currency),
            None if fees is None else Money(fees, currency),
            provider or None,
        )
        if editing:
            repo.update_bond_lot(lot)
        else:
            payments = _scheduled_from_form(form, lot.id, currency, is_bond=True)
            repo.add_holding_with_schedule(lot, payments)
    elif kind == "deposit":
        currency = _field(form, "currency").upper()
        bank_name = _field(form, "bank")
        if bank_name == "__other__":
            bank_name = _field(form, "bank_other")
        annual_rate = _decimal(form, "annual_rate", optional=True)
        deposit = Deposit(
            _field(form, "id")
            if editing
            else (_field(form, "id", optional=True) or _new_id("deposit")),
            bank_name,
            Money(_decimal(form, "principal"), currency),
            _date(form, "opened_on"),
            _date(form, "matures_on"),
            annual_rate,
        )
        if editing:
            repo.update_deposit(deposit)
        else:
            payments = _scheduled_from_form(form, deposit.id, currency, is_bond=False)
            repo.add_holding_with_schedule(deposit, payments)
    elif kind == "payment":
        holding_id = _field(form, "holding_id")
        bond = repo.get_bond_lot(holding_id)
        deposit = repo.get_deposit(holding_id)
        currency = (
            bond.instrument.face_value.currency
            if bond
            else (deposit.principal.currency if deposit else None)
        )
        if currency is None:
            raise ValueError("Unknown investment holding")
        payment = ScheduledPayment(
            _field(form, "id")
            if editing
            else (_field(form, "id", optional=True) or _new_id("payment")),
            holding_id,
            PaymentKind(_field(form, "kind")),
            _date(form, "due_on"),
            Money(_decimal(form, "expected"), currency),
        )
        if editing:
            repo.update_payment(payment)
        else:
            repo.add_payment(payment)
    elif kind == "control":
        control_date = _date(form, "as_of")
        active_ids = repo.balances_on(control_date)
        observed = {
            account.id: Money(_decimal(form, f"balance_{account.id}"), account.currency)
            for account in repo.list_accounts()
            if account.id in active_ids
        }
        repo.record_monthly_control(
            _new_id("control"),
            control_date,
            observed,
            note=_field(form, "note", optional=True) or None,
        )
    elif kind == "confirm_control":
        account_id = _field(form, "account_id")
        account = repo.get_account(account_id)
        if account is None:
            raise ValueError("Unknown account for monthly correction")
        repo.confirm_monthly_correction(
            _field(form, "control_id"),
            account_id,
            expected_difference=Money(_decimal(form, "expected_difference"), account.currency),
            event_id=_new_id("correction"),
            reason=_field(form, "reason"),
        )
    elif kind == "receipt":
        payment = repo.get_payment(_field(form, "payment_id"))
        if payment is None:
            raise ValueError("Unknown scheduled payment")
        receipt = ActualReceipt(
            _field(form, "id") if editing else _new_id("receipt"),
            payment.id,
            _date(form, "received_on"),
            Money(_decimal(form, "amount"), payment.expected.currency),
        )
        if editing:
            repo.update_receipt(receipt)
        else:
            repo.add_receipt(receipt)
    elif kind == "credit":
        payment_id = _field(form, "payment_id")
        receipt_id = _field(form, "receipt_id")
        receipt = next((r for r in repo.list_receipts(payment_id) if r.id == receipt_id), None)
        if receipt is None:
            raise ValueError("Unknown actual receipt")
        repo.add_cash_event(
            CashEvent(
                _new_id("credit"),
                receipt.received_on,
                CashEventKind.RECEIPT,
                (CashLeg(_field(form, "account_id"), receipt.amount),),
                receipt_id=receipt.id,
            )
        )
    else:
        raise ValueError("Unknown form")


def _format_amount(value) -> str:
    """Exact grouping plus a compact human-readable approximation."""
    number = value.amount if isinstance(value, Money) else Decimal(str(value))
    plain = format(number, "f")
    sign = "-" if plain.startswith("-") else ""
    integer, dot, fractional = plain.lstrip("-").partition(".")
    spaced = sign + " ".join(
        reversed([integer[max(0, end - 3) : end] for end in range(len(integer), 0, -3)])
    )
    if dot:
        spaced += "." + fractional
    absolute = abs(number)
    if absolute >= Decimal("1000000000"):
        divisor, unit = Decimal("1000000000"), "млрд"
    elif absolute >= Decimal("1000000"):
        divisor, unit = Decimal("1000000"), "млн"
    elif absolute >= Decimal("1000"):
        divisor, unit = Decimal("1000"), "тыс."
    else:
        return spaced
    shortened = absolute / divisor
    if shortened >= 100:
        approximate = str(shortened.quantize(Decimal("1")))
    else:
        approximate = str(shortened.quantize(Decimal("0.1")))
        if "." in approximate:
            approximate = approximate.rstrip("0").rstrip(".")
    return f"{spaced} ({sign}{approximate} {unit})"


def _backup_before_edit(path: Path) -> None:
    """Protect one user-edited financial record from irreversible mistakes."""
    from contextlib import closing

    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    directory = base / "fin" / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    name = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    destination = directory / f"fin-before-edit-{name}-{uuid4().hex[:8]}.sqlite3"
    try:
        with closing(sqlite3.connect(path)) as original:
            with closing(sqlite3.connect(destination)) as backup:
                original.backup(backup)
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _read_view(repo: SQLiteCashRepository, as_of: date) -> dict:
    accounts = repo.list_accounts()
    banks = repo.list_banks()
    securities_accounts = repo.list_securities_accounts()
    banks_by_id = {bank.id: bank for bank in banks}
    bank_accounts = tuple(account for account in accounts if account.kind == AccountKind.BANK)
    cash_locations = tuple(account for account in accounts if account.kind == AccountKind.CASH)
    account_labels = {
        account.id: (
            f"Наличные · {account.name} ({account.currency})"
            if account.kind == AccountKind.CASH
            else f"{banks_by_id[account.bank_id].name} · {account.name} ({account.currency})"
            if account.bank_id in banks_by_id
            else f"{account.name} ({account.currency})"
        )
        for account in accounts
    }
    opening = repo.get_opening_snapshot()
    bank_names = tuple(
        sorted(
            {
                *(account.name for account in accounts if account.kind == AccountKind.BANK),
                *(bank.name for bank in banks),
                *(deposit.bank for deposit in repo.list_deposits()),
            },
            key=str.casefold,
        )
    )
    custodian_names = tuple(
        sorted(
            {
                *bank_names,
                *(bond.custodian for bond in repo.list_bond_lots() if bond.custodian),
                *(record.custodian for record in repo.list_securities_entries()),
            },
            key=str.casefold,
        )
    )
    warning = None
    balances = {}
    if opening:
        if as_of < opening.as_of:
            warning = "Selected date is before the opening snapshot"
        else:
            balances = repo.balances_on(as_of)
    active_accounts = tuple(account for account in accounts if account.id in balances)
    holdings = []
    for bond in repo.list_bond_lots():
        holdings.append(
            {
                "id": bond.id,
                "type": "OVGZ / bond",
                "name": bond.instrument.isin,
                "custodian": bond.custodian or "Не указан",
                "amount": f"{_format_amount(bond.quantity)} × {_format_amount(bond.instrument.face_value)}",
                "currency": bond.instrument.face_value.currency,
                "maturity": bond.instrument.matures_on,
                "cost": _format_amount(bond.cost_basis) if bond.cost_basis else "неизвестно",
                "model": bond,
                "label": (
                    f"ОВГЗ {bond.instrument.isin} · {bond.custodian or 'Не указан'}"
                    f" · {bond.quantity} шт. · {bond.acquired_on}"
                ),
            }
        )
    for deposit in repo.list_deposits():
        holdings.append(
            {
                "id": deposit.id,
                "type": "Deposit",
                "name": deposit.bank,
                "amount": _format_amount(deposit.principal),
                "currency": deposit.principal.currency,
                "maturity": deposit.matures_on,
                "cost": "—",
                "model": deposit,
                "label": f"Депозит {deposit.bank} · {deposit.principal.amount} {deposit.principal.currency}",
            }
        )
    payments = []
    posted_receipts = {
        event.receipt_id for event in repo.list_cash_events() if event.receipt_id is not None
    }
    for holding in holdings:
        for payment in repo.list_payments(holding["id"]):
            receipts = repo.list_receipts(payment.id)
            balance = payment_balance(payment, receipts, as_of)
            payments.append(
                {
                    "id": payment.id,
                    "holding": holding["id"],
                    "kind": payment.kind.value,
                    "due": payment.due_on,
                    "expected": payment.expected,
                    "received": balance.received,
                    "outstanding": balance.outstanding,
                    "excess": balance.excess,
                    "status": balance.status.value,
                    "auto_principal": repo.is_auto_principal(payment.id),
                    "model": payment,
                    "holding_label": holding["label"],
                    "receipts": [{"item": r, "posted": r.id in posted_receipts} for r in receipts],
                }
            )
    payments.sort(key=lambda item: (item["due"], item["id"]))
    outlook = None
    if opening is not None and as_of >= opening.as_of:
        outlook = cash_outlook(
            balances.values(),
            (entry["model"] for entry in payments),
            (receipt["item"] for entry in payments for receipt in entry["receipts"]),
            posted_receipts,
            opening_on=opening.as_of,
            as_of=as_of,
        )
    controls = []
    if opening is not None and as_of >= opening.as_of:
        for snapshot in reversed(repo.list_monthly_controls()):
            if snapshot.as_of > as_of:
                continue
            calculated_now = repo.balances_on(snapshot.as_of)
            rows = [
                {
                    "account": repo.get_account(row.account_id),
                    "observed": row.observed,
                    "calculated_at_capture": row.calculated_at_capture,
                    "current_calculated": calculated_now[row.account_id],
                    "difference": row.observed - calculated_now[row.account_id],
                    "correction_event_id": row.correction_event_id,
                }
                for row in snapshot.balances
            ]
            controls.append(
                {"id": snapshot.id, "as_of": snapshot.as_of, "note": snapshot.note, "rows": rows}
            )
    last_day = monthrange(as_of.year, as_of.month)[1]
    suggested = as_of if as_of.day == last_day else as_of.replace(day=1) - timedelta(days=1)
    control_default = (
        suggested.isoformat() if opening is not None and suggested > opening.as_of else ""
    )
    imported_positions = repo.securities_positions_on(as_of)
    imported_entries = repo.list_securities_entries()
    confirmed_keys = {
        (position["isin"], position["custodian"].strip().casefold())
        for position in imported_positions
        if position["confirmed"]
    }
    imported_keys = {
        (position["isin"], position["custodian"].strip().casefold())
        for position in imported_positions
    }
    manual_keys = {
        (holding["model"].instrument.isin, holding["model"].custodian.strip().casefold())
        for holding in holdings
        if isinstance(holding["model"], BondLot) and holding["model"].custodian
    }
    investment_rows = []
    matched_manual = []
    for holding in holdings:
        bond = holding["model"]
        if isinstance(bond, BondLot) and bond.custodian:
            key = (bond.instrument.isin, bond.custodian.strip().casefold())
            if key in confirmed_keys:
                matched_manual.append(holding)
                continue
            if key in imported_keys:
                holding["movement_notice"] = True
        investment_rows.append(holding)

    security_nominal_totals: dict[str, Decimal] = {}
    for position in imported_positions:
        key = (position["isin"], position["custodian"].strip().casefold())
        if not position["confirmed"] and key in manual_keys:
            continue  # Movement alone cannot replace an existing manually entered lot.
        investment_rows.append(
            {
                "type": "OVGZ / bond",
                "name": position["isin"],
                "custodian": position["custodian"],
                "amount": f"{_format_amount(position['quantity'])} шт.",
                "currency": "",
                "cost": "неизвестно",
                "maturity": "неизвестно",
                "imported": True,
                "confirmed": position["confirmed"],
                "as_of": position["as_of"],
                "nominal": position["nominal"],
            }
        )
        if position["confirmed"] and position["nominal"] is not None:
            currency = position["nominal"].currency
            security_nominal_totals[currency] = (
                security_nominal_totals.get(currency, Decimal("0")) + position["nominal"].amount
            )
    investment_rows.sort(
        key=lambda item: (
            item["type"] == "Deposit",
            item["name"].casefold(),
            item.get("custodian", "").casefold(),
        )
    )
    events = repo.list_cash_events()
    return {
        "accounts": accounts,
        "banks": banks,
        "securities_accounts": securities_accounts,
        "banks_by_id": banks_by_id,
        "bank_accounts": bank_accounts,
        "cash_locations": cash_locations,
        "account_labels": account_labels,
        "opening": opening,
        "bank_names": bank_names,
        "custodian_names": custodian_names,
        "balances": balances,
        "active_accounts": active_accounts,
        "controls": controls,
        "control_default": control_default,
        "outlook": outlook,
        "holdings": holdings,
        "investment_rows": tuple(investment_rows),
        "matched_manual": tuple(matched_manual),
        "imported_positions": imported_positions,
        "imported_entries": imported_entries,
        "security_nominal_totals": tuple(sorted(security_nominal_totals.items())),
        "payments": payments,
        "events": tuple(reversed(events[-30:])),
        "as_of": as_of,
        "warning": warning,
    }


def create_app(db_path: str | Path) -> FastAPI:
    """Provide a local Bank UI; caller MUST bind the server to loopback only."""
    db_path = Path(db_path).expanduser().resolve()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount(
        "/assets", StaticFiles(directory=str(Path(__file__).parent / "static")), name="assets"
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    _TEMPLATES.env.filters["amount"] = _format_amount
    token = secrets.token_urlsafe(32)
    app.state.csrf_token = token
    import_secret = secrets.token_bytes(32)
    delete_secret = secrets.token_bytes(32)

    @app.middleware("http")
    async def private_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; img-src 'self'; connect-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/bank")

    def render_bank(
        request: Request,
        selected: date,
        *,
        failed_form: str | None = None,
        entered: dict[str, str] | None = None,
        form_error: str | None = None,
        schedule_rows: tuple[dict[str, str], ...] = (),
        import_preview: tuple = (),
        import_token: str = "",
        delete_plan=None,
        delete_token: str = "",
        status_code: int = 200,
    ) -> HTMLResponse:
        """Redisplay the full page with safe, previously entered fields on errors."""
        if not db_path.is_file():
            data = {
                "accounts": (),
                "banks": (),
                "securities_accounts": (),
                "banks_by_id": {},
                "bank_accounts": (),
                "cash_locations": (),
                "account_labels": {},
                "opening": None,
                "bank_names": (),
                "custodian_names": (),
                "balances": {},
                "active_accounts": (),
                "controls": (),
                "control_default": "",
                "outlook": None,
                "holdings": (),
                "investment_rows": (),
                "matched_manual": (),
                "imported_positions": (),
                "imported_entries": (),
                "security_nominal_totals": (),
                "payments": (),
                "events": (),
                "warning": None,
                "as_of": selected,
            }
            missing = True
        else:
            with SQLiteCashRepository(db_path) as repo:
                data = _read_view(repo, selected)
            missing = False
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="bank_forms.html",
            context={
                **data,
                "csrf": token,
                "today": date.today(),
                "one_day": timedelta(days=1),
                "activation_default": max(
                    date.today(),
                    data["opening"].as_of + timedelta(days=1)
                    if data["opening"] is not None
                    else date.today(),
                ),
                "missing_db": missing,
                "failed_form": failed_form,
                "form_values": entered or {},
                "form_error": form_error,
                "schedule_rows": schedule_rows,
                "import_preview": import_preview,
                "import_token": import_token,
                "delete_plan": delete_plan,
                "delete_token": delete_token,
            },
            status_code=status_code,
        )

    @app.get("/bank/forms", response_class=HTMLResponse, include_in_schema=False)
    def internal_form_source(request: Request, as_of: str | None = None):
        """Existing audited form and preview markup; not a user-facing dashboard."""
        try:
            selected = date.fromisoformat(as_of) if as_of else date.today()
        except ValueError:
            return HTMLResponse("Invalid evaluation date", status_code=422)
        return render_bank(request, selected)

    @app.get("/bank", response_class=HTMLResponse)
    @app.get("/workspace", response_class=HTMLResponse, include_in_schema=False)
    def task_workspace(
        request: Request,
        area: str = "home",
        target: str | None = None,
        as_of: str | None = None,
    ):
        """The task-first UI is the single primary Fin interface."""
        if area not in {"home", "money", "assets", "due", "activity", "review"}:
            return HTMLResponse("Unknown workspace", status_code=404)
        try:
            selected = date.fromisoformat(as_of) if as_of else date.today()
        except ValueError:
            return HTMLResponse("Invalid evaluation date", status_code=422)
        missing = not db_path.is_file()
        if missing:
            data = {
                "accounts": (),
                "banks": (),
                "securities_accounts": (),
                "banks_by_id": {},
                "bank_accounts": (),
                "cash_locations": (),
                "account_labels": {},
                "balances": {},
                "active_accounts": (),
                "outlook": None,
                "investment_rows": (),
                "payments": (),
                "events": (),
                "controls": (),
                "opening": None,
                "warning": None,
            }
        else:
            with SQLiteCashRepository(db_path) as repo:
                data = _read_view(repo, selected)

        selected_bank = (
            next((bank for bank in data["banks"] if target == f"bank:{bank.id}"), None)
            if area == "money"
            else None
        )
        selected_securities_account = (
            next(
                (item for item in data["securities_accounts"] if target == f"securities:{item.id}"),
                None,
            )
            if area == "money"
            else None
        )
        selected_account = (
            next(
                (account for account in data["accounts"] if account.id == target),
                None,
            )
            if area == "money"
            else None
        )
        selected_asset = None
        if area == "assets" and target is not None:
            try:
                position = int(target)
                if 0 <= position < len(data["investment_rows"]):
                    selected_asset = data["investment_rows"][position]
            except (ValueError, TypeError):
                pass
        selected_payment = (
            next(
                (payment for payment in data["payments"] if payment["id"] == target),
                None,
            )
            if area == "due"
            else None
        )
        selected_event = (
            next(
                (event for event in data["events"] if event.id == target),
                None,
            )
            if area == "activity"
            else None
        )
        selected_control = (
            next(
                (control for control in data["controls"] if control["id"] == target),
                None,
            )
            if area == "review"
            else None
        )
        related_events = (
            tuple(
                event
                for event in data["events"]
                if any(leg.account_id == selected_account.id for leg in event.legs)
            )[:8]
            if selected_account
            else ()
        )
        related_payments = (
            tuple(
                payment
                for payment in data["payments"]
                if payment["holding"] == selected_asset.get("id")
            )
            if selected_asset and selected_asset.get("id")
            else ()
        )
        outstanding = tuple(
            payment for payment in data["payments"] if payment["outstanding"].amount > 0
        )
        overdue_count = sum(payment["due"] < selected for payment in outstanding)
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="workspace.html",
            context={
                **data,
                "area": area,
                "target": target,
                "selected_account": selected_account,
                "selected_bank": selected_bank,
                "selected_securities_account": selected_securities_account,
                "selected_asset": selected_asset,
                "selected_payment": selected_payment,
                "selected_event": selected_event,
                "selected_control": selected_control,
                "related_events": related_events,
                "related_payments": related_payments,
                "outstanding": outstanding,
                "overdue_count": overdue_count,
                "as_of": selected,
                "today": date.today(),
                "one_day": timedelta(days=1),
                "activation_default": max(
                    date.today(),
                    data["opening"].as_of + timedelta(days=1)
                    if data["opening"] is not None
                    else date.today(),
                ),
                "csrf": token,
                "missing_db": missing,
            },
        )

    @app.post("/bank/delete/preview", response_class=HTMLResponse)
    async def preview_delete(request: Request):
        """Show exact dependent records and cash effects before any deletion."""
        form = await request.form()
        supplied = form.get("csrf")
        if not isinstance(supplied, str) or not secrets.compare_digest(supplied, token):
            return HTMLResponse("Forbidden", status_code=403)
        if not db_path.is_file():
            return HTMLResponse("Initialize the database first: fin db init", status_code=409)
        raw_date = request.query_params.get("as_of")
        try:
            as_of = date.fromisoformat(raw_date) if raw_date else date.today()
        except ValueError:
            return HTMLResponse("Invalid evaluation date", status_code=422)
        try:
            with SQLiteCashRepository(db_path) as repo:
                plan = repo.plan_delete(_field(form, "kind"), _field(form, "identifier"))
            return render_bank(
                request,
                as_of,
                delete_plan=plan,
                delete_token=sign_deletion(plan, delete_secret),
            )
        except (ValueError, TypeError, sqlite3.Error) as exc:
            return render_bank(request, as_of, form_error=str(exc), status_code=422)

    @app.post("/bank/delete/confirm", response_class=HTMLResponse)
    async def confirm_delete(request: Request):
        """Explicit typed confirmation; backup first; verify current DB fingerprint."""
        form = await request.form()
        supplied = form.get("csrf")
        if not isinstance(supplied, str) or not secrets.compare_digest(supplied, token):
            return HTMLResponse("Forbidden", status_code=403)
        if not db_path.is_file():
            return HTMLResponse("Initialize the database first: fin db init", status_code=409)
        raw_date = request.query_params.get("as_of")
        try:
            as_of = date.fromisoformat(raw_date) if raw_date else date.today()
        except ValueError:
            return HTMLResponse("Invalid evaluation date", status_code=422)
        try:
            if _field(form, "confirmation") != "УДАЛИТЬ":
                raise ValueError("Для удаления напиши УДАЛИТЬ полностью")
            kind, identifier, digest = verify_deletion(
                _field(form, "deletion_token"), delete_secret
            )
            with SQLiteCashRepository(db_path) as repo:
                reviewed = repo.plan_delete(kind, identifier)
            if not secrets.compare_digest(reviewed.digest, digest):
                raise ValueError("Данные изменились. Сначала проверь удаление заново.")
            _backup_before_edit(db_path)
            with SQLiteCashRepository(db_path) as repo:
                repo.delete_with_plan(kind, identifier, digest)
            anchors = {
                "account": "accounts",
                "cash": "accounts",
                "opening": "accounts",
                "cash_event": "events",
                "control": "monthly-controls",
                "holding": "holdings",
                "payment": "payments",
                "receipt": "payments",
                "securities_import": "imported-securities",
                "import_position": "holdings",
            }
            return RedirectResponse(
                f"/bank?as_of={as_of.isoformat()}#{anchors[kind]}",
                status_code=303,
            )
        except (ValueError, TypeError, sqlite3.Error, OSError) as exc:
            return render_bank(request, as_of, form_error=str(exc), status_code=422)

    @app.post("/bank/securities/preview", response_class=HTMLResponse)
    async def preview_securities(request: Request):
        """Read PDFs in memory, discard depositor details, show only safe facts."""
        form = await request.form()
        supplied = form.get("csrf")
        if not isinstance(supplied, str) or not secrets.compare_digest(supplied, token):
            return HTMLResponse("Forbidden", status_code=403)
        if not db_path.is_file():
            return HTMLResponse("Initialize the database first: fin db init", status_code=409)
        try:
            files = form.getlist("documents")
            if not 1 <= len(files) <= 16:
                raise ValueError("Выбери от 1 до 16 PDF выписок")
            entries = []
            for file in files:
                if not isinstance(file, UploadFile):
                    raise ValueError("Ожидается PDF-файл")
                payload = await file.read(8 * 1024 * 1024 + 1)
                entries.append(parse_securities_pdf(payload))
            if len({entry.source_hash for entry in entries}) != len(entries):
                raise ValueError("Одна и та же выписка выбрана повторно")
            items = tuple(entries)
            return render_bank(
                request,
                date.today(),
                import_preview=items,
                import_token=sign_preview(items, import_secret),
            )
        except ValueError as exc:
            return render_bank(request, date.today(), form_error=str(exc), status_code=422)

    @app.post("/bank/securities/confirm", response_class=HTMLResponse)
    async def confirm_securities(request: Request):
        """Require signed preview + explicit user approval; never persist raw PDFs."""
        form = await request.form()
        supplied = form.get("csrf")
        if not isinstance(supplied, str) or not secrets.compare_digest(supplied, token):
            return HTMLResponse("Forbidden", status_code=403)
        if not db_path.is_file():
            return HTMLResponse("Initialize the database first: fin db init", status_code=409)
        try:
            preview = verify_preview(_field(form, "preview_token"), import_secret)
            _backup_before_edit(db_path)
            with SQLiteCashRepository(db_path) as repo:
                added = repo.import_securities_entries(preview)
        except (ValueError, TypeError, sqlite3.Error, OSError) as exc:
            return render_bank(request, date.today(), form_error=str(exc), status_code=422)
        return RedirectResponse(
            f"/bank?as_of={max(entry.as_of for entry in preview).isoformat()}&imported={added}#holdings",
            status_code=303,
        )

    @app.post("/bank/{kind}", response_class=HTMLResponse)
    async def submit(request: Request, kind: str):
        if kind not in _KINDS:
            return HTMLResponse("Unknown form", status_code=404)
        form = await request.form()
        supplied_token = form.get("csrf")
        if not isinstance(supplied_token, str) or not secrets.compare_digest(supplied_token, token):
            return HTMLResponse("Forbidden", status_code=403)
        if not db_path.is_file():
            return HTMLResponse("Initialize the database first: fin db init", status_code=409)
        try:
            if kind.startswith("edit_"):
                _backup_before_edit(db_path)
            with SQLiteCashRepository(db_path) as repo:
                _submit(repo, kind, form)
        except (ValueError, TypeError, sqlite3.Error, OSError) as exc:
            entered = {
                name: value
                for name, value in form.items()
                if name != "csrf" and isinstance(value, str) and len(value) <= 2048
            }
            rows = tuple(
                {"kind": k, "on": d, "amount": a}
                for k, d, a in zip(
                    form.getlist("schedule_kind"),
                    form.getlist("schedule_on"),
                    form.getlist("schedule_amount"),
                    strict=False,
                )
                if all(isinstance(v, str) and len(v) <= 2048 for v in (k, d, a))
            )
            return render_bank(
                request,
                date.today(),
                failed_form=kind,
                entered=entered,
                form_error=str(exc),
                schedule_rows=rows[:100],
                status_code=422,
            )
        as_of = request.query_params.get("as_of")
        target = "/bank"
        if as_of:
            try:
                date.fromisoformat(as_of)
            except ValueError:
                pass
            else:
                target += "?as_of=" + as_of
        return RedirectResponse(target, status_code=303)

    return app


def run_local(db_path: str | Path, *, port: int = 8765) -> None:
    """Start loopback-only HTTP; never expose a passwordless finance UI on 0.0.0.0."""
    import uvicorn

    uvicorn.run(
        create_app(db_path),
        host="127.0.0.1",
        port=port,
        access_log=False,
    )
