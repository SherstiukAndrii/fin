# Fin

Single-user, offline-first personal finance app for Windows and Linux. The planned MVP contains **Bank** and **Overview**; see [MVP requirements](docs/mvp.md) and the [domain model](docs/domain-model.md).

**Current state:** Python scaffold, exact `Money`, tested Bank-domain models and **SQLite Bank/Cash persistence v5**. Actual cash ledger and dated opening snapshot are available in Python. First local Bank browser UI is available; Overview/FX still to come. Future investment calculators and statement analysis are documented under `docs/`.

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
   - **Fin: DB Init** — create/migrate `~/.local/share/fin/fin.sqlite3`, outside the repository (safe to rerun).
   - **Fin: DB Status** — read-only schema and integrity status of the local database.
   - **Fin: Web** — run the Bank browser UI on `http://127.0.0.1:8765` (first run **Fin: DB Init**).
   - **Fin: Tests** — run `pytest -v`, also the default **Run Test Task**.
   - **Fin: Ruff Check** — run `ruff check .` (lint).
   - **Fin: Ruff Format Check** — run `ruff format --check .` (non-mutating format verification).
   - **Fin: Ruff Format** — run `ruff format .` to format files.
   - **Fin: Check** — run lint, format check, and tests sequentially; also the default **Run Build Task** (`Ctrl+Shift+B`).
   - **Fin: Setup** — idempotent WSL venv setup; **Fin: Install** — run Setup and install/update `.[dev]`; **Fin: Bootstrap** — Install + DB Init.
4. Read results in the integrated terminal. **Fin: Check** should pass before opening or merging a PR.

All shared VS Code project settings and tasks live in **`fin.code-workspace`** (not `.vscode/tasks.json`). Open that workspace in WSL. Task commands use `.venv/bin/python` and `.venv/bin/ruff` explicitly, so shell activation is unnecessary. If those executables are missing, first run **Fin: Setup**, then **Fin: Install**.

## Checks before a PR

Run **Fin: Check** (or `Ctrl+Shift+B`) in VS Code. It executes the same lint, format-check, and pytest commands used by GitHub Actions.

GitHub Actions runs the same checks with Python 3.12, 3.13 and 3.14 for pull requests targeting `master`. Keep real account data, statements, tokens and credentials out of the repository, tests and PR descriptions.

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

A coupon never reduces principal, a maturity date does not imply an extra coupon, and a planned payment is **not** booked cash. Cost basis is not market value. Receipt overpayments are preserved and shown separately from the plan; duplicate receipt IDs are rejected. No automatic reinvestment, interest generation or tax calculations.

Run **Fin: Check** in `fin.code-workspace` before review. SQLite storage, account movements and valuations are separate future PRs.

## SQLite Bank persistence

`fin.persistence.SQLiteBankRepository` saves bond instruments/lots, deposits, explicit scheduled payments and confirmed receipts in a local SQLite file. It creates **schema version 5** on first open, preserves data when reopened, rejects unsupported future versions and enables foreign keys on every connection. `Decimal` amounts are stored as **TEXT**, not floating-point SQLite REAL. Unknown purchase prices/fees stay `NULL`, not zero.

```python
from pathlib import Path

from fin.persistence import SQLiteBankRepository

Path("data").mkdir(exist_ok=True)
with SQLiteBankRepository(Path.home() / ".local/share/fin/fin.sqlite3") as repository:
    assert repository.schema_version() == 5
    # repository.add_bond_lot(lot)
    # repository.add_payment(scheduled_payment)
    # repository.add_receipt(actual_receipt)
```

Create the parent directory before opening a file-based database. Files under `data/` and `*.sqlite3` / `*.db` are excluded from Git. Use the existing **Fin: Check** VS Code task (`Ctrl+Shift+B`): there is no separate install command or third-party DB dependency.

### Initialize and inspect the database

In VS Code open `fin.code-workspace` in WSL → **Terminal → Run Task… → Fin: DB Init**. This creates `~/.local/share/fin/fin.sqlite3` and applies the existing schema migrations. The task is safe to rerun. Use **Fin: DB Status** to inspect the path, schema version and read-only integrity checks.

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

## Monthly cash reconciliation (SQLite v4)

Bank lets you save one month-end control snapshot containing **observed cash for every
account** (including explicit zeroes), with the calculated account balances saved
as immutable comparison evidence. The snapshot does **not** change the actual
cash ledger. Review each difference separately and, if it is real, enter a reason
and confirm the suggested signed adjustment. Fin posts exactly one linked
`correction` event on that month-end date; it cannot be repeated or edited
through the ordinary cash-event form. A stale confirmation is rejected if the
ledger has changed since the page was displayed; refresh before approving.

