"""Typer CLI for spinetx.

Commands (see ``spinetx_coding_agent_start.md``)::

    spinetx init ./book --target de
    spinetx inspect ./book
    spinetx extract ./book
    spinetx next ./book
    spinetx validate ./book
    spinetx build ./book

spinetx never translates text; it extracts, validates, and rebuilds.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from spinetx import __version__
from spinetx.build import BuildError, build_project
from spinetx.chunking import spans_to_chunks
from spinetx.config import (
    SpinetxError,
    find_source_file,
    init_project,
    load_project,
)
from spinetx.epub_io import extract_epub
from spinetx.html_io import build_xhtml  # noqa: F401  (kept for downstream use)
from spinetx.markdown_io import extract_markdown
from spinetx.models import NamesFile
from spinetx.validate import validate_project, write_report

app = typer.Typer(
    name="spinetx",
    help=(
        "Prepare Markdown and EPUB documents for translation by a coding agent. "
        "spinetx does NOT translate text; it extracts, validates, and rebuilds."
    ),
    invoke_without_command=True,
    add_completion=False,
)

console = Console()


def _die(message: str, code: int = 1) -> None:
    """Print an error and exit with ``code``."""
    console.print(f"[red]error:[/red] {message}")
    raise typer.Exit(code=code)


def _handle_spinetx_error(exc: SpinetxError) -> None:
    _die(str(exc))


# --- version -----------------------------------------------------------------


@app.command()
def version() -> None:
    """Print the spinetx version."""
    console.print(__version__)


# --- init --------------------------------------------------------------------


@app.command()
def init(
    project_dir: Path = typer.Argument(..., help="Directory to create the project in."),
    target: str = typer.Option(
        ..., "--target", "-t", help="Target language code, e.g. de."
    ),
    source_lang: str = typer.Option(
        "en", "--source", "-s", help="Source language code (default: en)."
    ),
    source: Path | None = typer.Option(
        None,
        "--source-file",
        help="Optional source document to copy into <project>/source/.",
    ),
    chunk_size: int = typer.Option(
        50, "--chunk-size", help="Max records per chunk (default: 50)."
    ),
) -> None:
    """Create a new spinetx project layout."""
    try:
        proj = init_project(
            project_dir,
            target_language=target,
            source_language=source_lang,
            source_file=source,
            chunk_size=chunk_size,
        )
    except SpinetxError as exc:
        _handle_spinetx_error(exc)
        return

    console.print(f"[green]Created project:[/green] {proj.root}")
    console.print(f"  source_language: {proj.config.source_language}")
    console.print(f"  target_language: {proj.config.target_language}")
    console.print(f"  format:          {proj.config.format}")
    if proj.config.source_file:
        console.print(f"  source_file:     {proj.config.source_file}")
    else:
        console.print(
            "  [yellow]source/ is empty — drop a .md or .epub file into it.[/yellow]"
        )


# --- inspect -----------------------------------------------------------------


@app.command()
def inspect(
    project_dir: Path = typer.Argument(..., help="Project directory."),
) -> None:
    """Summarise the source document and how many records it would yield."""
    try:
        proj = load_project(project_dir)
        source = find_source_file(proj)
    except SpinetxError as exc:
        _handle_spinetx_error(exc)
        return

    fmt = proj.config.format
    names = _load_names_list(proj)
    record_count, extra = _count_records(source, fmt, names)

    table = Table(title=f"spinetx inspect — {proj.root}", show_header=False)
    table.add_row("source_file", source.name)
    table.add_row("format", fmt)
    table.add_row("source_language", proj.config.source_language)
    table.add_row("target_language", proj.config.target_language)
    table.add_row("estimated_records", str(record_count))
    table.add_row("protected_terms", ", ".join(names) if names else "(none)")
    table.add_row("details", extra)
    console.print(table)


def _load_names_list(proj) -> list[str]:
    from spinetx.config import load_names

    return load_names(proj).protected_terms


def _count_records(source: Path, fmt: str, names: list[str]) -> tuple[int, str]:
    if fmt == "markdown":
        text = source.read_text("utf-8")
        ext = extract_markdown(text, protected_terms=names)
        spans = ext.spans
        details = f"{len(spans)} prose span(s)"
    elif fmt == "epub":
        extraction = extract_epub(str(source), protected_terms=names)
        spans = extraction.spans
        details = f"{len(extraction.templates)} spine document(s)"
    else:  # pragma: no cover - config validation already guards this
        raise SpinetxError(f"Unsupported format {fmt!r}")

    from spinetx.chunking import segment_spans

    records = segment_spans(spans, language="en")
    return len(records), details


# --- extract -----------------------------------------------------------------


@app.command()
def extract(
    project_dir: Path = typer.Argument(..., help="Project directory."),
) -> None:
    """Extract translatable chunks into ``.spinetx/chunks/``.

    Idempotent: ``chunks/`` is rebuilt each run; ``translated/`` is left intact.
    """
    try:
        proj = load_project(project_dir)
        source = find_source_file(proj)
    except SpinetxError as exc:
        _handle_spinetx_error(exc)
        return

    names = _load_names_list(proj)
    fmt = proj.config.format
    if fmt == "markdown":
        text = source.read_text("utf-8")
        ext = extract_markdown(text, protected_terms=names)
        spans = ext.spans
    elif fmt == "epub":
        extraction = extract_epub(str(source), protected_terms=names)
        spans = extraction.spans
        # Persist per-document templates in the manifest so build can map back.
        _save_epub_manifest(proj, source, extraction)
    else:  # pragma: no cover
        _die(f"Unsupported format {fmt!r}")
        return

    chunks = spans_to_chunks(
        spans,
        source_language=proj.config.source_language,
        target_language=proj.config.target_language,
        chunk_size=proj.config.chunk_size,
    )

    # Idempotent rebuild of chunks/ — wipe and rewrite, keep translated/.
    proj.chunks_dir.mkdir(parents=True, exist_ok=True)
    for old in proj.chunks_dir.glob("*.json"):
        old.unlink()
    for chunk in chunks:
        (proj.chunks_dir / f"{chunk.chunk_id}.json").write_text(
            chunk.model_dump_json(indent=2), encoding="utf-8"
        )

    record_count = sum(len(c.records) for c in chunks)
    console.print(
        f"[green]Extracted[/green] {len(chunks)} chunk(s), "
        f"{record_count} record(s) into {proj.chunks_dir}"
    )


def _save_epub_manifest(proj, source, extraction) -> None:
    """Record epub templates and a source digest in manifest.json."""
    import hashlib
    import json

    from spinetx.config import write_manifest
    from spinetx.models import Manifest, ManifestSource

    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = Manifest(
        source=ManifestSource(
            filename=source.name,
            format="epub",
            source_language=proj.config.source_language,
            target_language=proj.config.target_language,
            sha256=sha,
        ),
        template={
            "documents": [
                {
                    "item_id": t.item_id,
                    "file_name": t.file_name,
                    "template": t.template,
                    "span_count": t.span_count,
                }
                for t in extraction.templates
            ]
        },
    )
    write_manifest(proj, manifest)
    # names file convenience: keep names.json in sync if user edited it.
    _ = (json, NamesFile)  # touch imports for clarity


# --- next --------------------------------------------------------------------


@app.command(name="next")
def next_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory."),
) -> None:
    """Print the first untranslated chunk and exit 0, or exit 1 when done.

    No files are written (no skeleton). Exit codes:
      0 — a chunk is ready to translate (its id + path printed).
      1 — every chunk is already translated.
    """
    try:
        proj = load_project(project_dir)
    except SpinetxError as exc:
        _handle_spinetx_error(exc)
        return

    chunk_ids = set(proj.chunk_ids())
    translated_ids = set(proj.translated_ids())
    pending = sorted(cid for cid in chunk_ids if cid not in translated_ids)
    if not pending:
        console.print(
            f"All {len(chunk_ids)} chunk(s) have translations in {proj.translated_dir}."
        )
        raise typer.Exit(code=1)

    cid = pending[0]
    chunk_path = proj.chunks_dir / f"{cid}.json"
    out_path = proj.translated_dir / f"{cid}.json"
    console.print(f"{cid}\t{chunk_path}")
    console.print(f"[dim]write translation to:[/dim] {out_path}")
    raise typer.Exit(code=0)


# --- validate ----------------------------------------------------------------


@app.command()
def validate(
    project_dir: Path = typer.Argument(..., help="Project directory."),
) -> None:
    """Validate translated chunks against the translation contract."""
    try:
        proj = load_project(project_dir)
    except SpinetxError as exc:
        _handle_spinetx_error(exc)
        return

    report = validate_project(proj)
    out = write_report(proj, report)

    if report.findings:
        for f in report.findings:
            color = "red" if f.severity == "error" else "yellow"
            loc = f" [{f.record_id}]" if f.record_id else ""
            console.print(
                f"[{color}]{f.severity}[/{color}] {f.chunk_id}{loc} "
                f"{f.rule}: {f.message}"
            )
    console.print(
        f"chunks_checked={report.chunks_checked} "
        f"passed={report.chunks_passed} "
        f"errors={len(report.errors)} warnings={len(report.warnings)} "
        f"missing={report.chunks_missing_translation}"
    )
    console.print(f"[dim]report:[/dim] {out}")
    if not report.passed:
        raise typer.Exit(code=1)


# --- build -------------------------------------------------------------------


@app.command()
def build(
    project_dir: Path = typer.Argument(..., help="Project directory."),
) -> None:
    """Rebuild the translated document into ``output/``."""
    try:
        proj = load_project(project_dir)
        result = build_project(proj)
    except SpinetxError as exc:
        _handle_spinetx_error(exc)
        return
    except BuildError as exc:
        _die(str(exc))
        return

    console.print(f"[green]Built[/green] {result.format} -> {result.output_path}")


# --- top-level callback (version) --------------------------------------------


@app.callback()
def _main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Print spinetx version and exit.",
        is_eager=True,
    ),
) -> None:
    """spinetx root options."""
    if version:
        console.print(__version__)
        raise typer.Exit


def main() -> None:
    """Console-script entry point (used by pyproject [project.scripts])."""
    # Typer raises typer.Exit for normal command exits; surface its code.
    try:
        app()
    except typer.Exit as exc:
        sys.exit(exc.exit_code)


__all__ = ["app", "main"]
