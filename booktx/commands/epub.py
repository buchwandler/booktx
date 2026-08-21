"""Typer commands for read-only built-EPUB inspection."""

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
from booktx.workflows.epub import EpubDocument, select_epub_documents

epub_app = typer.Typer()


def _documents_or_return(proj, chapter: str | None) -> list[EpubDocument] | None:
    try:
        return select_epub_documents(proj, chapter)
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return None


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
    """Inspect XHTML directly from the built EPUB archive."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    documents = _documents_or_return(runtime.project, chapter)
    if documents is None:
        return
    for document in documents:
        text = document.read_text()
        if contains is not None and contains.lower() not in text.lower():
            continue
        console.print(f"--- {document.name} ---")
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
    """Search XHTML directly inside the built EPUB archive."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    documents = _documents_or_return(runtime.project, None)
    if documents is None:
        return
    for document in documents:
        try:
            text = document.read_text()
            for lineno, line in enumerate(text.splitlines(), start=1):
                if text_pattern.lower() in line.lower():
                    console.print(
                        f"{document.name}:{lineno}: {line.strip()}",
                        soft_wrap=True,
                        markup=False,
                    )
        except (BooktxError, OSError, UnicodeError) as exc:
            console.print(f"error reading {document.name}: {exc}")


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
    """Extract plain text from XHTML in the built EPUB archive."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    documents = _documents_or_return(runtime.project, chapter)
    if documents is None:
        return
    for document in documents:
        text = document.read_text()
        stripped = re.sub(r"<[^>]+>", "", text)
        console.print(stripped.strip(), soft_wrap=True, markup=False)
