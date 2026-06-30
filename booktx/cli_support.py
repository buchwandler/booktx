"""Shared CLI-layer helpers used by ``booktx/cli.py`` and the per-slice
command modules under ``booktx/commands/``.

This module exists to break the import cycle that would otherwise arise when
command modules need the CLI helpers (console, error-to-exit mapping, project
loading, legacy arg parsing) while ``booktx/cli.py`` imports the command
modules to register them. By factoring the shared helpers into this neutral
module:

- ``booktx/cli.py`` imports them at the top and imports command modules at
  the top (no cycle, no ``E402``).
- ``booktx/commands/*.py`` import them from here (never from ``booktx.cli``).

The boundary guard in ``tests/test_cli_command_boundary.py`` only scans
``booktx/commands/``; this module may import ``booktx.config`` /
``booktx.runtime`` / ``booktx.identity`` freely because it is *not* a command
module. Workflow functions under ``booktx/workflows/`` own the actual
mutations; the helpers here only do CLI concerns: rendering, exit-code
mapping, and runtime/project loading.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from rich.console import Console

from booktx.context import (
    load_context,
    unapproved_required_questions,
    unresolved_required_questions,
)
from booktx.errors import BooktxError
from booktx.identity import identity_payload
from booktx.runtime import RuntimeContext, resolve_runtime

if TYPE_CHECKING:
    # ``Project`` lives in ``booktx.config``; imported under TYPE_CHECKING so
    # this module never imports config at runtime (keeps import order simple).
    from booktx.acceptance import SubmittedRecord
    from booktx.config import Project
    from booktx.status import ChapterProgress, ProfilesOverview, StatusBundle
    from booktx.validate import Finding
    # ``Project`` lives in ``booktx.config``; imported under TYPE_CHECKING so
    # this module never imports config at runtime (keeps import order simple).

# Shared console instance for all CLI output.
console = Console()


def _isolated_mode_error() -> str:
    return (
        "command is not available in profile-root isolated mode.\n"
        "Run this from the project root for collaborative/admin workflows."
    )


def _reject_if_isolated(runtime: RuntimeContext) -> None:
    if runtime.mode.isolated_output:
        _die(_isolated_mode_error())


def _render_profiles_overview_human(overview: ProfilesOverview) -> None:
    console.print(f"project: {overview.project}")
    if overview.source:
        console.print(f"source: {overview.source}")
    if overview.source_records:
        console.print(f"source records: {overview.source_records}")
    if not overview.profiles:
        console.print("profiles: none")
        return
    console.print("profiles:")
    for item in overview.profiles:
        marker = "*" if item.active else " "
        coverage = (
            f"translated={item.translated_records}/{item.total_records}"
            if item.total_records
            else "translated=0/0"
        )
        console.print(
            f"  {marker} {item.profile}   kind={item.kind}  "
            f"target={item.target_locale or item.target_language}  "
            f"model={item.model or 'human'}  {coverage}"
        )
    if overview.active_profile:
        console.print()
        console.print(f"active profile: {overview.active_profile}")


def _load_context_status(proj: Project) -> tuple[bool, bool]:
    try:
        ctx = load_context(proj)
    except Exception as exc:  # noqa: BLE001
        _die(f"translation context is invalid: {exc}")
    return (ctx is not None, bool(ctx and ctx.ready))


def _project_status_snapshot(proj: Project) -> StatusBundle:
    """Build the typed status snapshot + runtime index for ``proj``.

    Thin wrapper over :func:`booktx.status.build_status_snapshot`; the CLI
    owns the invalid-context error UX here.
    """
    from booktx.status import build_status_snapshot

    context_exists, context_ready = _load_context_status(proj)
    return build_status_snapshot(
        proj, context_exists=context_exists, context_ready=context_ready
    )


def _die(message: str, code: int = 1) -> None:
    """Print an error and exit with ``code``."""
    console.print(f"[red]error:[/red] {message}")
    raise typer.Exit(code=code)


def _handle_booktx_error(exc: BooktxError) -> None:
    _die(str(exc))


def _load_runtime_or_exit(
    project_dir: Path,
    *,
    profile: str | None = None,
    require_profile: bool = False,
) -> RuntimeContext:
    try:
        return resolve_runtime(
            project_dir,
            profile=profile,
            require_profile=require_profile,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        raise typer.Exit(code=1) from exc


def _resolve_project_value_args(
    arg1: str,
    arg2: str | None,
    *,
    value_name: str,
    project_dir: Path | None = None,
) -> tuple[Path, str]:
    """Accept VALUE, VALUE PROJECT_DIR, or PROJECT_DIR VALUE."""
    if project_dir is not None:
        if arg2 is not None:
            _die(f"--project cannot be combined with a second positional {value_name}")
        return project_dir.expanduser(), arg1

    if arg2 is None:
        return Path("."), arg1

    p1 = Path(arg1).expanduser()
    p2 = Path(arg2).expanduser()
    p1_is_project = (p1 / ".booktx" / "config.toml").is_file() or (
        p1 / ".booktx" / "source-config.toml"
    ).is_file()
    p2_is_project = (p2 / ".booktx" / "config.toml").is_file() or (
        p2 / ".booktx" / "source-config.toml"
    ).is_file()

    if p1_is_project and not p2_is_project:
        return p1, arg2
    if p2_is_project and not p1_is_project:
        return p2, arg1
    return p1, arg2


def _render_identity_human(payload: dict[str, Any]) -> None:
    context_payload = payload["context"]
    store_payload = payload["store"]
    context_state = {
        "ready": "READY",
        "not_ready": "NOT_READY",
        "missing": "MISSING",
        "invalid": "INVALID",
    }[str(context_payload["status"])]
    rows = [
        ("actor", payload["actor"]),
        ("harness", payload["harness"]),
        ("model", payload["model"]),
        ("active_version", payload["active_version"] or "none"),
        ("context", f"{context_state} {context_payload['path']}"),
        ("context_sha256", context_payload["sha256"] or "none"),
        ("source_sha256", payload["source_sha256"] or "none"),
        (
            "store_version",
            store_payload["version"]
            if store_payload["version"] is not None
            else "none",
        ),
        (
            "store_records",
            store_payload["record_count"]
            if store_payload["record_count"] is not None
            else "none",
        ),
    ]
    width = max(len(label) for label, _ in rows)
    console.print(f"booktx identity: {payload['project_dir']}", soft_wrap=True)
    for label, value in rows:
        console.print(f"{label + ':':<{width + 2}} {value}", soft_wrap=True)


def _print_identity(project_dir: Path, *, profile: str | None, as_json: bool) -> None:
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    payload = identity_payload(runtime.project, mode=runtime.mode)
    if as_json:
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return
    _render_identity_human(payload)


def _load_project_or_exit(
    project_dir: Path,
    *,
    profile: str | None = None,
    require_profile: bool = False,
) -> Project:
    return _load_runtime_or_exit(
        project_dir,
        profile=profile,
        require_profile=require_profile,
    ).project


# --- shared CLI-layer guards and rendering helpers -------------------------
# These helpers were factored out of ``booktx/cli.py`` so that the per-slice
# command modules under ``booktx/commands/`` can import them without creating a
# cycle (commands cannot import ``booktx.cli``). They wrap CLI concerns:
# validation-to-exit mapping, console rendering, and runtime-aware output.


def _maybe_auto_export_indexes(
    proj: Project, *, export_index: bool = False, trigger: str = ""
) -> None:
    """Auto-export editor indexes after accepted changes if configured."""
    from booktx.editor_indexes import export_editor_indexes

    cfg = proj.profile_config
    if cfg is None:
        return
    indexes_cfg = cfg.indexes
    if indexes_cfg is None and not export_index:
        return

    should_export = export_index
    if indexes_cfg is not None:
        if trigger == "review" and indexes_cfg.auto_export_after_review:
            should_export = True
        elif trigger == "translation" and indexes_cfg.auto_export_after_insert:
            should_export = True

    if not should_export:
        return

    try:
        result = export_editor_indexes(
            proj,
            write_jsonl=indexes_cfg.write_jsonl if indexes_cfg is not None else False,
        )
        console.print(
            f"indexes: exported {result.translated_count} translated, "
            f"{result.missing_count} missing",
        )
    except Exception as exc:
        # Non-fatal: don't block the main operation because of index export.
        console.print(f"[yellow]warning:[/yellow] index export failed: {exc}")


def _require_ready_context(
    proj: Project, *, allow_missing_context: bool = False
) -> bool:
    """Return True when context was checked and should be printed."""
    if allow_missing_context:
        return False
    ctx = load_context(proj)
    if ctx is None or not ctx.ready:
        _die("translation context is missing or not ready.\nRun: booktx context init .")
        return False
    unresolved = unresolved_required_questions(ctx)
    if unresolved and not ctx.ready_forced:
        ids = ", ".join(q.id for q in unresolved)
        _die(
            f"translation context has unapproved required answers: {ids}\n"
            "Run: booktx context questionnaire . and approve "
            "answers before translating."
        )
    unapproved = unapproved_required_questions(ctx)
    if unapproved and not ctx.ready_forced:
        ids = ", ".join(q.id for q in unapproved)
        _die(
            f"translation context has unapproved required answers: {ids}\n"
            "Run: booktx context questionnaire . and approve "
            "answers before translating."
        )
    return True


def _require_chunks(proj: Project) -> list[Path]:
    chunk_paths = proj.chunks()
    if not chunk_paths:
        _die("No source chunks found. Run: booktx extract .")
    return chunk_paths


def _require_no_source_drift(proj: Project) -> None:
    """Fail if the source file changed since the last extraction."""
    from booktx.config import current_source_sha256, extracted_source_sha256

    extracted = extracted_source_sha256(proj)
    if extracted and extracted != current_source_sha256(proj):
        _die(
            "source file has changed since last extraction; "
            "run 'booktx extract' to update chunks before translating"
        )


def _selected_chapter(
    bundle: StatusBundle, chapter_id: str | None
) -> ChapterProgress | None:
    from booktx.status import selected_chapter

    chapter = selected_chapter(bundle, chapter_id)
    if chapter is None and chapter_id is not None:
        _die(f"unknown chapter id: {chapter_id}")
    return chapter


def _project_relative(path: Path, root: Path) -> str:
    """Backward-compatible alias for :func:`booktx.tasks.project_relative`."""
    from booktx.tasks import project_relative

    return project_relative(path, root)


def _render_submission_failures(findings: list[Finding]) -> None:
    from booktx.rendering import render_submission_failures

    render_submission_failures(findings)


def _truncate(text: str, limit: int = 120) -> str:
    """Return a single-line excerpt of ``text``, truncated for display."""
    one_line = " ".join(text.split())
    if len(one_line) <= limit:
        return one_line
    return one_line[:limit].rstrip() + "\u2026"


def _render_finding(f: Finding) -> None:
    color = "red" if f.severity == "error" else "yellow"
    if f.record_id:
        loc = f" [{f.record_id}]"
    elif f.record_ids:
        loc = f" records={f.record_ids}"
    else:
        loc = ""
    scope_marker = ""
    if f.candidate_scope == "inactive" and f.candidate_ref:
        kind = f.candidate_kind or "translation"
        scope_marker = f" [{kind} {f.candidate_ref}]"
    console.print(
        f"[{color}]{f.severity}[/{color}] {f.chunk_id}{loc} "
        f"{f.rule}{scope_marker}: {f.message}"
    )
    if f.chapter_id:
        title = f" {f.chapter_title}".rstrip() if f.chapter_title else ""
        console.print(f"  chapter: {f.chapter_id}{title}")
    span_parts = []
    if f.span_index is not None:
        span_parts.append(f"span={f.span_index}")
    if f.block_id:
        span_parts.append(f"block={f.block_id}")
    if span_parts:
        console.print(f"  {' '.join(span_parts)}")
    if f.document_href:
        console.print(f"  href: {f.document_href}")


def _staged_preflight_check(
    proj: Project,
    submitted_records: list[SubmittedRecord],
    submitted_ids: set[str],
    *,
    fail_on_warnings: bool = False,
) -> None:
    """Run EPUB inline-XHTML preflight on staged submitted records.

    Layers submitted records on top of current effective translations and runs
    the preflight (via :mod:`booktx.acceptance_preflight`). If inline-XHTML
    errors (or, when ``fail_on_warnings=True``, warnings) are found, renders
    them and exits non-zero BEFORE the store is written.
    """
    from booktx.acceptance_preflight import run_staged_preflight
    from booktx.validate import Finding

    blocking = run_staged_preflight(
        proj,
        submitted_records,
        submitted_ids,
        fail_on_warnings=fail_on_warnings,
    )
    if not blocking:
        return
    for f in blocking:
        _render_finding(
            Finding(
                chunk_id=f.chunk_id or "epub-preflight",
                severity=f.severity,
                rule=f.rule,
                message=f.message,
                record_id=f.record_id,
                record_ids=list(f.record_ids),
                chapter_id=f.chapter_id,
                chapter_title=f.chapter_title,
                span_index=f.span_index,
                block_id=f.block_id,
                document_href=f.document_href,
                source=f.source,
                target=f.target,
            )
        )
        fix_record = f.record_id or (f.record_ids[0] if f.record_ids else "")
        if fix_record:
            console.print(
                f"  fix: booktx translation revise-record . {fix_record} --stdin",
                soft_wrap=True,
                markup=False,
            )
    raise typer.Exit(code=1)
