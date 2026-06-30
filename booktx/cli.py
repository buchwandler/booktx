"""Typer CLI for booktx.

Commands (see ``booktx_coding_agent_start.md``)::

    booktx init ./book --target de
    booktx inspect ./book
    booktx extract ./book
    booktx next ./book
    booktx validate ./book
    booktx build ./book

booktx never translates text; it extracts, validates, and rebuilds.
"""

# ruff: noqa: E501

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError
from rich.table import Table

from booktx import __version__
from booktx.acceptance import (
    SubmissionValidationError,
    SubmittedRecord,
    accept_one_record,
    accept_translation_records,
)
from booktx.build import BuildError, build_project
from booktx.chapters import (
    ChapterMap,
    detect_chapters,
    load_chapter_map,
    write_chapter_map,
)
from booktx.chunking import RECORD_ID_SCHEME, segmenter_metadata, spans_to_chunks

# Shared CLI helpers (console, error/exit mapping, project loading). Lives in a
# neutral module so command modules under booktx/commands/ can import these
# without importing booktx.cli (which would create a cycle).
from booktx.cli_support import (
    _die,
    _handle_booktx_error,
    _load_project_or_exit,
    _load_runtime_or_exit,
    _maybe_auto_export_indexes,
    _print_identity,
    _project_relative,
    _project_status_snapshot,
    _reject_if_isolated,
    _render_finding,
    _render_profiles_overview_human,
    _render_submission_failures,
    _require_chunks,
    _require_no_source_drift,
    _require_ready_context,
    _selected_chapter,
    _staged_preflight_check,
    console,
)
from booktx.command_hints import (
    build_command,
    check_command,
    context_chapter_note_command,
    translate_next_command,
    translate_todo_resume_command,
    translate_todo_status_command,
)
from booktx.commands.context import context_app
from booktx.commands.epub import epub_app

# --- Phase 3 slice 1: identity command module --------------------------------
# Imported with the other first-party imports; the sub-apps are registered
# onto the root ``app`` further below. booktx/commands/identity.py delegates
# to booktx/workflows/identity.py and booktx/cli_support.py (never booktx.cli).
from booktx.commands.identity import (
    actor_app,
    harness_app,
    identity_app,
    model_app,
)
from booktx.commands.profile import profile_app
from booktx.commands.review import review_app
from booktx.commands.source import source_app
from booktx.commands.version import version_app
from booktx.config import (
    BooktxError,
    Project,
    _err,
    find_source_file,
    identity_path,
    init_project,
    load_identity,
    load_manifest,
    load_project,
    load_source_project,
    load_translation_store,
    load_translation_task,
    load_translation_version_ledger,
    project_source_sha256,
    protected_terms_sha256,
    translation_ingest_block_path,
    translation_ingest_path,
    translation_store_path,
    translation_task_path,
    translation_task_source_block_path,
    write_identity,
    write_translation_store,
)
from booktx.context import (
    context_markdown_path,
    load_context,
)
from booktx.editor_indexes import (
    EditorIndexError,
    EditorIndexesResult,
    export_editor_indexes,
)
from booktx.epub_io import EpubExtraction, extract_epub
from booktx.epub_manifest import EPUB2TEXT_SCHEMA, EPUB_TEMPLATE_PIPELINE
from booktx.html_io import build_xhtml  # noqa: F401  (kept for downstream use)
from booktx.markdown_io import extract_markdown
from booktx.models import (
    Chunk,
    Manifest,
    NamesFile,
    StoredTranslationRecordV2,
    TranslatedChunk,
    TranslatedRecord,
    TranslationCandidate,
    TranslationIdentity,
    TranslationReviewCandidate,
    TranslationStore,
    TranslationTask,
)
from booktx.pass_through import (
    ensure_pass_through_profile,
    run_pass_through,
)
from booktx.path_display import display_path
from booktx.progress import (
    SourceRecordView,
    load_source_chunks,
    load_source_records,
    source_record_sha256,
)
from booktx.record_refs import parse_record_ref, resolve_record_range
from booktx.runtime import RuntimeMode
from booktx.status import (
    ChapterProgress,
    StatusBundle,
    build_profiles_overview,
    build_status_snapshot,
)
from booktx.submissions import resolve_submission
from booktx.tasks import create_translation_task, select_translation_record_ids
from booktx.todo_resume import resolve_translation_todo, resume_translation_todo
from booktx.todo_status import (
    build_todo_status,
    current_todo_chapter_id,
    load_translation_todo,
)
from booktx.translation_store import (
    active_candidate,
    active_review_candidate,
    ensure_store_record,
    find_candidate,
    migrate_legacy_store,
    upsert_translation_version,
)
from booktx.validate import (
    Finding,
    Severity,
    ValidationReport,
    load_validation_context,
    strict_load_translated,
    validate_chunk_pair,
    validate_project,
    validate_record_pair,
    validation_exits_nonzero,
    write_report,
)
from booktx.versioning import (
    default_identity,
    lookup_version,
    resolve_current_version,
    resolve_identity,
)

app = typer.Typer(
    name="booktx",
    help=(
        "Prepare Markdown and EPUB documents for translation by a coding agent. "
        "booktx does NOT translate text; it extracts, validates, and rebuilds."
    ),
    invoke_without_command=True,
    add_completion=False,
)

translate_app = typer.Typer(help="Command-based translation workflow.")
doctor_app = typer.Typer(help="Diagnostic commands.")
app.add_typer(context_app, name="context")
app.add_typer(translate_app, name="translate")
app.add_typer(translate_app, name="translation")
app.add_typer(source_app, name="source")
app.add_typer(doctor_app, name="doctor")
app.add_typer(review_app, name="review")
app.add_typer(version_app, name="version")
app.add_typer(profile_app, name="profile")
app.add_typer(epub_app, name="epub")

# --- Phase 3 slice 1: identity (actor / harness / model / identity-whoami)
# commands registered below; cli.py stays the stable app-assembly entrypoint.
app.add_typer(actor_app, name="actor")
app.add_typer(harness_app, name="harness")
app.add_typer(model_app, name="model")
app.add_typer(identity_app, name="identity")


def _display_path(path: Path, mode: RuntimeMode | None) -> str:
    if mode is not None:
        return display_path(path, mode)
    return path.as_posix()


def _submission_ingest_hint(
    proj: Project,
    task_id: str | None,
    *,
    mode: RuntimeMode | None = None,
) -> str | None:
    """Project-relative path to the canonical profile-local ingest file.

    Used to point agents at the generated submission location when a
    ``--file``/``--json-file`` path is missing. Returns ``None`` when no
    profile is selected or the task id is unknown.
    """
    if proj.profile is None or not task_id:
        return None
    from booktx.config import translation_ingest_block_path

    return (
        display_path(translation_ingest_block_path(proj, task_id), mode)
        if mode is not None
        else _project_relative(translation_ingest_block_path(proj, task_id), proj.root)
    )


def _resolved_identity(proj: Project) -> TranslationIdentity:
    return resolve_identity(proj)


def _write_identity_defaults(
    proj: Project,
    *,
    actor: str | None = None,
    harness: str | None = None,
    model: str | None = None,
) -> TranslationIdentity:
    identity = resolve_identity(proj, actor=actor, harness=harness, model=model)
    write_identity(proj, identity)
    return identity


def _clear_identity_field(proj: Project, field_name: str) -> TranslationIdentity:
    current = load_identity(proj)
    fallback = default_identity()
    identity = TranslationIdentity(
        actor=current.actor if current is not None else fallback.actor,
        harness=current.harness if current is not None else fallback.harness,
        model=current.model if current is not None else fallback.model,
    )
    setattr(identity, field_name, getattr(fallback, field_name))
    if identity == fallback and identity_path(proj).is_file():
        identity_path(proj).unlink()
        return fallback
    write_identity(proj, identity)
    return identity


def _ordered_source_records(proj: Project) -> list[SourceRecordView]:
    return load_source_records(proj)


def _ledger_metadata_for_version(
    proj: Project, version_ref: str | None
) -> dict[str, Any] | None:
    if not version_ref:
        return None
    ledger = load_translation_version_ledger(proj)
    try:
        track, subversion = lookup_version(ledger, version_ref)
    except BooktxError:
        return None
    return {
        "version_ref": subversion.version_ref,
        "version": track.version,
        "subversion": subversion.subversion,
        "actor": track.actor,
        "harness": track.harness,
        "model": track.model,
        "label": track.label,
        "context_sha256": subversion.context_sha256,
        "baseline_sha256": subversion.baseline_sha256,
        "legacy_full_context_sha256": subversion.legacy_full_context_sha256,
        "context_label": subversion.context_label,
        "forced": subversion.forced,
    }


