"""Typer commands for built-EPUB XHTML inspection (Phase 3 slice 3).

Thin command layer for ``epub inspect / grep / extract-text``. Each command
loads the runtime/project via the shared CLI helper, resolves the output dir +
XHTML files via :mod:`booktx.workflows.epub`, reads the (read-only) XHTML, and
renders the result. ``proj.output_dir`` (not ``proj.paths.output_dir``) is used
throughout.
"""

from __future__ import annotations

import re
from pathlib import Path

import typer

from booktx.cli_support import (
    _handle_booktx_error,
    _load_runtime_or_exit,
    console,
)
from booktx.errors import BooktxError
from booktx.workflows.epub import resolve_epub_output_dir, select_xhtml_files

epub_app = typer.Typer()


@epub_app.command(name="inspect")
def epub_inspect_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    chapter: str | None = typer.Option(
        None, "--chapter", help="Chapter id to inspect."
    ),
    contains: str | None = typer.Option(
        None, "--contains", help="Only show content containing this text."
    ),
) -> None:
    """Inspect built EPUB XHTML output."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    proj = runtime.project
    try:
        output_dir = resolve_epub_output_dir(proj)
        xhtml_files = select_xhtml_files(output_dir, chapter)
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return

    for xhtml_path in xhtml_files:
        text = xhtml_path.read_text("utf-8", errors="replace")
        if contains is not None and contains.lower() not in text.lower():
            continue
        console.print(f"--- {xhtml_path.name} ---")
        if contains is not None:
            for line in text.splitlines():
                if contains.lower() in line.lower():
                    console.print(line.strip(), soft_wrap=True, markup=False)
        else:
            console.print(text[:2000], soft_wrap=True, markup=False)
            if len(text) > 2000:
                console.print("... (truncated)")


@epub_app.command(name="grep")
def epub_grep_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    text_pattern: str = typer.Argument(..., help="Text to search for."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
) -> None:
    """Grep built EPUB XHTML output for text."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    proj = runtime.project
    try:
        output_dir = resolve_epub_output_dir(proj)
        xhtml_files = select_xhtml_files(output_dir)
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return

    for xhtml_path in xhtml_files:
        try:
            text = xhtml_path.read_text("utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if text_pattern.lower() in line.lower():
                    rel = xhtml_path.relative_to(output_dir)
                    console.print(
                        f"{rel}:{lineno}: {line.strip()}",
                        soft_wrap=True,
                        markup=False,
                    )
        except Exception as exc:
            console.print(f"error reading {xhtml_path.name}: {exc}")


@epub_app.command(name="extract-text")
def epub_extract_text_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    chapter: str | None = typer.Option(
        None, "--chapter", help="Chapter id to extract text from."
    ),
) -> None:
    """Extract plain text from built EPUB XHTML."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    proj = runtime.project
    try:
        output_dir = resolve_epub_output_dir(proj)
        xhtml_files = select_xhtml_files(output_dir, chapter)
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return

    for xhtml_path in xhtml_files:
        text = xhtml_path.read_text("utf-8", errors="replace")
        stripped = re.sub(r"<[^>]+>", "", text)
        console.print(
            stripped.strip(),
            soft_wrap=True,
            markup=False,
        )
