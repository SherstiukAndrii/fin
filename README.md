# Fin

Single-user, offline-first personal finance app for Windows and Linux. The planned MVP contains **Bank** and **Overview**; see [MVP requirements](docs/mvp.md) and the [domain model](docs/domain-model.md).

**Current state:** Python scaffold, exact `Money`, tested Bank-domain models and **SQLite Bank persistence v1**. No live cash ledger, FX engine or web UI yet. Future investment calculators and statement analysis are documented under `docs/`.

## Development with Windows + WSL (Ubuntu) + VS Code

Use a WSL terminal in the repository root. Ensure `python3 --version` reports **3.12 or newer**, and install your distribution's Python venv package if needed.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m fin
python -m fin --version
```

The installed `fin` command is equivalent to `python -m fin`. Without subcommands it reports scaffold status; `fin db` manages local SQLite. It does **not** run a server.

## Run and test from VS Code (WSL)

1. In VS Code, open the **`fin.code-workspace` file in WSL** (not just the repository folder or a Windows-local window). From the repository root in a WSL terminal, run `code fin.code-workspace`, or use **File → Open Workspace from File…** in a WSL VS Code window.
2. In the WSL terminal, once per clone, set up the project:

   ```bash
   python3 -m venv .venv
   .venv/bin/python -m pip install -e ".[dev]"
   ```

3. From **Terminal → Run Task…** (or **Ctrl+Shift+P → Tasks: Run Task**) choose a project task:
   - **Fin: Run** — launch the current CLI scaffold (`python -m fin`), not a web server.
   - **Fin: DB Init** — create/migrate `data/fin.sqlite3` (safe to run again).
   - **Fin: DB Status** — read-only schema and integrity status of the local database.
   - **Fin: Tests** — run `pytest -v`, also the default **Run Test Task**.
   - **Fin: Ruff Check** — run `ruff check .` (lint).
   - **Fin: Ruff Format Check** — run `ruff format --check .` (non-mutating format verification).
   - **Fin: Ruff Format** — run `ruff format .` to format files.
   - **Fin: Check** — run lint, format check, and tests sequentially; also the default **Run Build Task** (`Ctrl+Shift+B`).
   - **Fin: Setup** — create the local `.venv` if missing; **Fin: Install** — install/update `.[dev]` into it.
4. Read results in the integrated terminal. **Fin: Check** should pass before opening or merging a PR.

All shared VS Code project settings and tasks live in **`fin.code-workspace`** (not `.vscode/tasks.json`). Open that workspace in WSL. Task commands use `.venv/bin/python` and `.venv/bin/ruff` explicitly, so shell activation is unnecessary. If those executables are missing, first run **Fin: Setup**, then **Fin: Install**.

## Checks before a PR

Run **Fin: Check** (or `Ctrl+Shift+B`) in VS Code. It executes the same lint, format-check, and pytest commands used by GitHub Actions.

GitHub Actions runs the same checks with Python 3.12 and 3.13 for pull requests targeting `master`. Keep real account data, statements, tokens and credentials out of the repository, tests and PR descriptions.

## Project layout

- `src/fin/` — installed Python package; financial domain and app layers come later.
- `tests/` — automated smoke tests; later tests will cover money, dates, FX and investments.
- `docs/` — approved financial rules and future modules.
- `.github/workflows/ci.yml` — lint, formatting and test checks.
- `fin.code-workspace` — single source of truth for VS Code settings, WSL Python interpreter, pytest discovery, run/test/lint/format/setup tasks.

Development flow: small feature branch → Draft PR → automated checks and manual review → user-controlled squash merge. Do not commit `.venv`, generated files or private financial data.

## Exact money amounts

# Money value object

Money calculations use exact `Decimal` inputs and explicit currency identifiers.
Mixed-currency addition/subtraction raises an error: an exchange rate must come
from a **separate dated FX service** (future PR). No implicit two-decimal rule.

```python
from decimal import Decimal, ROUND_HALF_UP