def _store_record_payload(
    proj: Project, record_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    ordered = _ordered_source_records(proj)
    by_id = {record.record_id: record for record in ordered}
    canonical_id = parse_record_ref(record_id).canonical_id
    source_record = by_id.get(canonical_id)
    if source_record is None:
        raise _err("unknown_record_id", f"unknown source record id: {record_id}")
    store = load_translation_store(proj)
    stored = store.records.get(canonical_id)
    versions: list[dict[str, Any]] = []
    active_version = None
    if stored is not None:
        active_version = stored.active_version
        for candidate in stored.versions:
            versions.append(
                {
                    "version": candidate.version,
                    "subversion": candidate.subversion,
                    "version_ref": candidate.version_ref,
                    "target": candidate.target,
                    "status": candidate.status,
                    "created_at": candidate.created_at,
                    "updated_at": candidate.updated_at,
                    "reviewed_at": candidate.reviewed_at,
                    "reviewed_by": candidate.reviewed_by,
                    "review_note": candidate.review_note,
                }
            )
    selected = {
        "id": canonical_id,
        "chunk_id": source_record.chunk_id,
        "source": source_record.source,
        "source_sha256": source_record.source_sha256,
        "active_version": active_version,
    }
    return selected, {"versions": versions, "store": store, "ordered": ordered}


# --- init --------------------------------------------------------------------


@app.command()
def init(
    project_dir: Path = typer.Argument(..., help="Directory to create the project in."),
    target: str | None = typer.Option(
        None, "--target", "-t", help="Optional target language code, e.g. de."
    ),
    profile: str | None = typer.Option(
        None, "--profile", help="Optional profile name to create when --target is used."
    ),
    source_lang: str = typer.Option(
        "en",
        "--source",
        "--source-lang",
        "-s",
        help="Source language code (default: en).",
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
    """Create a new booktx project layout."""
    try:
        proj = init_project(
            project_dir,
            target_language=target or "",
            profile_name=profile,
            source_language=source_lang,
            source_file=source,
            chunk_size=chunk_size,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return

    if target:
        console.print(f"[green]Initialized source project:[/green] {proj.root}")
        console.print(f"[green]Created profile:[/green] {proj.profile}")
        console.print(f"[green]Selected active profile:[/green] {proj.profile}")
    else:
        console.print(f"[green]Initialized source project:[/green] {proj.root}")
    console.print(f"  source_language: {proj.config.source_language}")
    if proj.config.target_language:
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
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return

    fmt = proj.config.format
    names = _load_names_list(proj)
    record_count, extra = _count_records(
        source, fmt, names, proj.config.source_language
    )

    table = Table(title=f"booktx inspect — {proj.root}", show_header=False)
    table.add_row("source_file", source.name)
    table.add_row("format", fmt)
    table.add_row("source_language", proj.config.source_language)
    table.add_row("target_language", proj.config.target_language)
    table.add_row("estimated_records", str(record_count))
    table.add_row("protected_terms", ", ".join(names) if names else "(none)")
    table.add_row("details", extra)
    console.print(table)


def _load_names_list(proj: Project) -> list[str]:
    from booktx.config import load_names

    return load_names(proj).protected_terms


def _count_records(
    source: Path, fmt: str, names: list[str], source_language: str
) -> tuple[int, str]:
    if fmt == "markdown":
        text = source.read_text("utf-8")
        ext = extract_markdown(text, protected_terms=names)
        spans = ext.spans
        details = f"{len(spans)} prose span(s)"
    elif fmt == "epub":
        extraction = extract_epub(str(source), protected_terms=names)
        spans = extraction.spans
        entries_raw = extraction.text2epub_manifest.get("entries", [])
        entries = entries_raw if isinstance(entries_raw, list) else []
        block_entries = [
            entry
            for entry in entries
            if isinstance(entry, dict) and entry.get("blocks")
        ]
        details = f"{len(block_entries)} spine document(s) with text blocks"
    else:  # pragma: no cover - config validation already guards this
        raise BooktxError("unsupported_format", f"Unsupported format {fmt!r}")

    from booktx.chunking import segment_spans

    records = segment_spans(spans, language=source_language)
    return len(records), details


def _chunk_json_texts(chunks: list[Chunk] | list[TranslatedChunk]) -> dict[str, str]:
    return {
        f"{chunk.chunk_id}.json": chunk.model_dump_json(indent=2) + "\n"
        for chunk in chunks
    }


def _has_accepted_store_records(proj: Project) -> bool:
    path = translation_store_path(proj)
    if not path.is_file():
        return False
    store = load_translation_store(proj)
    return any(
        any(candidate.status == "accepted" for candidate in record.versions)
        for record in store.records.values()
    )


def _same_extract_settings(
    manifest: Manifest,
    *,
    chunk_size: int,
    source_language: str,
    names_sha256: str,
) -> bool:
    return (
        manifest.chunk_size == chunk_size
        and manifest.record_id_scheme == RECORD_ID_SCHEME
        and manifest.segmenter == segmenter_metadata(source_language)
        and manifest.names_sha256 == names_sha256
    )


def _guard_extract_repeatability_and_rechunk(
    proj: Project,
    *,
    current_source_sha256: str,
    chunk_texts: dict[str, str],
    names_sha256: str,
    force_rechunk: bool,
) -> str | None:
    previous_manifest = load_manifest(proj)
    if previous_manifest is None:
        return None

    previous_source_sha256 = previous_manifest.source.sha256
    same_source = bool(previous_source_sha256) and (
        previous_source_sha256 == current_source_sha256
    )

    if (
        same_source
        and previous_manifest.record_id_scheme == RECORD_ID_SCHEME
        and previous_manifest.chunk_size != proj.config.chunk_size
        and _has_accepted_store_records(proj)
        and not force_rechunk
    ):
        _die(
            "chunk_size changed from "
            f"{previous_manifest.chunk_size} to {proj.config.chunk_size}, but this "
            f"project uses record_id_scheme={RECORD_ID_SCHEME}.\n"
            "Changing chunk_size would renumber record ids and orphan existing "
            "translation-store entries.\n"
            "Use the existing chunk_size, or run `booktx extract --force-rechunk` "
            "after backing up or migrating translations."
        )

    if same_source and _same_extract_settings(
        previous_manifest,
        chunk_size=proj.config.chunk_size,
        source_language=proj.config.source_language,
        names_sha256=names_sha256,
    ):
        existing_chunks = {
            path.name: path.read_text("utf-8")
            for path in sorted(proj.chunks(), key=lambda path: path.name)
        }
        if existing_chunks and existing_chunks != chunk_texts:
            _die(
                "repeatability violated: the same source and extraction settings "
                "did not reproduce byte-identical chunk files; refusing to replace "
                "the existing chunks."
            )

    if previous_source_sha256 and previous_source_sha256 != current_source_sha256:
        return (
            "source file changed since the previous extraction; validation may "
            "report stale translations."
        )
    return None


# --- extract -----------------------------------------------------------------


@app.command()
def extract(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    force_rechunk: bool = typer.Option(
        False,
        "--force-rechunk",
        help="Allow a risky chunk-size rechunk when chunk-local ids would be renumbered.",
    ),
) -> None:
    """Extract translatable chunks into ``.booktx/chunks/``.

    Idempotent: ``chunks/`` is rebuilt each run; ``translated/`` is left intact.
    """
    try:
        proj = load_project(project_dir)
        source = find_source_file(proj)
    except BooktxError as exc:
        _handle_booktx_error(exc)
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
    else:  # pragma: no cover
        _die(f"Unsupported format {fmt!r}")
        return

    chunks = spans_to_chunks(
        spans,
        source_language=proj.config.source_language,
        target_language=proj.config.target_language,
        chunk_size=proj.config.chunk_size,
    )
    if fmt == "epub":
        _assert_epub_records_are_clean(chunks)

    # Idempotent rebuild of chunks/ — write into a sibling temp dir and swap
    # it in atomically so an interrupted extract never leaves a half-empty
    # .booktx/chunks/.
    import tempfile

    from booktx.epub_manifest import sha256_path as _sha256
    from booktx.io_utils import write_text_atomic

    current_source_sha256 = (
        extraction.source_sha256 if fmt == "epub" else _sha256(source)
    )
    names_sha256 = protected_terms_sha256(names)
    chunk_texts = _chunk_json_texts(chunks)
    warning_message = _guard_extract_repeatability_and_rechunk(
        proj,
        current_source_sha256=current_source_sha256,
        chunk_texts=chunk_texts,
        names_sha256=names_sha256,
        force_rechunk=force_rechunk,
    )

    proj.booktx_dir.mkdir(parents=True, exist_ok=True)
    tmp_chunks = Path(tempfile.mkdtemp(prefix=".chunks.", dir=proj.booktx_dir))
    try:
        for filename, text in chunk_texts.items():
            write_text_atomic(tmp_chunks / filename, text)
        # Remove the previous chunks dir and move the temp one into place.
        if proj.chunks_dir.exists():
            shutil.rmtree(proj.chunks_dir)
        tmp_chunks.replace(proj.chunks_dir)
    except BaseException:
        shutil.rmtree(tmp_chunks, ignore_errors=True)
        raise

    record_count = sum(len(c.records) for c in chunks)
    epub_audit_warning = ""
    if fmt == "epub":
        _save_epub_manifest(proj, source, extraction, len(chunks), record_count)
        epub_audit_warning = _write_epub_chapter_map_and_audit(proj)
    elif fmt == "markdown":
        from booktx.config import write_manifest
        from booktx.models import Manifest, ManifestSource

        write_manifest(
            proj,
            Manifest(
                version=1,
                source=ManifestSource(
                    filename=source.name,
                    format="markdown",
                    source_language=proj.config.source_language,
                    target_language=proj.config.target_language,
                    sha256=current_source_sha256,
                ),
                chunk_count=len(chunks),
                record_count=record_count,
                chunk_size=proj.config.chunk_size,
                record_id_scheme=RECORD_ID_SCHEME,
                segmenter=segmenter_metadata(proj.config.source_language),
                names_sha256=names_sha256,
            ),
        )
    console.print(
        f"[green]Extracted[/green] {len(chunks)} chunk(s), "
        f"{record_count} record(s) into {proj.chunks_dir}"
    )
    if warning_message:
        console.print(f"[yellow]warning:[/yellow] {warning_message}", soft_wrap=True)
    if epub_audit_warning:
        console.print(f"[yellow]warning:[/yellow] {epub_audit_warning}", soft_wrap=True)
        console.print("[dim]details: booktx chapters . --audit[/dim]", soft_wrap=True)


def _assert_epub_records_are_clean(chunks: list[Chunk]) -> None:
    for chunk in chunks:
        for record in chunk.records:
            if "__TAG_" in record.source or "__SPANTX_" in record.source:
                raise BooktxError(
                    "epub_placeholders_leaked",
                    "new EPUB extraction produced TAG/SPANTX placeholders; "
                    "this is forbidden",
                )


def _save_epub_manifest(
    proj: Project,
    source: Path,
    extraction: EpubExtraction,
    chunk_count: int,
    record_count: int,
) -> None:
    """Record EPUB v2 extraction metadata in manifest.json."""
    import json

    from booktx.config import write_manifest
    from booktx.models import EpubTemplateData, Manifest, ManifestSource

    template = EpubTemplateData(
        pipeline=EPUB_TEMPLATE_PIPELINE,
        epub2text_schema=EPUB2TEXT_SCHEMA,
        text2epub_manifest=extraction.text2epub_manifest,
        spans=extraction.span_refs,
        navigation=extraction.navigation,
        chapter_mapping="epub2text-block-v1",
    )
    manifest = Manifest(
        version=2,
        source=ManifestSource(
            filename=source.name,
            format="epub",
            source_language=proj.config.source_language,
            target_language=proj.config.target_language,
            sha256=extraction.source_sha256,
        ),
        chunk_count=chunk_count,
        record_count=record_count,
        chunk_size=proj.config.chunk_size,
        record_id_scheme=RECORD_ID_SCHEME,
        segmenter=segmenter_metadata(proj.config.source_language),
        names_sha256=protected_terms_sha256(_load_names_list(proj)),
        template=template.model_dump(mode="json"),
    )
    write_manifest(proj, manifest)
    # names file convenience: keep names.json in sync if user edited it.
    _ = (json, NamesFile)  # touch imports for clarity


def _write_epub_chapter_map_and_audit(proj: Project) -> str:
    """Detect and persist the chapter map and audit after EPUB extraction.

    Returns a one-line warning string when the audit has findings, or "". The
    extraction itself stays successful: this is a completeness signal, not a
    policy gate, so preview/truncated EPUBs with warning-only findings still
    extract cleanly.
    """
    from booktx.epub_toc_audit import audit_epub_chapter_map, write_audit_report

    chapter_map = detect_chapters(proj)
    write_chapter_map(proj, chapter_map)
    result = audit_epub_chapter_map(proj, chapter_map=chapter_map)
    write_audit_report(proj, result)
    if not result.findings:
        return ""
    bits: list[str] = []
    if result.error_findings:
        bits.append(f"{len(result.error_findings)} error(s)")
    if result.warning_findings:
        bits.append(f"{len(result.warning_findings)} warning(s)")
    return (
        "EPUB chapter audit: "
        + ", ".join(bits)
        + " (visible TOC vs extracted chapters)."
    )


def _coverage_status(*, total: int, translated: int, has_error: bool) -> str:
    """Backward-compatible alias for :func:`booktx.status.coverage_status`."""
    from booktx.status import coverage_status

    return coverage_status(total=total, translated=translated, has_error=has_error)


def _format_chunk_span(chunk_ids: list[str]) -> str:
    from booktx.rendering import format_chunk_span

    return format_chunk_span(chunk_ids)


def _chapter_map_for_workflow(proj: Project) -> ChapterMap:
    """Refresh-and-load helper retained for direct callers outside status.py."""
    source_sha256 = project_source_sha256(proj)
    chapter_map = load_chapter_map(proj)
    if chapter_map is None or chapter_map.source_sha256 != source_sha256:
        chapter_map = detect_chapters(proj)
        write_chapter_map(proj, chapter_map)
    return chapter_map


def _render_epub_audit_summary(audit: Any) -> None:
    """Print a recomputed EPUB chapter-audit summary when findings exist."""
    if audit is None or not getattr(audit, "findings", None):
        return
    color = "red" if audit.has_blocking_errors else "yellow"
    label = "error" if audit.has_blocking_errors else "warning"
    console.print(
        f"[{color}]{label}:[/{color}] EPUB chapter audit: "
        f"{audit.error_count} error(s), {audit.warning_count} warning(s) "
        f"(visible TOC vs extracted chapters).",
        soft_wrap=True,
    )
    console.print("[dim]details: booktx chapters . --audit[/dim]", soft_wrap=True)


def _block_on_epub_audit_errors(bundle: StatusBundle) -> None:
    """Refuse new work selection when the recomputed EPUB audit has errors.

    Warnings (preview/truncated EPUBs) stay non-blocking; only ``error`` findings
    such as ``epub_toc_href_extracted_but_unmapped`` block. The audit is always
    recomputed (``StatusBundle.epub_audit``), never read from a persisted report.
    """
    audit = getattr(bundle, "epub_audit", None)
    if audit is None or not audit.has_blocking_errors:
        return
    errors = [f for f in audit.findings if f.severity == "error"]
    preview = "; ".join(f"{f.code}: {f.message}" for f in errors[:3])
    suffix = "" if len(errors) <= 3 else f" ...(+{len(errors) - 3} more)"
    _die(
        f"EPUB chapter audit reports {len(errors)} blocking error(s); refusing to "
        f"select new work until resolved. {preview}{suffix}\n"
        "Inspect: booktx chapters . --audit"
    )


def _limit_records_by_words(
    record_ids: list[str], source_by_id: dict[str, Any], max_words: int
) -> list[str]:
    from booktx.tasks import limit_records_by_words

    try:
        return limit_records_by_words(record_ids, source_by_id, max_words)
    except ValueError as exc:
        _die(f"--{str(exc).replace('_', '-')}")
        raise typer.Exit(code=1) from exc


def _select_translation_record_ids(
    bundle: StatusBundle,
    chapter: ChapterProgress,
    *,
    unit: str,
    max_words: int,
) -> tuple[str, list[str]]:
    try:
        return select_translation_record_ids(
            bundle,
            chapter,
            unit=unit,
            max_words=max_words,
        )
    except ValueError as exc:
        _die(f"--{str(exc).replace('_', '-')}")
        raise typer.Exit(code=1) from exc


def _create_translation_task(
    proj: Project,
    bundle: StatusBundle,
    chapter: ChapterProgress,
    *,
    mode: RuntimeMode | None = None,
    unit: str,
    record_ids: list[str],
    requested_max_words: int | None = None,
    todo_id: str | None = None,
) -> TranslationTask:
    """Backward-compatible alias for :func:`booktx.tasks.create_translation_task`."""
    return create_translation_task(
        proj,
        bundle,
        chapter,
        mode=mode,
        unit=unit,
        record_ids=record_ids,
        requested_max_words=requested_max_words,
        todo_id=todo_id,
    )


def _print_status_human(bundle: StatusBundle, chapter: ChapterProgress | None) -> None:
    from booktx.rendering import print_status_human

    print_status_human(bundle, chapter)
    _render_epub_audit_summary(getattr(bundle, "epub_audit", None))


def _print_translate_task(
    task: TranslationTask,
    proj: Project,
    *,
    mode: RuntimeMode | None = None,
    as_json: bool,
    output_format: str,
    show_sources: bool = False,
    show_template: bool = False,
) -> None:
    from booktx.rendering import print_translate_task

    print_translate_task(
        task,
        proj,
        mode=mode,
        as_json=as_json,
        output_format=output_format,
        show_sources=show_sources,
        show_template=show_template,
    )


def _load_translation_task_or_exit(proj: Project, task_id: str) -> TranslationTask:
    task = load_translation_task(proj, task_id)
    if task is None:
        _die(f"unknown task id: {task_id} ({translation_task_path(proj, task_id)})")
        raise typer.Exit(code=1)
    return task


def _next_chapter(
    proj: Project,
    *,
    print_context: bool,
    mode: RuntimeMode | None = None,
) -> None:
    summary = _project_status_snapshot(proj)
    _block_on_epub_audit_errors(summary)
    chapter = summary.snapshot.next
    if chapter is None:
        console.print("All chapter records have accepted translations.")
        raise typer.Exit(code=1)
    if print_context:
        if mode is not None:
            console.print(
                f"context: {display_path(context_markdown_path(proj), mode)}",
                soft_wrap=True,
            )
        else:
            console.print(f"context: {context_markdown_path(proj)}", soft_wrap=True)
    console.print(f"chapter: {chapter.chapter_id}  {chapter.title}".rstrip())
    console.print(f"status: {chapter.status}")
    console.print(
        f"record range: {chapter.record_range.start}..{chapter.record_range.end}"
    )
    console.print(
        f"records: {chapter.records_translated} / "
        f"{chapter.records_total} translated, "
        f"{chapter.records_remaining} remaining"
    )
    console.print(f"chunks: {_format_chunk_span(chapter.chunk_ids)}")
    console.print(f"pending chunks: {_format_chunk_span(chapter.pending_chunk_ids)}")
    console.print(f"source words remaining: {chapter.source_words_remaining:,}")
    console.print(
        "[dim]next command:[/dim] "
        + translate_next_command(proj, mode=mode, chapter_id=chapter.chapter_id)
    )
    raise typer.Exit(code=0)


# --- next --------------------------------------------------------------------


@app.command(name="status")
def status_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    chapter: str | None = typer.Option(
        None, "--chapter", help="Optional chapter id to focus the report."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit stable JSON output."),
) -> None:
    """Report record-aware translation progress."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=False)
    proj = runtime.project
    if proj.layout_version == "profiles" and proj.profile is None:
        overview = build_profiles_overview(load_source_project(proj.root))
        if as_json:
            console.print_json(
                json.dumps(overview.model_dump(mode="json"), ensure_ascii=False)
            )
            return
        _render_profiles_overview_human(overview)
        return
    _require_chunks(proj)
    summary = _project_status_snapshot(proj)
    selected = _selected_chapter(summary, chapter)
    if selected is not None:
        summary.snapshot.chapters = [selected]
        summary.snapshot.next = selected
    if runtime.mode.isolated_output:
        summary.snapshot.project = display_path(
            proj.profile_dir or proj.root, runtime.mode
        )
    if as_json:
        console.print_json(
            json.dumps(summary.snapshot.model_dump(mode="json"), ensure_ascii=False)
        )
        return
    _print_status_human(summary, selected)


@app.command(name="next")
def next_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    allow_missing_context: bool = typer.Option(
        False,
        "--allow-missing-context",
        help="Legacy override: allow next without a ready translation context.",
    ),
    unit: str = typer.Option(
        "chunk", "--unit", help="Translation unit to return: chunk or chapter."
    ),
) -> None:
    """Print the next pending legacy work item and point callers at translate/*."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    proj = runtime.project

    if unit not in {"chunk", "chapter"}:
        _die("--unit must be chunk or chapter")
    _require_chunks(proj)
    print_context = _require_ready_context(
        proj, allow_missing_context=allow_missing_context
    )
    if unit == "chapter":
        _next_chapter(proj, print_context=print_context, mode=runtime.mode)
        return
    if runtime.mode.isolated_output:
        _die(
            "booktx next is not available in profile-root isolated mode; use `booktx translate next .` instead"
        )
    summary = _project_status_snapshot(proj)
    _block_on_epub_audit_errors(summary)
    pending_chunks = [
        chunk.chunk_id
        for chunk in summary.index.chunk_summaries
        if chunk.records_remaining > 0
    ]
    if not pending_chunks:
        console.print("All chunk records have accepted translations.")
        raise typer.Exit(code=1)
    if print_context:
        console.print(f"context: {context_markdown_path(proj)}", soft_wrap=True)
    cid = pending_chunks[0]
    chunk_path = proj.chunks_dir / f"{cid}.json"
    records_remaining = next(
        chunk.records_remaining
        for chunk in summary.index.chunk_summaries
        if chunk.chunk_id == cid
    )
    console.print(f"{cid}\t{chunk_path}", soft_wrap=True)
    console.print(f"records remaining: {records_remaining}")
    console.print("[dim]submit with:[/dim]")
    profile_part = f" --profile {proj.profile}" if proj.profile else ""
    console.print(
        f"booktx translate next {project_dir}{profile_part} --unit chunk",
        soft_wrap=True,
    )
    console.print(f"booktx translate insert .{profile_part} --stdin")
    raise typer.Exit(code=0)


# --- chapters ---------------------------------------------------------------


@app.command(name="chapters")
def chapters_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    audit: bool = typer.Option(
        False,
        "--audit",
        help=(
            "Audit the EPUB visible TOC against extracted spans, navigation, "
            "and the chapter map; writes .booktx/reports/chapter-audit.json."
        ),
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON output.",
    ),
) -> None:
    """Detect and list chapter ranges, or audit EPUB chapter completeness."""
    try:
        proj = load_project(project_dir)
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    if audit:
        _run_chapter_audit(proj, as_json=as_json)
        return
    chapter_map = detect_chapters(proj)
    write_chapter_map(proj, chapter_map)
    for chapter in chapter_map.chapters:
        chunks = ", ".join(chapter.chunk_ids)
        title = f"  {chapter.title}" if chapter.title else ""
        console.print(
            f"{chapter.chapter_id}{title}\tchunks: {chunks}\t"
            f"records: {chapter.start_record_id}..{chapter.end_record_id}"
        )


def _run_chapter_audit(proj: Project, *, as_json: bool = False) -> None:
    from booktx.epub_toc_audit import (
        audit_epub_chapter_map,
        write_audit_report,
    )

    if proj.config.format != "epub":
        if as_json:
            console.print_json('{"error": "chapter audit is EPUB-only"}')
        else:
            _die("chapter audit is EPUB-only")
        return
    chapter_map = load_chapter_map(proj)
    if chapter_map is None:
        # Read-only audit: detect without persisting so chapter-map.json is
        # not mutated by --audit.
        chapter_map = detect_chapters(proj)
    result = audit_epub_chapter_map(proj, chapter_map=chapter_map)
    out_path = write_audit_report(proj, result)
    if as_json:
        console.print_json(json.dumps(result.as_dict(), indent=2, ensure_ascii=False))
        return
    console.print("EPUB chapter audit")
    console.print(f"toc entries: {len(result.toc_entries)}")
    console.print(f"numbered TOC chapters: {result.numbered_toc_count}")
    console.print(f"numbered chapters in map: {result.mapped_numbered_chapter_count}")
    console.print(f"extracted documents: {result.extracted_document_count}")
    if result.missing_numbered_titles:
        preview = ", ".join(result.missing_numbered_titles[:12])
        suffix = "" if len(result.missing_numbered_titles) <= 12 else ", ..."
        console.print(f"missing numbered chapters: {preview}{suffix}")
    if not result.findings:
        console.print("findings: none")
    else:
        for finding in result.findings:
            severity_color = {
                "error": "red",
                "warning": "yellow",
                "info": "cyan",
            }.get(finding.severity, "white")
            console.print(
                f"[{severity_color}]{finding.severity}[/{severity_color}] "
                f"{finding.code}: {finding.message}",
                soft_wrap=True,
            )
    console.print(f"report: {out_path}")


@app.command(name="next-chapter")
def next_chapter_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    allow_missing_context: bool = typer.Option(
        False,
        "--allow-missing-context",
        help="Legacy override: allow next-chapter without ready context.",
    ),
) -> None:
    """Print the next incomplete chapter and all chunks it covers."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    proj = runtime.project
    _require_chunks(proj)
    print_context = _require_ready_context(
        proj, allow_missing_context=allow_missing_context
    )
    _next_chapter(proj, print_context=print_context, mode=runtime.mode)


@translate_app.command(name="next")
def translate_next(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    chapter: str | None = typer.Option(None, "--chapter", help="Optional chapter id."),
    unit: str = typer.Option(
        "paragraph",
        "--unit",
        help="Work-unit selection: paragraph, batch, chunk, or chapter.",
    ),
    max_words: int = typer.Option(
        900,
        "--max-words",
        help="Maximum source words to return for paragraph or batch work units.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
    output_format: str = typer.Option(
        "text",
        "--format",
        help="Human output format: text, tsv, or block.",
    ),
    show_sources: bool = typer.Option(
        False,
        "--show-sources",
        help="Print source records inline (block format only).",
    ),
    show_template: bool = typer.Option(
        False,
        "--show-template",
        help="Print the heredoc submit template inline (block format only).",
    ),
    allow_missing_context: bool = typer.Option(
        False,
        "--allow-missing-context",
        help="Legacy override: allow next without a ready translation context.",
    ),
    chapter_word_limit: int | None = typer.Option(
        None,
        "--chapter-word-limit",
        help="Source-word threshold above which --unit chapter is treated as oversized.",
    ),
    large_chapter_mode: str = typer.Option(
        "todo",
        "--large-chapter-mode",
        help="How to handle oversized --unit chapter requests: todo, error, or chapter.",
    ),
    force_chapter: bool = typer.Option(
        False,
        "--force-chapter",
        help="Force --unit chapter regardless of size (alias for --large-chapter-mode chapter).",
    ),
) -> None:
    """Return the next text to translate and persist a task id."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    proj = runtime.project
    if unit not in {"paragraph", "batch", "chunk", "chapter"}:
        _die("--unit must be paragraph, batch, chunk, or chapter")
    if output_format not in {"text", "tsv", "block"}:
        _die("--format must be text, tsv, or block")
    if as_json and output_format != "text":
        _die("--json cannot be combined with --format")
    _require_chunks(proj)
    _require_no_source_drift(proj)
    _require_ready_context(proj, allow_missing_context=allow_missing_context)
    summary = _project_status_snapshot(proj)
    _block_on_epub_audit_errors(summary)
    selected_chapter = _selected_chapter(summary, chapter)
    if selected_chapter is None:
        console.print("All records already have accepted translations.")
        raise typer.Exit(code=1)
    # Large-chapter protection: when --unit chapter is requested and the
    # chapter exceeds the safe word budget, redirect to a single-chapter todo.
    if unit == "chapter" and not force_chapter:
        limit = chapter_word_limit or max_words
        if selected_chapter.source_words_remaining > limit:
            from booktx.todo_resume import ensure_single_chapter_todo

            if large_chapter_mode == "error":
                from booktx.command_hints import (
                    profile_option_fragment,
                    translate_todo_resume_command,
                )

                console.print(
                    f"Chapter {selected_chapter.chapter_id} has "
                    f"{selected_chapter.source_words_remaining:,} source words remaining, "
                    f"exceeding the safe budget of {limit}."
                )
                prof = profile_option_fragment(proj, runtime.mode)
                console.print("Create a bounded todo:")
                console.print(
                    f"booktx translate todo-next .{prof}"
                    f" --start-chapter {selected_chapter.chapter_id}"
                    f" --chapters 1 --batch-words {max_words} --write",
                    soft_wrap=True,
                    markup=False,
                )
                console.print("Resume the todo:")
                console.print(
                    translate_todo_resume_command(
                        proj,
                        mode=runtime.mode,
                        latest=True,
                    ),
                    soft_wrap=True,
                    markup=False,
                )
                raise typer.Exit(code=1)
            # large_chapter_mode == "todo" (default)
            todo = ensure_single_chapter_todo(
                proj,
                summary,
                chapter_id=selected_chapter.chapter_id,
                batch_words=max_words,
            )
            console.print(
                f"large chapter detected: {selected_chapter.chapter_id} "
                f"{selected_chapter.title} has "
                f"{selected_chapter.source_words_remaining:,} source words remaining"
            )
            console.print(f"created todo: {todo.todo_id}")
            console.print(
                f"goal: complete chapter {selected_chapter.chapter_id} {selected_chapter.title}"
            )
            console.print(f"batch words: {todo.batch_words}")
            from booktx.todo_resume import resume_translation_todo

            task = resume_translation_todo(
                proj, summary, mode=runtime.mode, todo_id=todo.todo_id
            )
            _print_translate_task(
                task,
                proj,
                mode=runtime.mode,
                as_json=as_json,
                output_format=output_format,
                show_sources=show_sources,
                show_template=show_template,
            )
            return
    actual_unit, record_ids = _select_translation_record_ids(
        summary,
        selected_chapter,
        unit=unit,
        max_words=max_words,
    )
    if not record_ids:
        console.print("Selected chapter has no remaining records.")
        raise typer.Exit(code=1)
    task = _create_translation_task(
        proj,
        summary,
        selected_chapter,
        mode=runtime.mode,
        unit=actual_unit,
        record_ids=record_ids,
        requested_max_words=max_words,
    )
    _print_translate_task(
        task,
        proj,
        mode=runtime.mode,
        as_json=as_json,
        output_format=output_format,
        show_sources=show_sources,
        show_template=show_template,
    )


@translate_app.command(name="insert")
def translate_insert(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    task_id: str | None = typer.Option(None, "--task-id", help="Optional task id."),
    stdin: bool = typer.Option(False, "--stdin", help="Read the payload from stdin."),
    record_id: str | None = typer.Option(None, "--record-id", help="Single record id."),
    target: str | None = typer.Option(None, "--target", help="Single target text."),
    json_file: Path | None = typer.Option(
        None,
        "--json-file",
        help="Compatibility sugar for --format json --file PATH.",
    ),
    input_file: Path | None = typer.Option(
        None,
        "--file",
        help="Read submission payload from a file using --format.",
    ),
    input_format: str = typer.Option(
        "json",
        "--format",
        help="Input format for --stdin/--file payloads: json, tsv, or block.",
    ),
    allow_missing_context: bool = typer.Option(
        False,
        "--allow-missing-context",
        help="Legacy override: allow insert without a ready translation context.",
    ),
    export_index: bool = typer.Option(
        False, "--export-index", help="Export editor QA indexes after acceptance."
    ),
) -> None:
    """Accept translated text through the CLI and write the store atomically."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    proj = runtime.project
    if input_format not in {"json", "tsv", "block"}:
        _die("--format must be json, tsv, or block")
    _require_chunks(proj)
    _require_no_source_drift(proj)
    _require_ready_context(proj, allow_missing_context=allow_missing_context)

    try:
        parsed = resolve_submission(
            record_id=record_id,
            target=target,
            input_format=input_format,
            stdin=stdin,
            json_file=json_file,
            input_file=input_file,
            ingest_hint=_submission_ingest_hint(proj, task_id, mode=runtime.mode),
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return

    submitted_records = parsed.records
    payload_task_id = parsed.task_id

    effective_task_id = task_id or payload_task_id
    task = (
        _load_translation_task_or_exit(proj, effective_task_id)
        if effective_task_id
        else None
    )
    summary = _project_status_snapshot(proj)
    # Pre-write EPUB inline-XHTML check (Q2=a).
    # Stage submitted records and run the preflight BEFORE writing the store.
    submitted_ids = {r.id for r in submitted_records}
    try:
        _staged_preflight_check(proj, submitted_records, submitted_ids)
    except ValidationError as exc:
        console.print(
            "[red]error:[/red] internal preflight staging failed while "
            "validating submitted EPUB inline XHTML"
        )
        console.print(
            "hint: retry after updating booktx; the staged EPUB model could "
            "not be built. Run with debug output if available for traceback details."
        )
        console.print(f"detail: {exc}")
        raise typer.Exit(code=1) from None
    try:
        result = accept_translation_records(
            proj,
            submitted_records,
            bundle=summary,
            task=task,
            submission_translation_version=parsed.translation_version,
            submission_profile=parsed.profile,
            enforce_task_version=True,
        )
    except SubmissionValidationError as exc:
        _render_submission_failures(exc.findings)
        raise typer.Exit(code=1) from None
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(
        f"accepted: {result.accepted_records} record(s), "
        f"{result.target_words} target word(s)"
    )
    if result.version_ref:
        console.print(f"version: {result.version_ref}")
    _maybe_auto_export_indexes(proj, export_index=export_index, trigger="translation")
    if result.chapter_id:
        console.print(f"chapter: {result.chapter_id} {result.chapter_title}".rstrip())
        console.print(
            f"progress: {result.records_translated} / "
            f"{result.records_total} records translated, "
            f"{result.records_remaining} remaining"
        )
        if result.records_remaining == 0:
            console.print(
                f"chapter complete: {result.chapter_id} {result.chapter_title}".rstrip()
            )
            console.print("recommended context update template:")
            console.print(
                context_chapter_note_command(
                    proj,
                    mode=runtime.mode,
                    chapter_id=result.chapter_id,
                    title=result.chapter_title or "<TITLE>",
                ),
                soft_wrap=True,
                markup=False,
            )

    # Rebuild status after insert to get fresh totals.
    fresh = _project_status_snapshot(proj)
    max_words = task.requested_max_words if task and task.requested_max_words else 800
    if task is not None and task.todo_id:
        todo = load_translation_todo(proj, task.todo_id)
        if todo is None:
            console.print(
                f"[yellow]warning:[/yellow] todo {task.todo_id} referenced by task "
                f"{task.task_id} is missing; falling back to generic next hints"
            )
        else:
            todo_status = build_todo_status(
                proj,
                todo,
                fresh,
                fail_on_warnings=False,
            )
            if todo_status.goal_complete:
                console.print(f"todo complete: {todo.todo_id}")
                console.print("next: stop - todo goal complete")
            elif todo_status.next_safe_command is not None:
                console.print(
                    "next: " + todo_status.next_safe_command,
                    soft_wrap=True,
                    markup=False,
                )
            return
    if fresh.snapshot.totals.records_remaining == 0:
        console.print(
            "next: " + build_command(proj, mode=runtime.mode),
            soft_wrap=True,
            markup=False,
        )
    elif result.chapter_id and result.records_remaining > 0:
        # Current chapter still incomplete — stay on it.
        # Warn if this looks like an oversized chapter task (no todo backing).
        if task is not None and not task.todo_id and task.unit == "chapter":
            from booktx.command_hints import profile_option_fragment

            console.print(
                "[yellow]warning:[/yellow] this looks like an oversized chapter task. "
                "Use a bounded todo instead:"
            )
            prof = profile_option_fragment(proj, runtime.mode)
            console.print(
                f"booktx translate todo-next .{prof}"
                f" --start-chapter {result.chapter_id}"
                f" --chapters 1 --batch-words {max_words} --write",
                soft_wrap=True,
                markup=False,
            )
            console.print(
                f"booktx translate todo-resume .{prof} --latest --format block",
                soft_wrap=True,
                markup=False,
            )
        console.print(
            "next: "
            + translate_next_command(
                proj,
                mode=runtime.mode,
                chapter_id=result.chapter_id,
                max_words=max_words,
            ),
            soft_wrap=True,
            markup=False,
        )
    elif result.chapter_id:
        # Chapter just completed — advance to next incomplete chapter.
        console.print(
            "next: "
            + translate_next_command(
                proj,
                mode=runtime.mode,
                max_words=max_words,
            ),
            soft_wrap=True,
            markup=False,
        )


@translate_app.command(name="todo-next")
def translate_todo_next(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    chapters: int = typer.Option(
        3, "--chapters", min=1, help="Number of incomplete chapters to complete."
    ),
    batch_words: int = typer.Option(
        800, "--batch-words", min=1, help="Source-word budget per translate next batch."
    ),
    max_run_words: int | None = typer.Option(
        None,
        "--max-run-words",
        min=1,
        help="Optional source-word cap for this agent run.",
    ),
    start_chapter: str | None = typer.Option(
        None,
        "--start-chapter",
        help="Optional chapter id to start from.",
    ),
    skip_current: bool = typer.Option(
        False,
        "--skip-current",
        help="Start after the current first incomplete chapter.",
    ),
    write: bool = typer.Option(
        False,
        "--write",
        help="Write todo markdown/json under translations/<profile>/todos/.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """Create a durable run-control todo for a bounded multi-chapter translation run.

    This writes a todo file (not translations) describing how many chapters to
    complete, the per-task word budget, and the stop conditions.  The agent
    reads the todo and loops ``translate next -> fill -> insert -> validate``
    until done or a stop condition occurs.
    """
    from booktx.agent_todo import build_translation_todo, write_translation_todo

    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    proj = runtime.project
    _require_chunks(proj)
    _require_no_source_drift(proj)
    _require_ready_context(proj)
    bundle = _project_status_snapshot(proj)
    _block_on_epub_audit_errors(bundle)

    try:
        todo = build_translation_todo(
            proj,
            bundle,
            chapters=chapters,
            batch_words=batch_words,
            max_run_words=max_run_words,
            skip_current=skip_current,
            start_chapter=start_chapter,
        )
    except ValueError as exc:
        _die(str(exc))

    json_path: Path | None = None
    md_path: Path | None = None
    if write:
        json_path, md_path = write_translation_todo(proj, todo, mode=runtime.mode)
        # Verify the written file is loadable before printing success.
        loaded = load_translation_todo(proj, todo.todo_id)
        if loaded is None:
            _die(f"internal error: wrote todo {todo.todo_id} but could not reload it")

    if as_json:
        payload: dict[str, object] = {
            "version": 1,
            "todo_id": todo.todo_id,
            "profile": todo.profile,
            "target_language": todo.target_language,
            "target_locale": todo.target_locale,
            "chapters_requested": todo.chapters_requested,
            "batch_words": todo.batch_words,
            "max_run_words": todo.max_run_words,
            "include_current": todo.include_current,
            "created_at": todo.created_at,
            "baseline_ref": todo.baseline_ref,
            "baseline_sha256": todo.baseline_sha256,
            "context_sha256": todo.context_sha256,
            "source_sha256": todo.source_sha256,
            "chapters": [
                {
                    "chapter_id": c.chapter_id,
                    "title": c.title,
                    "status": c.status,
                    "records_total": c.records_total,
                    "records_translated_at_start": c.records_translated_at_start,
                    "records_remaining_at_start": c.records_remaining_at_start,
                    "source_words_remaining_at_start": c.source_words_remaining_at_start,
                    "pending_chunk_ids": c.pending_chunk_ids,
                }
                for c in todo.chapters
            ],
        }
        if json_path is not None:
            payload["json_path"] = display_path(json_path, runtime.mode)
        if md_path is not None:
            payload["markdown_path"] = display_path(md_path, runtime.mode)
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return

    # Human output
    console.print(f"todo: {todo.todo_id}")
    first = todo.chapters[0] if todo.chapters else None
    if first:
        console.print(
            f"goal: complete {todo.chapters_requested} incomplete chapter(s),"
            f" starting at {first.chapter_id} {first.title}".rstrip()
        )
    else:
        console.print(f"goal: complete {todo.chapters_requested} incomplete chapter(s)")
    console.print(f"batch words: {todo.batch_words}")
    console.print("chapters: " + ", ".join(c.chapter_id for c in todo.chapters))
    if md_path is not None:
        console.print(
            f"markdown: {display_path(md_path, runtime.mode)}",
            soft_wrap=True,
            markup=False,
        )
    if json_path is not None:
        console.print(
            f"json: {display_path(json_path, runtime.mode)}",
            soft_wrap=True,
            markup=False,
        )
    console.print(
        "next command: "
        + translate_todo_status_command(proj, mode=runtime.mode, todo_id=todo.todo_id),
        soft_wrap=True,
        markup=False,
    )
    console.print(
        "resume command: "
        + translate_todo_resume_command(
            proj,
            mode=runtime.mode,
            todo_id=todo.todo_id,
            output_format="block",
        ),
        soft_wrap=True,
        markup=False,
    )


def _print_todo_status_human(status: Any) -> None:
    chapters_display = ", ".join(chapter.chapter_id for chapter in status.todo.chapters)
    console.print(f"todo: {status.todo.todo_id}")
    console.print(
        f"goal: complete {status.todo.chapters_requested} chapter(s): {chapters_display}"
    )
    console.print(f"complete: {status.complete_count} / {len(status.chapters)}")
    console.print(f"state: {status.state}")
    console.print(f"source drift: {'yes' if status.source_drifted else 'no'}")
    console.print(f"context drift: {'yes' if status.context_drifted else 'no'}")
    validation = status.validation
    console.print(
        "validation: "
        f"errors={validation.errors} warnings={validation.warnings}"
        f"{' (blocking)' if validation.blocking else ''}"
    )
    if status.validation_scope_chapter is not None:
        scope = status.validation_scope_chapter
        title = ""
        if status.current_chapter is not None:
            title = f" {status.current_chapter.title}".rstrip()
        console.print(f"validation scope: chapter {scope}{title}")
    if status.blocking_reason:
        console.print(f"reason: {status.blocking_reason}")
    if status.current_chapter is not None:
        current = status.current_chapter
        console.print(f"current: {current.chapter_id} {current.title}".rstrip())
        console.print(
            f"progress: {current.records_translated_now} / {current.records_total} "
            f"records, {current.records_remaining_now} remaining"
        )
    else:
        console.print("current: none")
    if status.next_safe_command is not None:
        console.print(
            "next: " + status.next_safe_command,
            soft_wrap=True,
            markup=False,
        )
    elif status.goal_complete:
        console.print("next: stop - todo goal complete")
    if status.global_note:
        console.print(
            f"note: {status.global_note}",
            soft_wrap=True,
            markup=False,
        )
    console.print("planned chapters:")
    for chapter in status.chapters:
        console.print(
            f"- {chapter.chapter_id} {chapter.title}: "
            f"{chapter.records_translated_now} / {chapter.records_total} translated, "
            f"{chapter.records_remaining_now} remaining, status={chapter.status_now}"
        )


@translate_app.command(name="todo-status")
def translate_todo_status(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    todo_id: str | None = typer.Option(None, "--todo-id", help="Todo id to inspect."),
    latest: bool = typer.Option(
        False, "--latest", help="Select the latest incomplete todo."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit stable JSON output."),
) -> None:
    """Show live bounded-run todo status and the next safe command."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    proj = runtime.project
    _require_chunks(proj)
    bundle = _project_status_snapshot(proj)
    try:
        todo = resolve_translation_todo(proj, bundle, todo_id=todo_id, latest=latest)
        scope_chapter = current_todo_chapter_id(todo, bundle)
        scoped_report = validate_project(proj, chapter_id=scope_chapter)
        # Second full pass for the non-blocking global note (ac-0003).
        global_report = validate_project(proj) if scope_chapter is not None else None
        status = build_todo_status(
            proj,
            todo,
            bundle,
            mode=runtime.mode,
            validation_report=scoped_report,
            fail_on_warnings=True,
            scope_chapter_id=scope_chapter,
            global_report=global_report,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    if as_json:
        console.print_json(json.dumps(status.as_dict(), ensure_ascii=False))
        return
    _print_todo_status_human(status)


@translate_app.command(name="todo-resume")
def translate_todo_resume(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    todo_id: str | None = typer.Option(None, "--todo-id", help="Todo id to resume."),
    latest: bool = typer.Option(
        False, "--latest", help="Resume the latest incomplete todo."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
    output_format: str = typer.Option(
        "block",
        "--format",
        help="Human output format: text, tsv, or block.",
    ),
    show_sources: bool = typer.Option(
        False,
        "--show-sources",
        help="Print source records inline (block format only).",
    ),
    show_template: bool = typer.Option(
        False,
        "--show-template",
        help="Print the heredoc submit template inline (block format only).",
    ),
) -> None:
    """Resume a bounded multi-chapter todo and create the next safe task."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    proj = runtime.project
    if output_format not in {"text", "tsv", "block"}:
        _die("--format must be text, tsv, or block")
    if as_json and output_format != "text":
        _die("--json cannot be combined with --format")
    _require_chunks(proj)
    bundle = _project_status_snapshot(proj)
    try:
        task = resume_translation_todo(
            proj, bundle, mode=runtime.mode, todo_id=todo_id, latest=latest
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    _print_translate_task(
        task,
        proj,
        mode=runtime.mode,
        as_json=as_json,
        output_format=output_format,
        show_sources=show_sources,
        show_template=show_template,
    )


@translate_app.command(name="import-legacy")
def translate_import_legacy(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
) -> None:
    """Import valid legacy translated chunk files into the translation store."""
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    _require_chunks(proj)
    store = load_translation_store(proj)
    resolution = resolve_current_version(
        proj,
        note="Imported valid legacy translated chunks into nested translation store.",
    )
    imported_records = 0
    imported_chunks = 0
    source_chunks = {chunk.chunk_id: chunk for chunk in load_source_chunks(proj)}
    for chunk_id, source_chunk in source_chunks.items():
        if proj.translated_dir is None:
            continue
        path = proj.translated_dir / f"{chunk_id}.json"
        if not path.is_file():
            continue
        findings = validate_chunk_pair(source_chunk, path, load_context(proj))
        if any(finding.severity == Severity.ERROR for finding in findings):
            continue
        translated_chunk, err = strict_load_translated(path)
        if err is not None or translated_chunk is None:
            continue
        imported_chunks += 1
        source_records = {record.id: record for record in source_chunk.records}
        for record in translated_chunk.records:
            source_record = source_records[record.id]
            stored = ensure_store_record(
                store,
                record.id,
                source=source_record.source,
                source_sha256=source_record_sha256(source_record.source),
            )
            if active_candidate(stored) is not None:
                continue
            upsert_translation_version(
                stored,
                resolution.version_ref,
                record.target,
                updated_at=datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
            )
            imported_records += 1
    store.source_sha256 = project_source_sha256(proj)
    write_translation_store(proj, store)
    console.print(
        f"imported: {imported_records} record(s) from {imported_chunks} legacy chunk(s)"
    )


@translate_app.command(name="migrate-store")
def translate_migrate_store(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    write: bool = typer.Option(False, "--write", help="Rewrite the store as v2."),
    actor: str | None = typer.Option(
        None, "--actor", help="Actor for migrated ledger."
    ),
    harness: str | None = typer.Option(
        None, "--harness", help="Harness for migrated ledger."
    ),
    model: str | None = typer.Option(
        None, "--model", help="Model for migrated ledger."
    ),
    context_label: str | None = typer.Option(
        None,
        "--context-label",
        help="Optional label stored on the migrated subversion.",
    ),
    allow_missing_source: bool = typer.Option(
        False,
        "--allow-missing-source",
        help="Write migrated records even when some legacy ids no longer exist in source chunks.",
    ),
) -> None:
    """Inspect or rewrite a legacy translation-store.json into the v2 schema."""
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    path = translation_store_path(proj)
    if not path.is_file():
        _die("translation-store.json is missing")

    try:
        raw = json.loads(path.read_text("utf-8"))
        if isinstance(raw, dict) and raw.get("version") == 2:
            console.print("translation-store.json is already v2")
            return
        legacy = TranslationStore.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        _die(f"translation-store.json is invalid: {exc}")
        return

    source_records = {record.record_id: record for record in load_source_records(proj)}
    migration = migrate_legacy_store(legacy, source_records=source_records)

    if not write:
        console.print(f"dry-run: would migrate {migration.migrated_records} record(s)")
        if migration.missing_source_ids:
            console.print(
                "missing source records: " + ", ".join(migration.missing_source_ids)
            )
        return

    if actor is not None or harness is not None or model is not None:
        write_identity(
            proj,
            TranslationIdentity(
                actor=actor or "user:unknown",
                harness=harness or "booktx",
                model=model or "human",
            ),
        )

    resolution = resolve_current_version(
        proj,
        actor=actor,
        harness=harness,
        model=model,
        context_label=context_label,
        note="Migrated legacy v1 translation store to v2 nested store.",
    )
    migration = migrate_legacy_store(
        legacy,
        source_records=source_records,
        version_ref=resolution.version_ref,
    )
    if migration.missing_source_ids and not allow_missing_source:
        _die(
            "cannot migrate store with missing source records: "
            + ", ".join(migration.missing_source_ids)
        )
    write_translation_store(proj, migration.store)
    console.print(
        f"migrated: {migration.migrated_records} record(s) to store v2 at "
        f"{_project_relative(path, proj.root)}"
    )
    console.print(f"version: {resolution.version_ref}")
    console.print(
        "ledger: "
        + _project_relative(
            proj.booktx_dir / "translation-version-ledger.json", proj.root
        )
    )
    if actor is not None or harness is not None or model is not None:
        console.print(f"identity: {_project_relative(identity_path(proj), proj.root)}")


@translate_app.command(name="export")
def translate_export(  # noqa: C901
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    version_ref: str | None = typer.Option(
        None, "--version", help="Export one exact version ref such as 1.2."
    ),
    track: int | None = typer.Option(
        None,
        "--track",
        help="Export one major track, optionally with --latest-subversion.",
    ),
    latest_subversion: bool = typer.Option(
        False,
        "--latest-subversion",
        help="When exporting a track, choose the latest accepted subversion per record.",
    ),
    all_versions: bool = typer.Option(
        False,
        "--all-versions",
        help="Export all accepted versions into translated/<version-ref>/ chunk files.",
    ),
) -> None:
    """Export fully accepted store-backed chunks into translated/*.json."""
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    _require_chunks(proj)
    store = load_translation_store(proj)
    if all_versions and (version_ref is not None or track is not None):
        _die("--all-versions cannot be combined with --version or --track")
        return
    if track is not None and not latest_subversion:
        _die("--track currently requires --latest-subversion")
        return

    from booktx.io_utils import write_json_model_atomic

    def _pick_candidate(
        stored: StoredTranslationRecordV2,
    ) -> TranslationCandidate | None:
        if all_versions:
            return None
        if version_ref is not None:
            candidate = find_candidate(stored, version_ref)
            return (
                candidate
                if candidate is not None and candidate.status == "accepted"
                else None
            )
        if track is not None:
            matches = [
                candidate
                for candidate in stored.versions
                if candidate.version == track and candidate.status == "accepted"
            ]
            if not matches:
                return None
            return max(matches, key=lambda item: item.subversion)
        candidate = active_candidate(stored)
        return (
            candidate
            if candidate is not None and candidate.status == "accepted"
            else None
        )

    exported = 0
    if all_versions:
        version_map: dict[str, dict[str, list[TranslatedRecord]]] = {}
        for chunk in load_source_chunks(proj):
            for record in chunk.records:
                stored = store.records.get(record.id)
                if stored is None:
                    continue
                for candidate in stored.versions:
                    if candidate.status != "accepted":
                        continue
                    version_map.setdefault(candidate.version_ref, {}).setdefault(
                        chunk.chunk_id, []
                    ).append(
                        TranslatedRecord(
                            id=record.id,
                            version=candidate.version_ref,
                            target=candidate.target,
                        )
                    )
        for ref, chunks in version_map.items():
            if proj.translated_dir is None:
                continue
            export_dir = proj.translated_dir / ref
            export_dir.mkdir(parents=True, exist_ok=True)
            for chunk_id, records in chunks.items():
                write_json_model_atomic(
                    export_dir / f"{chunk_id}.json",
                    TranslatedChunk(chunk_id=chunk_id, records=records),
                )
                exported += 1
        console.print(f"exported: {exported} chunk file(s) to {proj.translated_dir}")
        return

    for chunk in load_source_chunks(proj):
        translated_records: list[TranslatedRecord] = []
        for record in chunk.records:
            stored = store.records.get(record.id)
            if stored is None:
                translated_records = []
                break
            picked = _pick_candidate(stored)
            if picked is None:
                translated_records = []
                break
            translated_records.append(
                TranslatedRecord(
                    id=record.id,
                    version=picked.version_ref,
                    target=picked.target,
                )
            )
        if not translated_records:
            continue
        translated_chunk = TranslatedChunk(
            chunk_id=chunk.chunk_id, records=translated_records
        )
        findings = []
        for source_record, translated_record in zip(
            chunk.records, translated_chunk.records, strict=True
        ):
            findings.extend(
                validate_record_pair(
                    source_record, translated_record, chunk.chunk_id, load_context(proj)
                )
            )
        if any(finding.severity == Severity.ERROR for finding in findings):
            continue
        if proj.translated_dir is None:
            continue
        write_json_model_atomic(
            proj.translated_dir / f"{chunk.chunk_id}.json", translated_chunk
        )
        exported += 1
    console.print(f"exported: {exported} chunk(s) to {proj.translated_dir}")


@translate_app.command(name="export-index")
def translate_export_index(  # noqa: C901
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    kind: list[str] = typer.Option(
        [],
        "--kind",
        help=(
            "Index kind to write. Repeatable. One of source, target, "
            "source-target. Defaults to all three kinds."
        ),
    ),
    fail_on_warn: bool = typer.Option(
        False,
        "--fail-on-warn",
        help="Fail when target validation warnings are present.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit command summary as JSON."),
    jsonl: bool = typer.Option(
        False,
        "--jsonl",
        help="Also write current-only JSONL aliases next to the JSON indexes.",
    ),
) -> None:
    """Export profile-local editor QA indexes.

    Writes generated, rebuildable artifacts under translations/<profile>/:
    source-index.json (source text only), target-index.json (target text only),
    and source-target-index.json (slim side-by-side view). All three are safe
    to delete and regenerate; the canonical state remains translation-store.json.
    """
    valid_kinds = {"source", "target", "source-target"}
    invalid = sorted({k for k in kind if k not in valid_kinds})
    if invalid:
        _die(
            f"invalid --kind value(s) {invalid}; expected one of {sorted(valid_kinds)}"
        )
        return
    requested = set(kind) if kind else None

    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    proj = runtime.project
    mode = runtime.mode

    def _display(path_str: str | None) -> str | None:
        if path_str is None:
            return None
        return display_path(Path(path_str), mode)

    try:
        result = export_editor_indexes(
            proj,
            kinds=requested,  # type: ignore[arg-type]  # validated against valid_kinds above; mypy cannot narrow set[str] -> set[Literal[...]]
            fail_on_warn=fail_on_warn,
            write_jsonl=jsonl,
        )
    except EditorIndexError as exc:
        # source-index may have been written before target-based export failed.
        partial = exc.result
        if partial.source_path is not None:
            console.print(
                f"exported source index: {partial.source_record_count} "
                f"record(s) to {_display(partial.source_path)}"
            )
        if as_json:
            payload = _editor_index_summary(partial, _display)
            payload["error"] = str(exc)
            console.print_json(json.dumps(payload, ensure_ascii=False))
        else:
            console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if as_json:
        console.print_json(
            json.dumps(_editor_index_summary(result, _display), ensure_ascii=False)
        )
        return

    if result.source_path is not None:
        console.print(
            f"exported source index: {result.source_record_count} record(s) "
            f"to {_display(result.source_path)}"
        )
    if result.target_path is not None:
        console.print(
            f"exported target index: {result.target_record_count} record(s) "
            f"to {_display(result.target_path)}"
        )
    if result.source_target_path is not None:
        console.print(
            f"exported source-target index: {result.source_target_record_count} "
            f"record(s) to {_display(result.source_target_path)}"
        )
    console.print(f"translated: {result.translated_count}")
    console.print(f"missing: {result.missing_count}")
    console.print(f"warnings: {result.warning_count}")
    console.print(f"errors: {result.error_count}")
    if jsonl:
        console.print("jsonl: written for requested successful indexes")


def _editor_index_summary(
    result: EditorIndexesResult, display: Callable[[str | None], str | None]
) -> dict[str, Any]:
    return {
        "source_path": display(result.source_path),
        "target_path": display(result.target_path),
        "source_target_path": display(result.source_target_path),
        "source_record_count": result.source_record_count,
        "target_record_count": result.target_record_count,
        "source_target_record_count": result.source_target_record_count,
        "translated_count": result.translated_count,
        "missing_count": result.missing_count,
        "warning_count": result.warning_count,
        "error_count": result.error_count,
        "written": list(result.written),
    }


@translate_app.command(name="task-status")
def translate_task_status(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    task_id: str = typer.Option(..., "--task-id", help="Task id to inspect."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """Report accepted vs missing progress for one persisted translation task.

    Makes interrupted translation runs diagnosable without inspecting the store
    by hand. Exits 0 only when every task record is accepted and current.
    """
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    _require_chunks(proj)
    task = _load_translation_task_or_exit(proj, task_id)
    store = load_translation_store(proj)

    accepted_ids: list[str] = []
    missing_ids: list[str] = []
    stale_ids: list[str] = []
    for record in task.records:
        stored = store.records.get(record.id)
        if stored is None:
            missing_ids.append(record.id)
            continue
        expected_sha = source_record_sha256(record.source)
        if stored.source_sha256 and stored.source_sha256 != expected_sha:
            stale_ids.append(record.id)
            continue
        candidate = active_candidate(stored)
        if candidate is None or candidate.status != "accepted":
            missing_ids.append(record.id)
            continue
        accepted_ids.append(record.id)

    total = len(task.records)
    accepted = len(accepted_ids)
    not_current = total - accepted
    first_missing = (
        missing_ids[0] if missing_ids else (stale_ids[0] if stale_ids else None)
    )
    complete = not_current == 0

    source_display = _project_relative(
        translation_task_source_block_path(proj, task.task_id), proj.root
    )
    block_ingest_display = _project_relative(
        translation_ingest_block_path(proj, task.task_id), proj.root
    )
    json_ingest_display = _project_relative(
        translation_ingest_path(proj, task.task_id), proj.root
    )
    from booktx.command_hints import translate_insert_command

    submit_hint = translate_insert_command(
        proj,
        task_id=task.task_id,
        file_path=block_ingest_display,
        input_format="block",
    )

    payload = {
        "version": 1,
        "task_id": task.task_id,
        "chapter_id": task.chapter_id,
        "chapter_title": task.chapter_title,
        "records_total": total,
        "records_accepted": accepted,
        "records_missing": len(missing_ids),
        "records_stale": len(stale_ids),
        "missing_ids": missing_ids,
        "stale_ids": stale_ids,
        "first_missing": first_missing,
        "complete": complete,
        "source_block_path": source_display,
        "block_ingest_path": block_ingest_display,
        "json_ingest_path": json_ingest_display,
        "submit_hint": submit_hint,
    }
    if as_json:
        console.print_json(json.dumps(payload, ensure_ascii=False))
        raise typer.Exit(code=0 if complete else 1)

    console.print(f"task: {task.task_id}")
    console.print(f"chapter: {task.chapter_id}  {task.chapter_title}".rstrip())
    console.print(f"records: {accepted} / {total} accepted, {not_current} missing")
    if stale_ids:
        console.print(f"stale: {len(stale_ids)} record(s) need re-translation")
    if first_missing is not None:
        console.print(f"first missing: {first_missing}")
    console.print(f"source file: {source_display}", soft_wrap=True, markup=False)
    console.print(f"ingest file: {block_ingest_display}", soft_wrap=True, markup=False)
    console.print(f"submit: {submit_hint}", soft_wrap=True, markup=False)
    raise typer.Exit(code=0 if complete else 1)


@translate_app.command(name="get-record")
def translation_get_record(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    record_ref: str = typer.Argument(..., help="Record ref such as 74@38."),
    before: int = typer.Option(0, "--before", min=0, help="Neighbor records before."),
    after: int = typer.Option(0, "--after", min=0, help="Neighbor records after."),
    version: str | None = typer.Option(
        None, "--version", help="Show one specific version."
    ),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """Inspect one source record with nearby context and available versions."""
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    selected, details = _store_record_payload(proj, record_ref)
    ordered = details["ordered"]
    ordered_ids = [record.record_id for record in ordered]
    selected_id = selected["id"]
    try:
        index = ordered_ids.index(selected_id)
    except ValueError:
        _die(f"unknown source record id: {selected_id}")
        return

    store = details["store"]

    def _record_payload(source_record: SourceRecordView) -> dict[str, Any]:
        payload = {
            "id": source_record.record_id,
            "chunk_id": source_record.chunk_id,
            "source": source_record.source,
        }
        stored = store.records.get(source_record.record_id)
        if stored is not None:
            payload["active_version"] = stored.active_version
            candidate = (
                find_candidate(stored, version)
                if version is not None
                else active_candidate(stored)
            )
            if candidate is not None:
                payload["target"] = candidate.target
                payload["status"] = candidate.status
                payload["version_ref"] = candidate.version_ref
        return payload

    before_records = [
        _record_payload(record) for record in ordered[max(0, index - before) : index]
    ]
    selected_payload = _record_payload(ordered[index])
    selected_payload["available_targets"] = details["versions"]
    selected_payload["ledger_metadata"] = _ledger_metadata_for_version(
        proj, version or selected_payload.get("active_version")
    )
    after_records = [
        _record_payload(record) for record in ordered[index + 1 : index + 1 + after]
    ]
    payload = {
        "selected_record_ref": selected_id,
        "before": before_records,
        "selected": selected_payload,
        "after": after_records,
        "available_targets": details["versions"],
        "active_version": selected_payload.get("active_version"),
        "ledger_metadata": selected_payload["ledger_metadata"],
    }
    if as_json:
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return
    for item in before_records:
        console.print(f"   {item['id']}  {item['source']}")
    console.print(f">> {selected_id}  {selected_payload['source']}")
    for candidate in details["versions"]:
        console.print(
            f"   {candidate['version_ref']} [{candidate['status']}] {candidate['target']}"
        )
    for item in after_records:
        console.print(f"   {item['id']}  {item['source']}")


@translate_app.command(name="list")
def translation_list(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    range_spec: str | None = typer.Option(
        None, "--range", help="Range spec such as 74@38..74@42."
    ),
    chapter: int | None = typer.Option(None, "--chapter", help="Chapter number."),
    version: str | None = typer.Option(
        None, "--version", help="Show a specific version."
    ),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """List records for a range or chapter in source reading order."""
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    if (range_spec is None) == (chapter is None):
        _die("use exactly one of --range or --chapter")
        return
    ordered = _ordered_source_records(proj)
    ordered_ids = [record.record_id for record in ordered]
    ctx = load_context(proj)
    bundle = build_status_snapshot(
        proj,
        context_exists=ctx is not None,
        context_ready=bool(ctx and ctx.ready),
    )
    spec = range_spec if range_spec is not None else f"chapter:{chapter}"
    try:
        selected_ids = resolve_record_range(
            spec,
            ordered_record_ids=ordered_ids,
            chapter_record_ids=bundle.index.record_ids_by_chapter,
        )
    except ValueError as exc:
        _die(str(exc))
        return
    store = load_translation_store(proj)
    payload: list[dict[str, Any]] = []
    for record in ordered:
        if record.record_id not in selected_ids:
            continue
        item = {
            "id": record.record_id,
            "chunk_id": record.chunk_id,
            "source": record.source,
        }
        stored = store.records.get(record.record_id)
        if stored is not None:
            if stored.active_version is not None:
                item["active_version"] = stored.active_version
            candidate = (
                find_candidate(stored, version)
                if version is not None
                else active_candidate(stored)
            )
            if candidate is not None:
                item["target"] = candidate.target
                item["status"] = candidate.status
                item["version_ref"] = candidate.version_ref
        payload.append(item)
    if as_json:
        console.print_json(json.dumps({"records": payload}, ensure_ascii=False))
        return
    for item in payload:
        suffix = f" [{item['version_ref']}]" if "version_ref" in item else ""
        console.print(f"{item['id']}{suffix}  {item['source']}")


@translate_app.command(name="compare")
def translation_compare(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    record_ref: str = typer.Argument(..., help="Record ref such as 74@38."),
    versions: str = typer.Option(
        ..., "--versions", help="Comma-separated version refs."
    ),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """Compare multiple stored version candidates for one record."""
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    selected, details = _store_record_payload(proj, record_ref)
    store = details["store"]
    stored = store.records.get(selected["id"])
    if stored is None:
        _die(f"record {selected['id']} has no stored translations")
        return
    from booktx.translation_store import find_review_candidate

    requested = [item.strip() for item in versions.split(",") if item.strip()]
    payload = {"record_ref": selected["id"], "comparisons": []}
    for ref in requested:
        if ref.startswith("R"):
            candidate: TranslationCandidate | TranslationReviewCandidate | None = (
                find_review_candidate(stored, ref)
            )
            kind = "review"
        else:
            candidate = find_candidate(stored, ref)
            kind = "translation"
        payload["comparisons"].append(
            {
                "ref": ref,
                "kind": kind,
                "target": candidate.target if candidate is not None else None,
                "status": candidate.status if candidate is not None else None,
            }
        )
    if as_json:
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return
    for item in payload["comparisons"]:
        console.print(f"{item['ref']} {item['kind']}: {item['target'] or '<missing>'}")


@translate_app.command(name="activate")
def translation_activate(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    record_ref: str = typer.Argument(..., help="Record ref such as 74@38."),
    version_ref: str = typer.Argument(..., help="Version ref to activate."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
) -> None:
    """Activate one stored candidate version for a single record."""
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    store = load_translation_store(proj)
    record_id = parse_record_ref(record_ref).canonical_id
    stored = store.records.get(record_id)
    if stored is None:
        _die(f"record {record_id} has no stored translations")
        return
    candidate = find_candidate(stored, version_ref)
    if candidate is None:
        _die(f"record {record_id} has no version {version_ref}")
        return
    stored.active_version = candidate.version_ref
    write_translation_store(proj, store)
    console.print(f"{record_id} -> {candidate.version_ref}")


@translate_app.command(name="review")
def translation_review(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    record_ref: str = typer.Argument(..., help="Record ref such as 74@38."),
    activate: str | None = typer.Option(
        None, "--activate", help="Optionally activate a version."
    ),
    note: str | None = typer.Option(None, "--note", help="Review note."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
) -> None:
    """Review one stored candidate and optionally activate it."""
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    store = load_translation_store(proj)
    record_id = parse_record_ref(record_ref).canonical_id
    stored = store.records.get(record_id)
    if stored is None:
        _die(f"record {record_id} has no stored translations")
        return
    candidate = (
        find_candidate(stored, activate)
        if activate is not None
        else active_candidate(stored)
    )
    if candidate is None:
        _die(f"record {record_id} has no matching review target")
        return
    if activate is not None:
        stored.active_version = candidate.version_ref
    candidate.reviewed_at = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    candidate.reviewed_by = _resolved_identity(proj).actor
    candidate.review_note = note
    write_translation_store(proj, store)
    console.print(f"{record_id} reviewed {candidate.version_ref}")


@translate_app.command(name="set-record")
def translate_set_record(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    task_id: str = typer.Option(..., "--task-id", help="Task id owning the record."),
    record_id: str = typer.Option(..., "--record-id", help="Record id to set."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    stdin: bool = typer.Option(
        False,
        "--stdin",
        help="Read the target text from stdin (default source).",
    ),
    target: str | None = typer.Option(None, "--target", help="Inline target text."),
    allow_missing_context: bool = typer.Option(
        False,
        "--allow-missing-context",
        help="Legacy override: allow set-record without a ready context.",
    ),
) -> None:
    """Commit a single translated record from stdin (or --target).

    Lets an agent safely commit one record at a time so work already written to
    translation-store.json survives interruption. Prefer this over embedding a
    whole chapter section in one shell command when truncation is a concern.
    """
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    _require_chunks(proj)
    _require_ready_context(proj, allow_missing_context=allow_missing_context)
    task = _load_translation_task_or_exit(proj, task_id)
    if record_id not in {record.id for record in task.records}:
        _die(f"record {record_id} is not part of task {task.task_id}")

    if target is not None:
        target_text = target
    elif stdin:
        target_text = sys.stdin.read()
        # Drop a single trailing newline (common shell/heredoc artifact) while
        # preserving all internal multiline text.
        if target_text.endswith("\r\n"):
            target_text = target_text[:-2]
        elif target_text.endswith("\n"):
            target_text = target_text[:-1]
    else:
        _die("provide the target text with --stdin or --target")

    summary = _project_status_snapshot(proj)
    try:
        result = accept_one_record(
            proj,
            record_id,
            target_text,
            bundle=summary,
            task=task,
            submission_profile=task.profile or None,
        )
    except SubmissionValidationError as exc:
        _render_submission_failures(exc.findings)
        raise typer.Exit(code=1) from None
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(f"accepted: 1 record, {result.target_words} target word(s)")
    if result.chapter_id:
        console.print(f"chapter: {result.chapter_id} {result.chapter_title}".rstrip())
        console.print(
            f"progress: {result.records_translated} / "
            f"{result.records_total} records translated, "
            f"{result.records_remaining} remaining"
        )


@translate_app.command(name="revise-record")
def translation_revise_record(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    record_ref: str = typer.Argument(..., help="Record id to revise."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    stdin: bool = typer.Option(
        False,
        "--stdin",
        help="Read the target text from stdin (default source).",
    ),
    target: str | None = typer.Option(None, "--target", help="Inline target text."),
    activate: bool = typer.Option(
        True,
        "--activate/--no-activate",
        help="Activate the revised version after writing.",
    ),
) -> None:
    """Revise an already accepted translation record safely.

    Validates the new target, runs staged EPUB inline-XHTML preflight
    (strict mode: warnings block), and writes through the store API.
    Never edits translation-store.json directly.
    """
    from booktx.io_utils import utc_timestamp

    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    proj = runtime.project
    _require_chunks(proj)
    _require_ready_context(proj)
    record_id = parse_record_ref(record_ref).canonical_id

    # Read target text.
    if target is not None:
        target_text = target
    elif stdin:
        target_text = sys.stdin.read()
        if target_text.endswith("\r\n"):
            target_text = target_text[:-2]
        elif target_text.endswith("\n"):
            target_text = target_text[:-1]
    else:
        _die("provide the target text with --stdin or --target")
        return  # unreachable, but keeps mypy happy

    if not target_text.strip():
        _die(f"empty target for record {record_id}")

    # Load store and check the record exists.
    store = load_translation_store(proj)
    stored = store.records.get(record_id)
    if stored is None:
        _die(f"record {record_id} has no stored translations")
    assert stored is not None

    # Reject when an active_review exists: the effective output would be
    # the review candidate, so revising the translation version is a
    # silent no-op. The user must clear or re-review explicitly.
    review = active_review_candidate(stored)
    if review is not None:
        _die(
            f"record {record_id} has active review {review.review_ref},"
            f" so changing active_version will not affect output."
            f" Use `booktx review revise-record . {record_ref} --base-review {review.review_ref}"
            f" --stdin` or `booktx review deactivate . {record_ref}`."
        )

    # Validate the record pair.
    bundle = _project_status_snapshot(proj)
    source_view = bundle.index.source_by_id.get(record_id)
    if source_view is None:
        _die(f"record {record_id} has no matching source record")
    assert source_view is not None
    source_chunks = bundle.index.source_chunks
    source_chunk = source_chunks.get(source_view.chunk_id)
    if source_chunk is None:
        _die(f"record {record_id} has no matching source chunk")
    assert source_chunk is not None
    source_record = next((r for r in source_chunk.records if r.id == record_id), None)
    if source_record is None:
        _die(f"record {record_id} not found in source chunk")
    assert source_record is not None
    translated = TranslatedRecord(id=record_id, target=target_text)
    context = load_validation_context(proj)
    pair_findings = validate_record_pair(
        source_record, translated, source_chunk.chunk_id, context
    )
    pair_errors = [f for f in pair_findings if f.severity == Severity.ERROR]
    if pair_errors:
        _render_submission_failures(pair_errors)
        raise typer.Exit(code=1)

    # Staged EPUB inline-XHTML preflight (strict: warnings also block).
    _staged_preflight_check(
        proj,
        [SubmittedRecord(id=record_id, target=target_text)],
        {record_id},
        fail_on_warnings=True,
    )

    # Write through the store API.
    resolution = resolve_current_version(proj)
    version_ref = resolution.version_ref
    ensure_store_record(
        store,
        record_id,
        source=source_view.source,
        source_sha256=source_view.source_sha256,
    )
    upsert_translation_version(
        store.records[record_id],
        version_ref,
        target_text,
        updated_at=utc_timestamp(),
        activate=activate,
    )
    write_translation_store(proj, store)

    console.print(
        f"revised: {record_id} -> {version_ref}" + (" (activated)" if activate else "")
    )
    # Suggest a scoped re-check.
    chapter_id = bundle.index.record_to_chapter.get(record_id, "")
    recheck = check_command(
        proj,
        mode=runtime.mode,
        chapter_id=chapter_id or None,
        fail_on_warnings=True,
    )
    console.print(
        f"recheck: {recheck}",
        soft_wrap=True,
        markup=False,
    )


@translate_app.command(name="revise-block")
def translation_revise_block(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    file: Path | None = typer.Option(None, "--file", help="Block submission file."),
    stdin: bool = typer.Option(
        False, "--stdin", help="Read block submission from stdin."
    ),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    output_format: str = typer.Option(
        "block", "--format", help="Submission format: block."
    ),
    activate: bool = typer.Option(
        True,
        "--activate/--no-activate",
        help="Activate revised versions after writing.",
    ),
) -> None:
    """Revise multiple accepted translation records from a block file safely."""
    if output_format != "block":
        _die("translation revise-block currently supports --format block only")
        return
    if (file is None) == (not stdin):
        _die("provide exactly one of --file or --stdin")
        return
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    proj = runtime.project
    _require_chunks(proj)
    _require_ready_context(proj)
    from booktx.io_utils import utc_timestamp
    from booktx.submissions import parse_block_submission

    text = sys.stdin.read() if stdin else file.read_text("utf-8")  # type: ignore[union-attr]
    parsed = parse_block_submission(text)
    if not parsed.records:
        _die("block submission contains no records")
        return
    submitted = [
        SubmittedRecord(id=parse_record_ref(r.id).canonical_id, target=r.target)
        for r in parsed.records
    ]
    submitted_ids = {item.id for item in submitted}
    if len(submitted_ids) != len(submitted):
        _die("duplicate record id in block submission")
        return

    store = load_translation_store(proj)
    conflicts = []
    for item in submitted:
        stored = store.records.get(item.id)
        if stored is None:
            _die(f"record {item.id} has no stored translations")
            return
        review = active_review_candidate(stored)
        if review is not None:
            conflicts.append(f"{item.id} ({review.review_ref})")
    if conflicts:
        _die(
            "records have active reviews, so revising active_version will not affect output: "
            + ", ".join(conflicts)
            + ". Use `booktx review deactivate . RECORD` or review correction commands first."
        )
        return

    bundle = _project_status_snapshot(proj)
    context = load_validation_context(proj)
    findings: list[Finding] = []
    source_views: dict[str, SourceRecordView] = {}
    for item in submitted:
        source_view = bundle.index.source_by_id.get(item.id)
        if source_view is None:
            _die(f"record {item.id} has no matching source record")
            return
        source_chunk = bundle.index.source_chunks.get(source_view.chunk_id)
        if source_chunk is None:
            _die(f"record {item.id} has no matching source chunk")
            return
        source_record = next((r for r in source_chunk.records if r.id == item.id), None)
        if source_record is None:
            _die(f"record {item.id} not found in source chunk")
            return
        findings.extend(
            validate_record_pair(
                source_record,
                TranslatedRecord(id=item.id, target=item.target),
                source_chunk.chunk_id,
                context,
            )
        )
        source_views[item.id] = source_view
    errors = [f for f in findings if f.severity == Severity.ERROR]
    if errors:
        _render_submission_failures(errors)
        raise typer.Exit(code=1)
    _staged_preflight_check(proj, submitted, submitted_ids, fail_on_warnings=True)

    version_ref = resolve_current_version(proj).version_ref
    for item in submitted:
        source_view = source_views[item.id]
        ensure_store_record(
            store,
            item.id,
            source=source_view.source,
            source_sha256=source_view.source_sha256,
        )
        upsert_translation_version(
            store.records[item.id],
            version_ref,
            item.target,
            updated_at=utc_timestamp(),
            activate=activate,
        )
    write_translation_store(proj, store)
    chapters = sorted(
        {
            bundle.index.record_to_chapter.get(item.id, "")
            for item in submitted
            if bundle.index.record_to_chapter.get(item.id)
        }
    )
    console.print(
        f"revised: {len(submitted)} record(s) -> {version_ref}"
        + (" (activated)" if activate else "")
    )
    if chapters:
        console.print("affected chapters: " + ", ".join(chapters))
        for chapter_id in chapters:
            console.print(
                "recheck: "
                + check_command(
                    proj,
                    mode=runtime.mode,
                    chapter_id=chapter_id,
                    fail_on_warnings=True,
                ),
                soft_wrap=True,
                markup=False,
            )


# --- validate ----------------------------------------------------------------


@app.command()
def validate(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    include_inactive: bool = typer.Option(
        False,
        "--include-inactive",
        help="Also validate inactive historical translation versions.",
    ),
    fail_on_history_warnings: bool = typer.Option(
        False,
        "--fail-on-history-warnings",
        help=(
            "Imply --include-inactive and exit non-zero on inactive-version warnings."
        ),
    ),
    all_versions_strict: bool = typer.Option(
        False,
        "--all-versions-strict",
        help="Imply --include-inactive and keep inactive-version errors fatal.",
    ),
    chapter: str | None = typer.Option(
        None, "--chapter", help="Scope to a specific chapter id."
    ),
    task_id: str | None = typer.Option(
        None, "--task-id", help="Scope to a specific task id."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
    fail_on_warnings: bool = typer.Option(
        False,
        "--fail-on-warnings",
        help="Exit non-zero when validation reports warnings.",
    ),
) -> None:
    """Validate translated chunks against the translation contract."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    proj = runtime.project

    report = validate_project(
        proj,
        include_inactive_versions=(
            include_inactive or fail_on_history_warnings or all_versions_strict
        ),
        all_versions_strict=all_versions_strict,
        chapter_id=chapter,
        task_id=task_id,
    )
    out = write_report(proj, report)

    if as_json:
        console.print_json(json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
    else:
        _render_validate_findings(report)
        console.print(
            f"chunks_checked={report.chunks_checked} "
            f"passed={report.chunks_passed} "
            f"errors={len(report.errors)} warnings={len(report.warnings)} "
            f"missing={report.chunks_missing_translation}"
        )
        console.print("[dim]report:[/dim] ", end="")
        console.print(display_path(out, runtime.mode), soft_wrap=True, markup=False)
    if validation_exits_nonzero(
        report,
        fail_on_warnings=fail_on_warnings,
        fail_on_history_warnings=fail_on_history_warnings,
    ):
        raise typer.Exit(code=1)
        raise typer.Exit(code=1)


def _render_validate_findings(report: ValidationReport) -> None:
    if not report.findings:
        return
    for f in report.findings:
        _render_finding(f)


def _epub_output_audit_findings(
    proj: Project,
) -> tuple[list[Finding], dict[str, object]]:
    """Non-writing audit of the expected EPUB output path.

    Returns validation-style findings plus a JSON payload. Errors clearly when
    no output exists or the project is not an EPUB project.
    """
    from booktx.build import _output_path
    from booktx.config import find_source_file
    from booktx.epub_output_policy import (
        PolicyError,
        audit_epub_output_policy,
        resolve_epub_output_policy,
    )

    findings: list[Finding] = []
    if proj.config.format != "epub":
        findings.append(
            Finding(
                chunk_id="epub_output",
                severity=Severity.ERROR,
                rule="not_an_epub_project",
                message="--epub-output is only valid for EPUB projects.",
            )
        )
        return findings, {"findings": [f.as_dict() for f in findings]}

    try:
        source = find_source_file(proj, persist_discovery=False)
    except Exception as exc:  # noqa: BLE001
        findings.append(
            Finding(
                chunk_id="epub_output",
                severity=Severity.ERROR,
                rule="source_not_found",
                message=str(exc),
            )
        )
        return findings, {"findings": [f.as_dict() for f in findings]}

    out_path = _output_path(proj, source, suffix=".epub")
    payload: dict[str, object] = {"output_path": str(out_path)}
    if not out_path.is_file():
        findings.append(
            Finding(
                chunk_id="epub_output",
                severity=Severity.ERROR,
                rule="epub_output_missing",
                message=(
                    f"no built EPUB output found at {out_path}; "
                    "run `booktx build` first."
                ),
            )
        )
        payload["findings"] = [f.as_dict() for f in findings]
        return findings, payload

    try:
        policy = resolve_epub_output_policy(proj)
        report = audit_epub_output_policy(out_path, extraction_hrefs=[], policy=policy)
    except PolicyError as exc:
        findings.append(
            Finding(
                chunk_id="epub_output",
                severity=Severity.ERROR,
                rule="epub_output_audit_failed",
                message=str(exc),
            )
        )
        payload["findings"] = [f.as_dict() for f in findings]
        return findings, payload

    if report.applied:
        findings.append(
            Finding(
                chunk_id="epub_output",
                severity=Severity.INFO,
                rule="epub_output_policy_applied",
                message=(
                    f"EPUB output policy applied: language={report.language!r}, "
                    f"hyphenation={report.hyphenation!r}, "
                    f"patched_xhtml={len(report.patched_xhtml_entries)}, "
                    f"css_injected={len(report.css_injected_entries)}"
                ),
            )
        )
    for w in report.warnings:
        findings.append(
            Finding(
                chunk_id="epub_output",
                severity=Severity.WARN,
                rule="epub_output_css_conflict",
                message=f"{w['entry']}: {w['declaration']}",
                document_href=w.get("entry", ""),
            )
        )
    payload["findings"] = [f.as_dict() for f in findings]
    payload["policy"] = {
        "applied": report.applied,
        "language_policy": report.language_policy,
        "language": report.language,
        "hyphenation": report.hyphenation,
        "patched_xhtml_entries": list(report.patched_xhtml_entries),
        "css_injected_entries": list(report.css_injected_entries),
        "fixed_layout_skipped_entries": list(report.fixed_layout_skipped_entries),
        "warnings": list(report.warnings),
    }
    return findings, payload


@app.command()
def check(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    chapter: str | None = typer.Option(
        None, "--chapter", help="Scope to a specific chapter id."
    ),
    task_id: str | None = typer.Option(
        None, "--task-id", help="Scope to a specific task id."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
    fail_on_warnings: bool = typer.Option(
        True,
        "--fail-on-warnings/--no-fail-on-warnings",
        help="Exit non-zero when validation reports warnings.",
    ),
    epub_output: bool = typer.Option(
        False,
        "--epub-output",
        help="Audit the existing expected EPUB output for policy compliance without building.",
    ),
) -> None:
    """Scoped build-preflight check for inline XHTML and translation contracts.

    A human-friendly alias for scoped validation + EPUB inline-XHTML preflight.
    Prefer this after each chapter translation and before build.

    ``--epub-output`` audits the expected EPUB output path produced by a prior
    build against the resolved EPUB output policy. It does not build or modify
    the EPUB and emits the same findings in text and JSON modes.
    """
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    proj = runtime.project

    if epub_output:
        audit_findings, audit_payload = _epub_output_audit_findings(proj)
        if as_json:
            console.print_json(json.dumps(audit_payload, indent=2, ensure_ascii=False))
        else:
            _render_validate_findings(type("R", (), {"findings": audit_findings})())
            console.print(
                f"errors={sum(1 for f in audit_findings if f.severity == Severity.ERROR)} "
                f"warnings={sum(1 for f in audit_findings if f.severity == Severity.WARN)}"
            )
        has_blocking = any(f.severity == Severity.ERROR for f in audit_findings) or (
            fail_on_warnings
            and any(f.severity == Severity.WARN for f in audit_findings)
        )
        if has_blocking:
            raise typer.Exit(code=1)
        return

    report = validate_project(proj, chapter_id=chapter, task_id=task_id)

    if as_json:
        console.print_json(json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
    else:
        _render_validate_findings(report)
        console.print(
            f"chunks_checked={report.chunks_checked} "
            f"passed={report.chunks_passed} "
            f"errors={len(report.errors)} warnings={len(report.warnings)} "
            f"missing={report.chunks_missing_translation}"
        )
    if validation_exits_nonzero(report, fail_on_warnings=fail_on_warnings):
        raise typer.Exit(code=1)


@translate_app.command(name="audit-inline")
def translate_audit_inline(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    chapter: str | None = typer.Option(
        None, "--chapter", help="Scope to a specific chapter id."
    ),
    task_id: str | None = typer.Option(
        None, "--task-id", help="Scope to a specific task id."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """Audit active translations for required EPUB inline XHTML semantics."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    from booktx.inline_audit import audit_inline_xhtml

    result = audit_inline_xhtml(runtime.project, chapter_id=chapter, task_id=task_id)
    if json_output:
        console.print_json(json.dumps(result.as_dict(), ensure_ascii=False))
        return
    console.print("Inline XHTML audit")
    console.print(f"records with inline source: {result.records_with_inline_source}")
    console.print(f"valid active targets: {result.valid_active_targets}")
    console.print(f"missing inline tags: {result.missing_inline_tags}")
    console.print(f"invalid XHTML targets: {result.invalid_xhtml_targets}")
    console.print(f"opaque changed: {result.opaque_changed}")
    console.print(f"needs review: {result.needs_review}")


@translate_app.command(name="migrate-inline-xhtml")
def translate_migrate_inline_xhtml(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Report safe migrations without writing translated chunks.",
    ),
    write_safe: bool = typer.Option(
        False, "--write-safe", help="Write only safe automatic migrations."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """Safely migrate legacy targets for simple EPUB inline XHTML cases."""
    if dry_run and write_safe:
        _die("choose either --dry-run or --write-safe")
        return
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    from booktx.inline_audit import migrate_inline_xhtml

    report = migrate_inline_xhtml(runtime.project, write_safe=write_safe)
    if json_output:
        console.print_json(json.dumps(report, ensure_ascii=False))
        return
    console.print("Inline XHTML migration")
    console.print(f"safe mappings: {len(report['mapped_records'])}")
    console.print(f"needs review: {len(report['targets_requiring_review'])}")
    console.print(f"written: {report['written']}")


# --- build -------------------------------------------------------------------


@app.command()
def build(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    require_complete: bool = typer.Option(
        False,
        "--require-complete",
        help="Fail when any record is untranslated or invalid.",
    ),
    require_reviewed: bool = typer.Option(
        False,
        "--require-reviewed",
        help="Fail when required review coverage is missing or stale.",
    ),
) -> None:
    """Rebuild the translated document into ``output/``."""
    try:
        runtime = _load_runtime_or_exit(
            project_dir, profile=profile, require_profile=True
        )
        proj = runtime.project
        result = build_project(
            proj,
            require_complete=require_complete,
            require_reviewed=require_reviewed,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    except BuildError as exc:
        _die(str(exc))
        return

    console.print(
        f"[green]Built[/green] {result.format} -> "
        f"{display_path(result.output_path, runtime.mode)}"
    )
    if result.report:
        changed_entries = result.report.get("changed_entries", [])
        console.print(
            "  changed_entries="
            f"{_changed_entry_count(changed_entries)} "
            f"replacements={result.report.get('replacement_count', 0)} "
            f"unresolved_tokens={result.report.get('unresolved_token_count', 0)}"
        )


@app.command(name="pass-through")
def pass_through_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str = typer.Option(..., "--profile", help="Pass-through profile name."),
    create: bool = typer.Option(
        False, "--create", help="Create the pass-through profile if missing."
    ),
    select: bool = typer.Option(False, "--select", help="Select the created profile."),
    output_filename: str | None = typer.Option(
        None, "--output-filename", help="Output filename for a newly created profile."
    ),
    force: bool = typer.Option(
        True, "--force/--no-force", help="Refresh existing generated translated chunks."
    ),
    prune_stale: bool = typer.Option(
        True,
        "--prune-stale/--keep-stale",
        help="Remove stale generated translated chunks.",
    ),
    clear_store: bool = typer.Option(
        False,
        "--clear-store",
        help="Clear store records that would override generated chunks.",
    ),
    no_build: bool = typer.Option(
        False, "--no-build", help="Only generate and validate translated chunks."
    ),
    allow_warnings: bool = typer.Option(
        False, "--allow-warnings", help="Do not fail on validation warnings."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """Generate identity translated chunks, validate coverage, and rebuild output.

    This produces a source-as-target reconstruction fixture. It is not a real
    translation: each record target equals its source text. Compare the output
    against the source with a diff viewer to detect reconstruction drift.
    """
    runtime = _load_runtime_or_exit(project_dir, require_profile=False)
    _reject_if_isolated(runtime)
    try:
        proj = ensure_pass_through_profile(
            runtime.project.root,
            profile,
            create=create,
            select=select,
            output_filename=output_filename,
        )
        result = run_pass_through(
            proj,
            force=force,
            prune_stale=prune_stale,
            clear_store=clear_store,
            build=not no_build,
            allow_warnings=allow_warnings,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    except BuildError as exc:
        _die(str(exc))
        return

    payload = {
        "profile": result.profile,
        "chunks_written": result.chunks_written,
        "records_written": result.records_written,
        "stale_removed": result.stale_removed,
        "translated_dir": str(result.translated_dir),
        "validation": {
            "errors": len(result.validation_report.errors),
            "warnings": len(result.validation_report.warnings),
            "missing": result.validation_report.chunks_missing_translation,
        },
        "output_path": str(result.build_result.output_path)
        if result.build_result
        else None,
        "format": result.build_result.format if result.build_result else None,
    }
    if as_json:
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return

    console.print(f"pass-through profile: {result.profile}")
    console.print(f"translated chunks: {result.chunks_written}")
    console.print(f"translated records: {result.records_written}")
    console.print(f"removed stale translated chunks: {result.stale_removed}")
    console.print(
        "validation: passed "
        f"errors={len(result.validation_report.errors)} "
        f"warnings={len(result.validation_report.warnings)} "
        f"missing={result.validation_report.chunks_missing_translation}"
    )
    if result.build_result is not None:
        console.print(
            f"[green]Built[/green] {result.build_result.format} -> "
            f"{result.build_result.output_path}"
        )


def _changed_entry_count(changed_entries: object) -> int | object:
    if isinstance(changed_entries, list):
        return len(changed_entries)
    return changed_entries


# --- top-level callback (version) --------------------------------------------


@app.callback()
def _main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Print booktx version and exit.",
        is_eager=True,
    ),
) -> None:
    """booktx root options."""
    if version:
        console.print(__version__)
        raise typer.Exit


# --- qa scan command -----------------------------------------------------


@app.command(name="qa-scan")
def qa_scan_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    target_only: bool = typer.Option(
        False, "--target-only", help="Search targets only, omit source."
    ),
    forbidden: bool = typer.Option(
        False, "--forbidden", help="Check for forbidden glossary terms in targets."
    ),
    glossary: bool = typer.Option(
        False, "--glossary", help="Report glossary target mismatches."
    ),
    target_contains: str | None = typer.Option(
        None,
        "--target-contains",
        help="Literal substring to find in effective targets.",
    ),
    pattern: str | None = typer.Option(
        None, "--pattern", help="Regex pattern to match in targets."
    ),
    language_leftovers: str | None = typer.Option(
        None, "--language-leftovers", help="Detect source-language leftovers (e.g. en)."
    ),
    chapter: str | None = typer.Option(
        None, "--chapter", help="Scope to one chapter id."
    ),
    jsonl: bool = typer.Option(
        False, "--jsonl", help="Output one JSON object per finding per line."
    ),
) -> None:
    """Scan effective targets for QA findings without scripting."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    proj = runtime.project

    from booktx.qa_scan import qa_scan

    bundle = _project_status_snapshot(proj)

    try:
        result = qa_scan(
            proj,
            bundle,
            chapter_id=chapter,
            target_only=target_only,
            forbidden=forbidden,
            glossary=glossary,
            target_contains=target_contains,
            pattern=pattern,
            language_leftovers=language_leftovers,
        )
    except ValueError as exc:
        _die(str(exc))
        return

    if jsonl:
        import json as _json

        for finding in result.findings:
            console.print(
                _json.dumps(finding.as_dict(), ensure_ascii=False),
                soft_wrap=True,
                markup=False,
            )
    else:
        console.print(
            f"scanned {result.records_scanned} records, "
            f"{result.findings_count} findings"
        )
        for finding in result.findings:
            console.print(
                f"  {finding.id} [{finding.rule}] {finding.term}"
                f" -> {finding.target[:80]}..."
                if len(finding.target) > 80
                else f"  {finding.id} [{finding.rule}] {finding.term} -> {finding.target}",
                soft_wrap=True,
                markup=False,
            )


# --- translation search command -------------------------------------------


@translate_app.command(name="search")
def translation_search_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    target: str | None = typer.Option(
        None, "--target", help="Literal text to find in effective targets."
    ),
    source: str | None = typer.Option(
        None, "--source", help="Literal text to find in source text."
    ),
    chapter: str | None = typer.Option(
        None, "--chapter", help="Scope to one chapter id."
    ),
    record: str | None = typer.Option(
        None, "--record", help="Show one specific record id."
    ),
    before: int = typer.Option(0, "--before", help="Context records before the match."),
    after: int = typer.Option(0, "--after", help="Context records after the match."),
    jsonl: bool = typer.Option(
        False, "--jsonl", help="Output one JSON object per match per line."
    ),
) -> None:
    """Search effective translations without scripting against translation-store.json."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    proj = runtime.project

    from booktx.config import load_translation_store
    from booktx.translation_store import effective_target_candidate

    bundle = _project_status_snapshot(proj)
    store = load_translation_store(proj)
    store_records = store.records
    source_by_id = bundle.index.source_by_id

    chapters_to_search = (
        [chapter] if chapter is not None else list(bundle.index.record_ids_by_chapter)
    )

    if record is not None:
        stored = store_records.get(record)
        if stored is None:
            _die(f"record {record} not found in store")
            return
        eff = effective_target_candidate(stored)
        source_view = source_by_id.get(record)
        if jsonl:
            import json as _json

            console.print_json(
                _json.dumps(
                    {
                        "id": record,
                        "source": source_view.source if source_view else "",
                        "target": eff.target if eff else "",
                        "effective_ref": (
                            getattr(eff, "review_ref", None)
                            or getattr(eff, "version_ref", None)
                            or ""
                        ),
                    },
                    ensure_ascii=False,
                )
            )
        else:
            console.print(
                f"record: {record}"
                f" chapter={bundle.index.record_to_chapter.get(record, '?')}"
            )
            console.print(f"source: {source_view.source if source_view else ''}")
            console.print(f"target: {eff.target if eff else ''}")
            if eff:
                ref = getattr(eff, "review_ref", None) or getattr(
                    eff, "version_ref", "?"
                )
                console.print(f"ref: {ref}")
        return

    def _neighbor_target(records: dict[str, Any], rid: str) -> str:
        stored = records.get(rid)
        if stored is None:
            return ""
        eff = effective_target_candidate(stored)
        return eff.target if eff is not None else ""

    matches: list[dict[str, object]] = []
    for cid in chapters_to_search:
        flat = list(bundle.index.record_ids_by_chapter.get(cid, []))
        for idx, record_id in enumerate(flat):
            stored = store_records.get(record_id)
            if stored is None:
                continue
            eff = effective_target_candidate(stored)
            if eff is None:
                continue
            source_view = source_by_id.get(record_id)
            source_text = source_view.source if source_view else ""
            target_text = eff.target

            matched = False
            if target is not None and target.lower() in target_text.lower():
                matched = True
            if source is not None and source.lower() in source_text.lower():
                matched = True

            if matched:
                match = {
                    "id": record_id,
                    "chapter_id": cid,
                    "source": source_text
                    if not (target is not None and source is None)
                    else "",
                    "target": target_text,
                    "effective_ref": (
                        getattr(eff, "review_ref", None)
                        or getattr(eff, "version_ref", None)
                        or ""
                    ),
                }

                if before > 0 or after > 0:
                    before_ids = flat[max(0, idx - before) : idx]
                    after_ids = flat[idx + 1 : idx + 1 + after]
                    before_records = [
                        {
                            "id": rid,
                            "target": _neighbor_target(store_records, rid),
                        }
                        for rid in before_ids
                    ]
                    after_records = [
                        {
                            "id": rid,
                            "target": _neighbor_target(store_records, rid),
                        }
                        for rid in after_ids
                    ]
                    match["before"] = before_records
                    match["after"] = after_records

                matches.append(match)

    if jsonl:
        import json as _json

        for match in matches:
            console.print(
                _json.dumps(match, ensure_ascii=False),
                soft_wrap=True,
                markup=False,
            )
    else:
        console.print(f"found {len(matches)} matches")
        for match in matches:
            rec_id = match.get("id", "")
            target_text = str(match.get("target", ""))
            disp = f"{rec_id}: {target_text[:100]}"
            if len(disp) < len(target_text):
                disp += "..."
            console.print(f"  {disp}", soft_wrap=True, markup=False)


# --- root + doctor commands (extracted to commands/root.py in slice 8) -------


@app.command(name="whoami")
def whoami(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """Show resolved translation identity and project status."""
    _print_identity(project_dir, profile=profile, as_json=as_json)


@app.command(name="mode")
def mode_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory or profile root."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """Show how booktx resolved the current working path."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=False)
    payload = {
        "mode": runtime.mode.kind,
        "profile": runtime.mode.profile_name,
        "profiles_visible": not runtime.mode.isolated_output,
        "cross_profile_access": not runtime.mode.isolated_output,
        "safe_for_model_evaluation": runtime.mode.isolated_output,
        "source_access": runtime.mode.source_access,
    }
    if as_json:
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return
    console.print(f"mode: {payload['mode']}")
    if payload["profile"]:
        console.print(f"profile: {payload['profile']}")
    console.print(f"profiles visible: {'yes' if payload['profiles_visible'] else 'no'}")
    console.print(
        f"cross-profile access: {'yes' if payload['cross_profile_access'] else 'no'}"
    )
    console.print(
        "safe for model evaluation: "
        f"{'yes' if payload['safe_for_model_evaluation'] else 'no'}"
    )


@doctor_app.command(name="isolation")
def doctor_isolation_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory or profile root."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """Check whether the current path is ready for isolated evaluation."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=False)
    proj = runtime.project
    marker_exists = bool(
        runtime.mode.profile_root
        and (runtime.mode.profile_root / ".booktx-profile.json").is_file()
    )
    profile_local_context = bool(
        proj.context_json_path is not None
        and runtime.mode.profile_root is not None
        and proj.context_json_path.parent == runtime.mode.profile_root
    )
    profile_local_store = bool(
        proj.store_path is not None
        and runtime.mode.profile_root is not None
        and proj.store_path.parent == runtime.mode.profile_root
    )
    profile_local_ledger = bool(
        proj.ledger_path is not None
        and runtime.mode.profile_root is not None
        and proj.ledger_path.parent == runtime.mode.profile_root
    )
    redacted_samples = [
        display_path(proj.root, runtime.mode),
        display_path(proj.chunks_dir, runtime.mode),
        display_path(proj.profile_dir or proj.root, runtime.mode),
    ]
    path_redaction_pass = all(
        not sample.startswith("/")
        and "../" not in sample
        and (runtime.mode.profile_name or "") not in sample.replace(".", "")
        for sample in redacted_samples[:2]
    )
    source_available = bool(proj.chunks())
    passed = (
        runtime.mode.isolated_output
        and marker_exists
        and source_available
        and profile_local_context
        and profile_local_store
        and profile_local_ledger
        and path_redaction_pass
    )
    payload = {
        "isolation": "PASS" if passed else "FAIL",
        "mode": runtime.mode.kind,
        "profile": runtime.mode.profile_name,
        "source_broker": "available" if source_available else "unavailable",
        "cross_profile_commands": "blocked"
        if runtime.mode.isolated_output
        else "available",
        "path_redaction": "PASS" if path_redaction_pass else "FAIL",
        "source_access": runtime.mode.source_access,
    }
    if as_json:
        console.print_json(json.dumps(payload, ensure_ascii=False))
    else:
        console.print(f"isolation: {payload['isolation']}")
        console.print(f"mode: {payload['mode']}")
        if payload["profile"]:
            console.print(f"profile: {payload['profile']}")
        console.print(f"source broker: {payload['source_broker']}")
        console.print(f"cross-profile commands: {payload['cross_profile_commands']}")
        console.print(f"path redaction: {payload['path_redaction']}")
    if not passed:
        raise typer.Exit(code=1)


def main() -> None:
    """Console-script entry point (used by pyproject [project.scripts])."""
    # Typer raises typer.Exit for normal command exits; surface its code.
    try:
        app()
    except typer.Exit as exc:
        sys.exit(exc.exit_code)


__all__ = ["app", "main"]
