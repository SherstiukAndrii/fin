"""Minimal command line entry point for the development scaffold."""

import argparse
from collections.abc import Sequence
from importlib.metadata import version


def main(argv: Sequence[str] | None = None) -> int:
    """Show scaffold status without starting a server or accessing financial data."""
    parser = argparse.ArgumentParser(prog="fin", description="Fin development scaffold")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('fin-tools')}")
    parser.parse_args(argv)
    print("Fin scaffold is installed. Bank and Overview are not implemented yet.")
    return 0