- Observations are end-of-day closing values on the last calendar day of the
  month, strictly after the initial opening snapshot. The next full snapshot is
  saved separately; no earlier observation is silently replaced.
- Reconciliation is per account and per currency; there is no implicit FX and
  no automatic booking of coupons, principal or other planned payments.
- SQLite migrates atomically from v1/v2/v3 to v4, preserving existing balances,
  investment schedules and cash events. Run `Fin: DB Init` to apply the migration
  after backing up your private database, then `Fin: DB Status` to verify it.
- Changes to older events can alter historical calculated balances. Fin retains
  the originally captured calculation and displays the current calculation
  separately rather than rewriting the evidence.

## Cash accounts, opening snapshot and dated movements (SQLite v2)

The offline `fin.domain.accounts` module defines `CashAccount`, `OpeningSnapshot`, `CashEvent` and `actual_balances`. `fin.persistence.SQLiteCashRepository` uses the **same SQLite file** as `SQLiteBankRepository`. Existing v1 databases migrate in place to v2; bond lots, deposits and payment histories are retained.

- Create all cash/bank accounts first and save one **end-of-day** opening snapshot, with an amount for every account (including confirmed zero). The initial balances already include any earlier purchases. This first iteration does not support changing the opening snapshot or adding accounts after it; those workflows require explicit later rules.
- Post dated actual cash movements: external inflows/outflows, own-account transfers (two offsetting legs, same currency), auditable corrections (an explanation is required), and individual **confirmed receipt** events. Movements on the opening day are not replayed. Actual balances are computed from the opening state and confirmed **later** entries only.
- A scheduled coupon/interest/principal does **not** credit cash automatically. Even an `ActualReceipt` remains separate from account cash until a `CashEvent(kind=RECEIPT)` explicitly links its `receipt_id`, matching date, amount and currency. A unique SQL constraint prevents linking a receipt twice. An unrelated manual inflow is not automatically matched to investments; do not enter the same real-world receipt as both.
- Different account currencies are never added or converted implicitly. Cross-currency transfers, liability effects and automated posting for sales/purchases remain later work.

### Example: initial snapshot and actual cash

```python
from datetime import date
from decimal import Decimal
from pathlib import Path

from fin.domain.accounts import AccountKind, CashAccount, OpeningBalance, OpeningSnapshot
from fin.domain.money import Money
from fin.persistence import SQLiteCashRepository

with SQLiteCashRepository(Path.home() / ".local/share/fin/fin.sqlite3") as db:
    db.add_account(CashAccount("wallet", "Cash", "UAH", AccountKind.CASH))
    db.save_opening_snapshot(
        OpeningSnapshot(
            date(2027, 1, 1),
            (OpeningBalance("wallet", Money(Decimal("100.00"), "UAH")),),
        )
    )
    print(db.balances_on(date(2027, 1, 1)))
```

The example assumes a **new empty database** with an existing `data/` folder. For a real existing database, use its own accounts and opening snapshot, and do not run the example twice. Local financial databases and their SQLite sidecars are excluded from Git, **not** protected from filesystem deletion. Backup/restore comes later.


## First Bank browser screen (local-only)

From the repository in WSL, run **Fin: Install** after switching branches (new dependencies), then **Fin: DB Init** and **Fin: Web** via `fin.code-workspace`. Visit **http://127.0.0.1:8765/bank** from the same machine. Equivalent shell commands:

```bash
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m fin db init
.venv/bin/python -m fin web
# Optional: .venv/bin/python -m fin web --path ~/private/fin.sqlite3 --port 8766
```

Failed form submissions now keep all entered fields and show validation errors inline; no need to retype an entire OVGZ lot. TestClient uses the supported `httpx2` dev dependency, checked in CI on Python 3.12, 3.13 and 3.14.

The UI runs **only on 127.0.0.1** (loopback), never 0.0.0.0. It has no login and is **not safe to expose directly to public internet or a VPN**. Cross-origin form posting uses an in-memory CSRF token, a restricted Host header, no-store and restrictive browser headers; all HTML inputs are escaped by Jinja2. These are local-development protections, not full authentication.

Bank currently supports creating accounts **before** an immutable dated opening snapshot, entering a complete opening balance for every account, and adding actual inflows/outflows, same-currency transfers and explained corrections. Once the opening snapshot exists, adding new cash accounts is deferred until explicit rules are implemented. You can add bond lots, deposits, whole-lot scheduled coupon/interest/principal payments and separate actual receipts. A receipt must be **explicitly credited** to a cash account before it changes actual cash. **Investments do not auto-debit cash**: record relevant post-opening purchase outflows separately, and do not repeat purchases already included in the initial snapshot.

No market prices, FX conversion, net-worth Total or backup workflow yet. The UI uses the existing SQLite file; no real account data is stored in Git or tests. Make a backup before entering real data. `git clean -fdx` can delete local private files, even if they are ignored by Git.


