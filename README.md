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

## Checks before a PR

```bash
ruff check .
ruff format --check .
python -m pytest -v
```

GitHub Actions runs the same checks with Python 3.12 and 3.13 for pull requests targeting `master`. Keep real account data, statements, tokens and credentials out of the repository, tests and PR descriptions.

## Project layout

- `src/fin/` — installed Python package; financial domain and app layers come later.
- `tests/` — automated smoke tests; later tests will cover money, dates, FX and investments.
- `docs/` — approved financial rules and future modules.
- `.github/workflows/ci.yml` — lint, formatting and test checks.
- `fin.code-workspace` — existing VS Code workspace for use with WSL.

Development flow: small feature branch → Draft PR → automated checks and manual review → user-controlled squash merge. Do not commit `.venv`, generated files or private financial data.