from fin.domain.money import Money

price = Money(Decimal("1.005"), "UAH")
posted = price.rounded(decimal_places=2, rounding=ROUND_HALF_UP)

assert price.amount == Decimal("1.005")
assert posted.amount == Decimal("1.01")
```

Rounding policies depend on the actual currency, instrument or payment context
and must be chosen by its caller. Never round intermediate calculations simply
because a bank account commonly displays two decimals.

This PR is limited to the shared Money primitive. FX, valuations, coupons,
taxes, database persistence and actual transaction posting are later work.

## Bank domain: scheduled versus actual payments

The pure, offline `fin.domain.bank` module introduces:

- `BondInstrument` and `BondLot`: one ISIN with lots, quantities, unit purchase cost and fees. Unknown price/fees remain `None` (unknown cost basis), never silently zero.
- `Deposit`: principal, dates and optional contractual annual rate (a decimal fraction).
- `BondPaymentTerm` (`amount_per_bond`) and `DepositPaymentTerm` (`amount_for_deposit`): explicitly entered contractual schedules. Convert them with `bond_schedule` / `deposit_schedule` to immutable `ScheduledPayment` records. Bond amounts multiply by lot quantity **once**; deposit amounts never do.
- `ActualReceipt` and `payment_balance`: reconcile multiple separately dated receipts to one scheduled obligation, with expected, partial, overdue and paid states.

A coupon never reduces principal, a maturity date does not imply an extra coupon, and a planned payment is **not** booked cash. Cost basis is not market value. Receipt overpayments and duplicate IDs are rejected pending a later explicit reconciliation mechanism. No automatic reinvestment, interest generation or tax calculations.

Run **Fin: Check** in `fin.code-workspace` before review. SQLite storage, account movements and valuations are separate future PRs.

## SQLite Bank persistence

`fin.persistence.SQLiteBankRepository` saves bond instruments/lots, deposits, explicit scheduled payments and confirmed receipts in a local SQLite file. It creates **schema version 1** on first open, preserves data when reopened, rejects unsupported future versions and enables foreign keys on every connection. `Decimal` amounts are stored as **TEXT**, not floating-point SQLite REAL. Unknown purchase prices/fees stay `NULL`, not zero.

```python
from pathlib import Path

from fin.persistence import SQLiteBankRepository

Path("data").mkdir(exist_ok=True)
with SQLiteBankRepository(Path("data/fin.sqlite3")) as repository:
    assert repository.schema_version() == 1
    # repository.add_bond_lot(lot)
    # repository.add_payment(scheduled_payment)
    # repository.add_receipt(actual_receipt)
```

Create the parent directory before opening a file-based database. Files under `data/` and `*.sqlite3` / `*.db` are excluded from Git. Use the existing **Fin: Check** VS Code task (`Ctrl+Shift+B`): there is no separate install command or third-party DB dependency.

### Initialize and inspect the database

In VS Code open `fin.code-workspace` in WSL → **Terminal → Run Task… → Fin: DB Init**. This creates `data/fin.sqlite3` and applies the existing schema migrations. The task is safe to rerun. Use **Fin: DB Status** to inspect the path, schema version and read-only integrity checks.

Equivalent commands (run from the repository root):

```bash
.venv/bin/python -m fin db init
.venv/bin/python -m fin db status
# For a custom path:
.venv/bin/python -m fin db init --path /path/to/fin.sqlite3
.venv/bin/python -m fin db status --path /path/to/fin.sqlite3
```

`fin db status` does **not** create the file or run migrations: it reports a missing database, unsupported version, incomplete schema or integrity problems with a nonzero exit code. The default path is relative to the current working directory; workspace tasks always use the repository root.

Schema migrations run in a single transaction; each repository write is atomic. This is **storage of contracts and receipts**, not a ledger that automatically credits account balances. The opening snapshot, ledger, backup/restore and UI arrive in later PRs. Do not delete the SQLite database with `git clean -fdx` or similar cleanup commands.