## Bank UI updates: editing and safe storage

After updating to this PR, use **Fin: Bootstrap** in `fin.code-workspace` once,
then **Fin: Web**. Separate **Fin: Setup** is idempotent: it keeps an existing
`.venv`; **Fin: Install** runs Setup first and upgrades project dependencies.
The UI has editable accounts, opening snapshot, cash events, bond lots,
deposits, schedules and not-yet-credited actual receipts. Each edit creates a
**timestamped backup** in `~/.local/share/fin/backups/` before writing.
Already-credited investment receipts remain immutable; use an explicit cash
correction rather than changing historical evidence.

The web page formats `120000000` as `120 000 000 (120 млн)`, keeps exact
Decimal values for storage, and generates IDs automatically. Planned payments
are explicitly linked to a recognizable holding and dated event; a coupon
means interest from bonds, while an actual receipt is a confirmed bank credit.
Browser form submission is asynchronous, with inline error messages; JavaScript
prevents page jumps while preserving typed values.

Deposit bank selection is a **dropdown of existing bank-account names**, with
**Другой банк** for institutions not represented by a cash account. Selection
stores the bank's display name on the deposit; it does **not** transfer cash
or link investment principal automatically. Monetary input fields and bond
quantities show grouped digits as you type (e.g. `1 234 567.89`). The backend
accepts the grouped value and saves the exact Decimal without rounding.
Editing forms use the same formatting. New opening snapshots, cash movements,
bond purchases, deposit openings and actual receipts default their dates to
the current local day; contractual maturity and planned-payment dates must
be entered explicitly.

**One-step investment entry:** In the **Add bond / Add deposit** form,
optionally list as many expected payment rows as needed (date, coupon /
interest / principal, amount for the entire bond lot or entire deposit).
Click **+ Добавить выплату** for additional rows. Saving creates the
investment and all explicit scheduled payments in one SQLite transaction:
an invalid date, mismatched payment type, or excess scheduled principal
rolls back the whole entry. Empty optional rows are skipped. No coupons,
dates, or maturity amounts are automatically guessed from a rate; missing
payment terms can be added later from the separate payment form. Planned
payments never debit or credit the actual cash ledger.

**Automatic maturity principal:** the combined bond/deposit form always creates the
final **scheduled principal** using the stated maturity date and contractual
principal (`bond face value × quantity` or `deposit principal`). Do not enter
principal twice. Existing positions are *not* backfilled with invented payments
during the atomic SQLite v2→v3 migration. Editing an auto-generated principal
directly is blocked; for deposits, editing maturity/principal adjusts it
automatically unless an actual receipt has already been recorded.

**Custody provider per OVGZ lot:** the bond form selects an existing bank or a
custom bank/broker/depository (e.g. Freedom24); different lots of the same ISIN
can have different custody institutions. Old bonds migrate with custody
**unspecified**; neither holdings nor cash are reassigned.

**Actual vs planned amounts:** actual receipts can be smaller, larger or split
over multiple dates. The Bank calendar displays any overpayment explicitly
(`Сверх плана`) without changing the original planned amount. Actual cash
is still credited only by a separately linked confirmed receipt event.

### Data protection / git clean

The default SQLite path is **`~/.local/share/fin/fin.sqlite3`** (or
`$XDG_DATA_HOME/fin/fin.sqlite3`; optional `$FIN_DB_PATH` environment
override), **outside this Git checkout**. Running `Fin: DB Init` copies an
existing legacy `data/fin.sqlite3` to the new path with SQLite's online backup,
without deleting or overwriting the legacy file. It initializes a new database
only when no existing database is found. `git clean -fdx` can still delete
untracked files inside the repository (including `data/fin.sqlite3` and
`.venv`), so do not keep your only copy there.

Old staging tables in existing SQLite databases are left untouched by this
cleanup. No financial records or user databases are deleted.

## Offline import of securities account statements (SQLite v5)

The Bank screen supports uploading up to 16 text-layer PDF statements from the
recognized securities institutions. The PDF is processed **in memory**; neither
its raw bytes nor depositor names, tax IDs, passport numbers, account identifiers,
signatures or trade-contract references are saved. Only document/event
fingerprints and normalized custodian, ISIN, date, quantity, record kind and
nominal-per-unit (when explicitly present) are stored locally in SQLite v5.
Public Git and test fixtures contain only fictional values.

