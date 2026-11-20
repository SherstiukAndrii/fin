# Fin

Single-user, offline-first personal finance app for Windows and Linux. The planned MVP contains **Bank** and **Overview**; see [MVP requirements](docs/mvp.md) and the [domain model](docs/domain-model.md).

**Current state:** Python package scaffold only. No financial calculations, database or web UI yet. Future investment calculators and statement analysis are documented under `docs/`.

## Development with Windows + WSL (Ubuntu) + VS Code

Use a WSL terminal in the repository root. Ensure `python3 --version` reports **3.12 or newer**, and install your distribution's Python venv package if needed.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m fin
python -m fin --version
```

The installed `fin` command is equivalent to `python -m fin`. It only reports scaffold status; it does **not** run a server.

## Run and test from VS Code (WSL)

1. In VS Code, open the **`fin.code-workspace` file in WSL** (not just the repository folder or a Windows-local window). From the repository root in a WSL terminal, run `code fin.code-workspace`, or use **File → Open Workspace from File…** in a WSL VS Code window.
2. In the WSL terminal, once per clone, set up the project:

   ```bash
   python3 -m venv .venv
   .venv/bin/python -m pip install -e ".[dev]"
   ```

3. From **Terminal → Run Task…** (or **Ctrl+Shift+P → Tasks: Run Task**) choose a project task:
   - **Fin: Run** — launch the current CLI scaffold (`python -m fin`), not a web server.
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
