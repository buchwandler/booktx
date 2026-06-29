"""Typer commands for source-record inspection (Phase 3 slice 2).

Thin command layer for ``source status / record / chapter``. Each command loads
the runtime/project via the shared CLI helper, delegates data work to
:mod:`booktx.workflows.source`, renders the result, and maps
:class:`booktx.errors.BooktxError` to a non-zero exit.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from booktx.cli_support import (
    _die,
    _handle_booktx_error,
    _load_runtime_or_exit,
    _project_status_snapshot,
    console,
)
from booktx.errors import BooktxError
from booktx.workflows.source import (
    build_source_status_payload,
    collect_chapter_records,
    find_source_record,
)

source_app = typer.Typer(help="Inspect brokered source records without path leaks.")


def _validate_source_format(output_format: str) -> None:
    if output_format not in {"block", "text", "json"}:
        _die("--format must be block, text, or json")


@source_app.command(name="status")
def source_status_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory or profile root."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """Show a safe summary of extracted source state."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=False)
    proj = runtime.project
    payload = build_source_status_payload(proj)
    if as_json:
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return
    console.print(f"source: {payload['source']}")
    console.print(f"format: {payload['format']}")
    console.print(f"source language: {payload['source_language']}")
    console.print(f"records: {payload['records']}")
    console.print(f"chunks: {payload['chunks']}")
    console.print(f"chapters: {payload['chapters']}")


@source_app.command(name="record")
def source_record_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory or profile root."),
    record_ref: str = typer.Argument(..., help="Record id or record ref."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    output_format: str = typer.Option(
        "block",
        "--format",
        help="Output format: block, text, or json.",
    ),
) -> None:
    """Print one source record without exposing chunk paths."""
    _validate_source_format(output_format)
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=False)
    proj = runtime.project
    try:
        record = find_source_record(proj, record_ref)
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    payload = {"id": record.record_id, "source": record.source}
    if output_format == "json":
        console.print_json(json.dumps(payload, ensure_ascii=False))
    elif output_format == "text":
        console.print(f"{record.record_id}\t{record.source}")
    else:
        console.print(f">>> {record.record_id}")
        console.print(record.source)


@source_app.command(name="chapter")
def source_chapter_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory or profile root."),
    chapter_id: str = typer.Argument(..., help="Chapter id, e.g. 0001."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    output_format: str = typer.Option(
        "block",
        "--format",
        help="Output format: block, text, or json.",
    ),
) -> None:
    """Print all source records for one chapter without exposing chunk paths."""
    _validate_source_format(output_format)
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=False)
    proj = runtime.project
    bundle = _project_status_snapshot(proj)
    try:
        result = collect_chapter_records(bundle, chapter_id)
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    records = result.records
    if output_format == "json":
        console.print_json(
            json.dumps(
                {
                    "chapter_id": result.chapter_id,
                    "title": result.title,
                    "records": records,
                },
                ensure_ascii=False,
            )
        )
        return
    for item in records:
        if output_format == "text":
            console.print(f"{item['id']}\t{item['source']}")
        else:
            console.print(f">>> {item['id']}")
            console.print(item["source"])
            if item != records[-1]:
                console.print()