Use **Preview** to review all parsed rows. **Confirm import** explicitly writes
them after a separate SQLite backup outside the repository. Reimporting the same
PDF or the same recognized source event is idempotent. A closing-balance report
is authoritative for its custodian/ISIN/date; post-snapshot movements alone may
change it. Movement-only reports show an *unconfirmed net change*, not a
verified portfolio balance. **Imported positions appear directly in the
Bank Investments ("Вложения") table**. A confirmed statement for the same
ISIN and exact custodian replaces the corresponding manual lot rows in the
main display; manual contract lots remain available in a separate expandable
editor for cost, maturity and payout terms. Movement-only reports never
override manually entered positions. Different custodians remain distinct.
Unknown purchase cost, coupon dates, maturity and market prices are never
guessed; the view never adds the same security twice. No cash posting or
scheduled payment is created by import.

The parser intentionally supports only verified layouts and rejects scanned
PDFs, unknown institutions, ambiguous ISINs or mismatched nominal totals.
Keep the entire workflow on localhost and never commit private statements.

## Explicit deletion and dependency safety

Bank supports deletion of cash accounts, the initial opening snapshot, cash
events, monthly reconciliation snapshots, bond/deposit holdings, scheduled
payments, actual receipts, imported PDF records, and entire imported securities
positions. Every Delete action starts with a **read-only preview** showing the
number of linked records and any potential effect on cash balances. A signed
preview tied to the exact current database state, the CSRF token and typing
**УДАЛИТЬ** are required before a deletion is accepted. Preview never changes
the ledger.

Before an approved deletion, Fin makes a private SQLite backup outside Git.
Deletion is atomic and checks SQLite foreign-key integrity. A change to the
selected records since preview invalidates confirmation. Historical data must
not be silently rewritten: deleting a payout removes its related actual receipts
and cash credits; deleting an investment removes its payout schedule and credits;
deleting a monthly reconciliation removes its linked manual correction. Deleting
a posted cash operation changes actual cash from its original date, while a
monthly observation survives deletion of its correction event and can be reviewed
again. A cash account with ledger or monthly reconciliation dependencies and an
opening snapshot with live cash history cannot be deleted until those
dependencies are intentionally removed. These operations are never automatic.

Imported securities positions and imported source statements can be removed
independently. Removing imported facts does not delete manually maintained
bond contracts or create financial transactions. The procedure does not
implement an undo button: restore a private backup if the deletion was a
mistake. Use the delete preview to inspect every affected record count first.

## Bank balances: accounts vs physical cash

The Bank **Money** workspace displays **bank accounts** and **physical cash on
hand** as separate kinds of holdings. Cash is not a bank account: creation,
editing, balances and deletion are clearly distinguished in the UI. To preserve
existing balances, transaction history and stable SQLite v5 migrations,
both remain typed money locations within the **internal** existing ledger:
`AccountKind.BANK` and `AccountKind.CASH`. Physical cash has no bank or
account number, defaults to the label "На руках", and is not offered in
bank-account creation. Existing cash records are shown under the cash section
without changing their identifiers or recorded amounts.

The opening snapshot and monthly reconciliations cover **both** banks and
cash. Transfers between cash and bank locations change each balance once,
with no net creation of money. Real cash includes both kinds, while planned
payouts and imported securities do not book cash. To avoid accidental removal
of cash under the bank-account deletion label, cash deletion has its own
signed preview type and uses the same backup and dependency checks.
No migration or data rewrite is performed.

## Local favicon

Fin ships a small transparent PNG favicon derived from the generated duck-sailor mascot, cropped for recognizability at 32 × 32 pixels. The icon loads locally from `/assets/favicon-32.png`, is included in the Python package and requires no external web service. The icon is illustrative and not intended as an official Disney/Donald Duck asset. This change is a separate, descriptive follow-up commit in PR #17, preserving prior review history.

## Bank workspace and date navigation

The Bank interface uses six focused workspaces: **Overview**, **Accounts**,
**Investments**, **Payments**, **Reconciliation** and **Transactions**. The
top-of-page date control is an **as-of viewing date**, not a posting date.
Choose an exact day, move one calendar month backward/forward, jump to today,
or show the end of the previous month. Financial figures are always calculated
for that selected date, per currency. Changing the date does not write data.

With JavaScript enabled, the browser keeps the date toolbar and navigation in
place while updating only the relevant data cards. Expanded top-level forms and
the selected workspace are preserved across successful requests; feedback
appears as a fixed toast instead of inserting new content into the page.
Deleting an entity opens a native modal with dependent-record counts, warnings
and the required typed confirmation. Escape or Cancel closes it without
posting anything. A no-JavaScript fallback retains ordinary forms and an
inline deletion preview. Deletion still uses the same signed preview, CSRF,
private backup, transactional database logic and financial invariants.

Both the stylesheet and JavaScript ship **locally** in the Python package.
No CDN, telemetry, cookies or external data source are required. The
JavaScript source is syntax-checked in CI; backend behavior and currency
calculations remain unchanged.

