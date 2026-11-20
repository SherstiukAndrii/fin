"""Smoke tests: Fin can be installed and launched without network access."""

import subprocess
import sys

from fin.cli import main


def test_scaffold_runs_without_starting_a_server(capsys) -> None:
    assert main([]) == 0
    assert "not implemented yet" in capsys.readouterr().out


def test_module_entry_point_reports_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "fin", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "fin 0.1.0"
