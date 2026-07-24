"""Stable, dependency-light console bootstrap for booktx."""

from __future__ import annotations

import os
import sys
import traceback
from typing import TextIO

STARTUP_ERROR_EXIT_CODE = 70


def render_startup_failure(
    exc: BaseException,
    *,
    stream: TextIO | None = None,
    debug: bool | None = None,
) -> None:
    """Render an actionable startup error without importing the CLI stack."""
    output = stream or sys.stderr
    show_traceback = (
        os.environ.get("BOOKTX_DEBUG") == "1" if debug is None else debug
    )
    output.write(
        "booktx could not start.\n\n"
        "Startup error:\n"
        f"  {type(exc).__name__}: {exc}\n\n"
        "No project or profile data was modified.\n"
        "Run the repository CLI-import checks from the booktx checkout:\n"
        "  python -m pytest -q tests/test_command_catalog.py "
        "tests/test_import_health.py\n\n"
        "Set BOOKTX_DEBUG=1 to print the full traceback.\n"
    )
    if show_traceback:
        output.write("\nFull startup traceback:\n")
        traceback.print_exception(exc, file=output)


def main() -> None:
    """Load the full CLI lazily and contain import-time startup failures."""
    try:
        from booktx.cli import main as cli_main
    except Exception as exc:
        render_startup_failure(exc)
        raise SystemExit(STARTUP_ERROR_EXIT_CODE) from None
    cli_main()


__all__ = ["STARTUP_ERROR_EXIT_CODE", "main", "render_startup_failure"]
