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

1. In VS Code, open the repository **in WSL**, not as a Windows-local folder. Use **Remote: Connect to WSL** (or `code .` from the WSL terminal). Open the repository folder or `fin.code-workspace`.
2. In the WSL terminal, once per clone, set up the project:

   ```bash
   python3 -m venv .venv
   .venv/bin/python -m pip install -e ".[dev]"
   ```

3. From the VS Code menu choose **Terminal → Run Task…** (or **Ctrl+Shift+P → Tasks: Run Task**):
   - **Fin: Run** — launch the current CLI scaffold (`python -m fin`). It does **not** start a web server yet.
   - **Fin: Tests** — run `pytest -v`. It is also the default VS Code **Run Test Task**.
4. Read the output in VS Code's integrated terminal. A nonzero exit code means the task failed; fix and rerun before PR review.

Tasks in `.vscode/tasks.json` use the repository's `.venv/bin/python` explicitly and require the WSL Linux environment. If VS Code says that `.venv/bin/python` is missing, repeat step 2 in the WSL repository root. The commands do not need activation with `source`.

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
