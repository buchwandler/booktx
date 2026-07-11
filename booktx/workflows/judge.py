"""Workflow layer for judge/selection-profile commands."""

from __future__ import annotations

# ruff: noqa: E501
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from booktx.acceptance import SubmissionValidationError
from booktx.agents_md import AGENTS_FILENAME, inspect_agents_md
from booktx.config import (
    _err,
    judge_ingest_decisions_path,
    load_judge_task,
    load_profile_config,
    load_profile_project,
    load_translation_selection_ledger,
    profile_dir,
    write_profile_config,
)
from booktx.context import (
    TranslationContext,
    load_context,
    unapproved_required_questions,
    unresolved_required_questions,
)
from booktx.context_sync import apply_context_sync, plan_context_sync
from booktx.errors import BooktxError
from booktx.io_utils import write_text_atomic
from booktx.judge_acceptance import (
    SubmittedJudgeRecord,
    accept_judge_submission,
    parse_judge_block_submission,
    parse_judge_decisions_submission,
    parse_judge_json_submission,
)
from booktx.judge_provenance import audit_revision_provenance
from booktx.judge_sources import (
    configured_selection_sources,
    load_live_judge_source_views,
    load_snapshot_judge_source_views,
    sync_judge_source_snapshots,
    validate_judge_sources_snapshot,
)
from booktx.judge_store import (
    parse_sources_csv,
    record_has_candidate_gap,
    resolve_selection_sources,
    selected_record_ids,
)
from booktx.judge_tasks import create_judge_task, render_judge_ingest
from booktx.models import (
    JudgeTask,
    JudgeTaskCandidate,
    JudgeTaskRecord,
    Record,
    SelectionConfig,
    TranslatedRecord,
)
from booktx.placeholders import TOKEN_RE, collect_tokens
from booktx.selection_mode import (
    is_revision_selection_profile,
    revision_focus,
    selection_purpose,
)
from booktx.termbase import publish_termbase_snapshot
from booktx.termbase_match import iter_boundary_matches
from booktx.termbase_tasking import validate_termbase_record_pair
from booktx.validate import Severity, load_validation_context, validate_record_pair
from booktx.workflows.context import mark_ready_workflow
from booktx.workflows.profile import create_profile_workflow

if TYPE_CHECKING:
    from booktx.config import Project
    from booktx.runtime import RuntimeContext
    from booktx.status import StatusBundle

__all__ = [
    "build_judge_status_workflow",
    "create_judge_profile_workflow",
    "create_next_judge_task_workflow",
    "create_record_judge_task_workflow",
    "judge_task_edit_path",
    "judge_task_block_paths",
    "judge_task_decisions_path",
    "judge_task_json_path",
    "accept_judge_submission_workflow",
    "accept_identical_judge_records_workflow",
    "reset_judge_ingest_workflow",
    "prepare_judge_isolation_workflow",
    "prefill_judge_policy_fixes_workflow",
    "finish_chapter_plan_workflow",
    "load_judge_task",
]


def create_judge_profile_workflow(
    project_dir: Path,
    profile_name: str,
    *,
    target_language: str,
    target_locale: str | None,
    sources_csv: str | None,
    model: str | None,
    context_from: str | None,
    purpose: str = "compare",
    revision_focus: str = "general",
) -> Project:
    # Validate purpose and sources, and construct the intended SelectionConfig,
    # BEFORE create_profile_workflow writes any profile directory or list entry.
    # An invalid command must not leave a profile behind.
    purpose_value = _parse_selection_purpose(purpose)
    focus_value = _parse_revision_focus(revision_focus)
    source_profiles = parse_sources_csv(sources_csv)
    if not source_profiles:
        raise _err("judge_sources_missing", "--sources must not be empty")
    if purpose_value == "revise" and len(source_profiles) != 1:
        raise _err(
            "judge_revision_source_count",
            "selection.purpose=revise requires exactly one source profile",
        )
    if purpose_value != "revise" and focus_value != "general":
        raise _err(
            "judge_revision_focus",
            "revision focus is only valid with --purpose revise",
        )
    selection_cfg = SelectionConfig(
        sources=source_profiles,
        allow_edited_targets=True,
        require_all_sources=(purpose_value == "revise"),
        purpose=purpose_value,
        revision_focus=focus_value,
    )

    project = create_profile_workflow(
        project_dir,
        profile_name,
        target_language=target_language,
        target_locale=target_locale,
        model=model,
        kind="selection",
    )
    cfg = load_profile_config(project.root, profile_name)
    cfg.selection = selection_cfg
    write_profile_config(project.root, cfg)
    # Return the project with an up-to-date profile_config so callers
    # (e.g. command modules) can access .selection without re-loading from config.
    project.profile_config = cfg
    if context_from:
        _sync_judge_context_from_source(project.root, context_from, profile_name)
    return project


def _sync_judge_context_from_source(
    root: Path, source_profile: str, target_profile: str
) -> dict[str, Any]:
    plan = plan_context_sync(
        root,
        source_profile=source_profile,
        target_profiles=[target_profile],
        all_compatible=False,
        sections={"glossary", "style", "global-rules", "questions"},
        terms=[],
        question_ids=[],
        conflict="replace",
        same_locale=True,
        include_pass_through=False,
        include_selection=True,
        allow_not_ready=False,
        init_missing_context=True,
    )
    if plan.blocked:
        raise _err(
            "judge_context_from_blocked",
            f"context import from {source_profile} to {target_profile} is blocked",
        )
    applied = apply_context_sync(plan, root)
    target = load_profile_project(root, target_profile)
    ctx = load_context(target)
    ready = False
    if ctx is not None:
        if not unresolved_required_questions(ctx) and not unapproved_required_questions(
            ctx
        ):
            mark_ready_workflow(target, ctx, force=False, reason="")
            ready = True
    return {
        "source_profile": source_profile,
        "target_profile": target_profile,
        "targets": [item.profile for item in applied.targets],
        "ready": ready,
    }


def prepare_judge_isolation_workflow(
    project_dir: Path,
    *,
    profile: str,
    context_from: str | None,
    write: bool,
    replace_unmanaged: bool,
) -> dict[str, Any]:
    """One-command judge-isolation preparation.

    Validates the selection profile, requires an existing ready context
    (never approves or marks it ready), preflights snapshot inputs and the
    target ``AGENTS.md`` ownership before mutation, syncs source snapshots,
    and writes the judge-specific isolated ``AGENTS.md``. Dry-run by default;
    ``write=True`` performs the reconciliation. Safely rerunnable: the snapshot
    sync is idempotent and the AGENTS.md write is atomic, so a failure after
    publication but before the AGENTS.md write can be retried unchanged.
    """
    from booktx.runtime import resolve_runtime
    from booktx.workflows.agents import write_agents_workflow

    runtime = resolve_runtime(project_dir, profile=profile, require_profile=True)
    if runtime.mode.kind == "profile-root":
        raise _err(
            "judge_prepare_isolation_requires_project_root",
            "run `booktx judge prepare-isolation` from the project root",
        )
    project = runtime.project
    cfg = project.profile_config
    if cfg is None or cfg.kind != "selection":
        raise _err("judge_profile_kind", "judge isolation requires a selection profile")
    ctx = load_context(project)
    if context_from and (ctx is None or not ctx.ready):
        _sync_judge_context_from_source(project.root, context_from, profile)
        project = load_profile_project(project.root, profile)
        ctx = load_context(project)
    if ctx is None or not ctx.ready:
        suggested_source = context_from or configured_selection_sources(project)[0]
        raise _err(
            "judge_isolation_context_not_ready",
            "selection profile context is missing or not ready; "
            "sync approved policy first, for example: "
            f"`booktx context sync . --from {suggested_source} --to {profile} "
            "--section glossary --section style --section global-rules "
            "--section questions --write`, then `booktx context mark-ready "
            f". --profile {profile}`",
        )
    configured = configured_selection_sources(project)
    # Preflight before any mutation: plan the snapshot and inspect AGENTS.md.
    plan = sync_judge_source_snapshots(project, source_profiles=configured, write=False)
    target_agents = profile_dir(project.root, profile) / AGENTS_FILENAME
    inspection = inspect_agents_md(target_agents)
    if (
        inspection.state in ("unmanaged", "managed-malformed", "symlink")
        and not replace_unmanaged
    ):
        raise _err(
            "agents_unmanaged_target",
            f"target AGENTS.md for {profile} is {inspection.state}; "
            "pass --replace-unmanaged to overwrite",
        )
    payload: dict[str, Any] = {
        "profile": profile,
        "source_profiles": list(configured),
        "snapshot_id": plan.snapshot_id,
        "manifest_sha256": plan.manifest_sha256,
        "changed": plan.changed,
        "agents_state": inspection.state,
        "write": write,
    }
    if not write:
        payload["next"] = (
            f"booktx judge prepare-isolation . --profile {profile} --write"
        )
        return payload
    sync_result = sync_judge_source_snapshots(
        project, source_profiles=configured, write=True
    )
    termbase_snapshot_paths = publish_termbase_snapshot(project)
    agents_result = write_agents_workflow(
        project_dir,
        mode="isolated",
        profile=profile,
        replace_unmanaged=replace_unmanaged,
    )
    payload["snapshot_id"] = sync_result.snapshot_id
    payload["manifest_sha256"] = sync_result.manifest_sha256
    payload["changed"] = sync_result.changed
    payload["agents_written"] = [str(p) for p in agents_result.written]
    payload["termbase_snapshots"] = [str(path) for path in termbase_snapshot_paths]
    payload["next"] = f"cd translations/{profile} && booktx judge status ."
    return payload


def resolve_sources_csv(sources_csv: str | None) -> list[str]:
    from booktx.judge_store import parse_sources_csv

    values = parse_sources_csv(sources_csv)
    if not values:
        raise _err("judge_sources_missing", "--sources must not be empty")
    return values


def _parse_selection_purpose(value: str) -> Literal["compare", "revise"]:
    """Parse and validate a ``--purpose`` value before any mutation."""
    normalized = (value or "").strip().lower()
    if normalized not in ("compare", "revise"):
        raise _err(
            "judge_purpose",
            "invalid selection purpose; expected 'compare' or 'revise'",
        )
    return normalized  # type: ignore[return-value]


def _parse_revision_focus(value: str) -> Literal["general", "grammar"]:
    """Parse and validate a ``--revision-focus`` value before any mutation."""
    normalized = (value or "").strip().lower()
    if normalized not in ("general", "grammar"):
        raise _err(
            "judge_revision_focus",
            "invalid revision focus; expected 'general' or 'grammar'",
        )
    return normalized  # type: ignore[return-value]


def _reject_revision_deterministic(proj: Project, command: str) -> None:
    """Hard-reject a deterministic selection command in revise mode.

    Revise profiles require explicit copy/edited decisions for every
    record; the deterministic shortcuts (accept-identical, sweep-identical,
    prefill-policy-fixes) must fail before any task creation or file write and
    must leave no artifacts behind.
    """
    if is_revision_selection_profile(proj):
        raise _err(
            "judge_revision_explicit_decisions_required",
            f"{command} is disabled for selection.purpose=revise; ",
            "run judge next and insert explicit copy/edited decisions",
        )


def _source_access_from_runtime(runtime: RuntimeContext) -> Literal["live", "snapshot"]:
    """ "snapshot" in profile-root mode, "live" in project-root mode."""
    return "snapshot" if runtime.mode.kind == "profile-root" else "live"


def _snapshot_status(
    proj: Project,
    configured: list[str],
    sources_csv: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Classify the active snapshot and load its views.

    Returns ``(snapshot_info, source_views)``. The snapshot is reported as
    ``valid``/``missing``/``invalid``; on any failure no views are loaded and
    the returned info carries a sanitized message (never a parent/sibling path).
    """
    from booktx.errors import BooktxError
    from booktx.judge_sources import judge_sources_manifest_sha256

    explicit = parse_sources_csv(sources_csv) if sources_csv else []
    requested = explicit or configured
    try:
        manifest = validate_judge_sources_snapshot(proj)
        views = load_snapshot_judge_source_views(proj, requested)
    except BooktxError as exc:
        state = "missing" if exc.code == "judge_source_snapshot_missing" else "invalid"
        return (
            {"state": state, "message": str(exc), "generated_at": None, "profiles": []},
            {},
        )
    counts: list[dict[str, Any]] = [
        {
            "profile": name,
            "records_total": manifest.profiles[name].records_total,
            "effective_candidates_total": manifest.profiles[
                name
            ].effective_candidates_total,
        }
        for name in manifest.source_profiles
    ]
    try:
        manifest_sha = judge_sources_manifest_sha256(proj)
    except BooktxError:
        manifest_sha = ""
    return (
        {
            "state": "valid",
            "snapshot_id": manifest.snapshot_id,
            "generated_at": manifest.generated_at,
            "manifest_sha256": manifest_sha,
            "source_profiles": list(manifest.source_profiles),
            "profiles": counts,
            "message": None,
        },
        views,
    )


def _build_status_next_command(
    runtime: RuntimeContext,
    proj: Project,
    source_profiles: list[str],
    next_chapter: str | None,
    source_access: Literal["live", "snapshot"],
    snapshot_usable: bool,
    context_ready: bool,
) -> str:
    if next_chapter is None:
        return ""
    if not context_ready:
        return ""
    if source_access == "snapshot":
        if not snapshot_usable:
            return ""
        return (
            f"booktx judge next . --unit chapter --chapter {next_chapter} "
            "--max-records 8 --format decisions"
        )
    sources_arg = ",".join(source_profiles)
    return (
        f"booktx judge next . --profile {proj.profile} --sources {sources_arg} "
        f"--unit chapter --chapter {next_chapter} --max-records 8 --format block"
    )


def _build_sweep_hint_command(
    runtime: RuntimeContext,
    proj: Project,
    source_profiles: list[str],
    from_chapter: str,
    to_chapter: str,
    source_access: Literal["live", "snapshot"],
) -> str:
    """Format a profile-root-safe ``judge sweep-identical`` hint command."""
    if source_access == "snapshot":
        return (
            f"booktx judge sweep-identical . --from-chapter {from_chapter} "
            f"--to-chapter {to_chapter}"
        )
    sources_arg = ",".join(source_profiles)
    return (
        f"booktx judge sweep-identical . --profile {proj.profile} "
        f"--sources {sources_arg} --from-chapter {from_chapter} "
        f"--to-chapter {to_chapter}"
    )


def build_chapter_next_command(
    proj: Project,
    runtime: RuntimeContext,
    *,
    chapter: str,
    status_payload: dict[str, Any],
) -> str:
    """Format-aware `judge next` command for a specific chapter.

    Reuses the same builder as the global status next command so the scoped
    command respects snapshot (decisions) vs live (block) mode instead of
    hard-coding a format. Returns "" when context is not ready or the
    snapshot is unusable, mirroring `_build_status_next_command`.
    """
    context_info = status_payload.get("context") or {}
    context_ready = bool(context_info.get("ready"))
    snapshot_info = status_payload.get("snapshot")
    snapshot_usable = snapshot_info is None or snapshot_info.get("state") == "valid"
    source_access_raw = status_payload.get("source_access")
    if source_access_raw not in {"live", "snapshot"}:
        return ""
    source_access = cast(Literal["live", "snapshot"], source_access_raw)
    source_profiles = list(status_payload.get("source_profiles") or [])
    return _build_status_next_command(
        runtime,
        proj,
        source_profiles,
        chapter,
        source_access,
        snapshot_usable,
        context_ready,
    )


def _judge_context_status(proj: Project) -> dict[str, Any]:
    ctx = load_context(proj)
    if ctx is None:
        return {
            "exists": False,
            "ready": False,
            "open_required_questions": [],
            "unapproved_required_questions": [],
        }
    open_required = [question.id for question in unresolved_required_questions(ctx)]
    unapproved_required = [
        question.id for question in unapproved_required_questions(ctx)
    ]
    ready = ctx.ready and not open_required and not unapproved_required
    return {
        "exists": True,
        "ready": ready,
        "open_required_questions": open_required,
        "unapproved_required_questions": unapproved_required,
    }


def build_judge_status_workflow(
    proj: Project,
    runtime: RuntimeContext,
    *,
    bundle: StatusBundle,
    sources_csv: str | None,
) -> dict[str, Any]:
    source_access = _source_access_from_runtime(runtime)
    purpose = selection_purpose(proj)
    focus = revision_focus(proj)
    if purpose == "revise":
        selected_ids = set(audit_revision_provenance(proj).valid_record_ids)
    else:
        selected_ids = set(selected_record_ids(proj))
    context_info = _judge_context_status(proj)
    blocked_by: list[str] = []
    if not context_info["exists"]:
        blocked_by.append("context_missing")
    elif not context_info["ready"]:
        blocked_by.append("context_not_ready")
    snapshot_info: dict[str, Any] | None = None
    source_views: dict[str, Any] = {}
    source_profiles: list[str]
    if source_access == "snapshot":
        source_profiles = configured_selection_sources(proj)
        snapshot_info, source_views = _snapshot_status(
            proj, source_profiles, sources_csv
        )
    else:
        source_profiles = resolve_selection_sources(proj, sources_csv)
        source_views = load_live_judge_source_views(proj, source_profiles)
    chapters: list[dict[str, Any]] = []
    candidate_gaps = 0
    next_chapter: str | None = None
    snapshot_usable = snapshot_info is None or snapshot_info.get("state") == "valid"
    for chapter_id, record_ids in bundle.index.record_ids_by_chapter.items():
        total = len(record_ids)
        selected = sum(1 for record_id in record_ids if record_id in selected_ids)
        if snapshot_usable:
            gaps = sum(
                1
                for record_id in record_ids
                if record_has_candidate_gap(source_views, record_id)
            )
        else:
            gaps = 0
        candidate_gaps += gaps
        if next_chapter is None and selected < total:
            next_chapter = chapter_id
        chapter = bundle.index.chapters_by_id[chapter_id]
        chapters.append(
            {
                "chapter_id": chapter_id,
                "title": chapter.title,
                "selected_records": selected,
                "total_records": total,
                "missing_records": total - selected,
                "candidate_gap_records": gaps,
            }
        )
    next_command = _build_status_next_command(
        runtime,
        proj,
        source_profiles,
        next_chapter,
        source_access,
        snapshot_usable,
        bool(context_info["ready"]),
    )
    if source_access == "snapshot" and snapshot_info is not None:
        snapshot_state = snapshot_info.get("state")
        if snapshot_state == "missing":
            blocked_by.append("snapshot_missing")
        elif snapshot_state == "invalid":
            blocked_by.append("snapshot_invalid")
    if purpose == "revise" and candidate_gaps:
        blocked_by.append("revision_source_incomplete")
    if blocked_by:
        next_command = ""
    decisions_copy = 0
    decisions_edited = 0
    decision_edit_rate = 0.0
    if purpose == "revise":
        ledger = load_translation_selection_ledger(proj)
        for record_id in selected_ids:
            decision = ledger.records.get(record_id)
            if decision is None:
                continue
            if decision.decision_kind == "copy":
                decisions_copy += 1
            elif decision.decision_kind == "edited":
                decisions_edited += 1
        decided_total = decisions_copy + decisions_edited
        if decided_total:
            decision_edit_rate = round(decisions_edited / decided_total, 4)
    incomplete_chapters = [
        entry["chapter_id"]
        for entry in chapters
        if int(entry.get("missing_records", 0)) > 0
    ]
    sweep_hint = ""
    if (
        purpose != "revise"
        and context_info["ready"]
        and not blocked_by
        and snapshot_usable
        and len(incomplete_chapters) >= 2
    ):
        sweep_hint = _build_sweep_hint_command(
            runtime,
            proj,
            source_profiles,
            incomplete_chapters[0],
            incomplete_chapters[-1],
            source_access,
        )
    return {
        "profile": proj.profile or "",
        "source_profiles": source_profiles,
        "records_selected": len(selected_ids),
        "records_total": bundle.snapshot.totals.records_total,
        "records_missing": bundle.snapshot.totals.records_total - len(selected_ids),
        "records_with_candidate_gaps": candidate_gaps,
        "decisions_copy": decisions_copy,
        "decisions_edited": decisions_edited,
        "decision_edit_rate": decision_edit_rate,
        "chapters": chapters,
        "next_command": next_command,
        "sweep_hint": sweep_hint,
        "mode": runtime.mode.kind,
        "source_access": source_access,
        "selection_purpose": purpose,
        "revision_focus": focus,
        "context": context_info,
        "blocked_by": blocked_by,
        "snapshot": snapshot_info,
    }


def create_next_judge_task_workflow(
    proj: Project,
    *,
    bundle: StatusBundle,
    sources_csv: str | None,
    chapter: str | None,
    max_words: int,
    max_records: int | None,
    max_rendered_lines: int | None,
    require_all_sources: bool,
    source_access: Literal["live", "snapshot"] = "live",
) -> JudgeTask:
    if source_access == "snapshot":
        source_profiles = configured_selection_sources(proj)
        if sources_csv:
            # profile-root --sources may only select an order-preserving subset;
            # the snapshot loader enforces that. Use the explicit list as-is.
            source_profiles = parse_sources_csv(sources_csv)
    else:
        source_profiles = resolve_selection_sources(proj, sources_csv)
    effective_require_all_sources = _effective_require_all_sources(
        proj, require_all_sources
    )
    try:
        return create_judge_task(
            proj,
            bundle,
            source_profiles=source_profiles,
            chapter_id=chapter,
            record_id=None,
            max_words=max_words,
            max_records=max_records,
            max_rendered_lines=max_rendered_lines,
            require_all_sources=effective_require_all_sources,
            source_access=source_access,
        )
    except ValueError as exc:
        raise _err("judge_next", str(exc)) from exc


def _effective_require_all_sources(proj: Project, cli_value: bool) -> bool:
    if is_revision_selection_profile(proj):
        return True
    cfg = proj.profile_config
    selection = cfg.selection if cfg is not None else None
    return cli_value or (
        selection.require_all_sources if selection is not None else False
    )


def create_record_judge_task_workflow(
    proj: Project,
    *,
    bundle: StatusBundle,
    sources_csv: str | None,
    record_id: str,
    require_all_sources: bool,
    source_access: Literal["live", "snapshot"] = "live",
) -> JudgeTask:
    if source_access == "snapshot":
        source_profiles = configured_selection_sources(proj)
        if sources_csv:
            source_profiles = parse_sources_csv(sources_csv)
    else:
        source_profiles = resolve_selection_sources(proj, sources_csv)
    effective_require_all_sources = _effective_require_all_sources(
        proj, require_all_sources
    )
    try:
        return create_judge_task(
            proj,
            bundle,
            source_profiles=source_profiles,
            chapter_id=None,
            record_id=record_id,
            max_words=10**9,
            require_all_sources=effective_require_all_sources,
            source_access=source_access,
        )
    except ValueError as exc:
        raise _err("judge_record", str(exc)) from exc


def judge_task_block_paths(proj: Project, task: JudgeTask) -> tuple[str, str]:
    from booktx.config import judge_ingest_block_path, judge_task_source_block_path

    judge_task_id = task.judge_task_id
    return (
        str(judge_task_source_block_path(proj, judge_task_id)),
        str(judge_ingest_block_path(proj, judge_task_id)),
    )


def judge_task_edit_path(proj: Project, task: JudgeTask, output_format: str) -> str:
    if output_format == "block":
        return judge_task_block_paths(proj, task)[1]
    if output_format == "decisions":
        return judge_task_decisions_path(proj, task)
    if output_format == "json":
        return judge_task_json_path(proj, task)
    raise _err("judge_format", "--format must be block, decisions, or json")


def judge_task_decisions_path(proj: Project, task: JudgeTask) -> str:
    return str(judge_ingest_decisions_path(proj, task.judge_task_id))


def judge_task_json_path(proj: Project, task: JudgeTask) -> str:
    from booktx.config import judge_ingest_json_path

    return str(judge_ingest_json_path(proj, task.judge_task_id))


def accept_judge_submission_workflow(
    proj: Project,
    *,
    bundle: StatusBundle,
    judge_task_id: str,
    file: Path,
    input_format: str,
    runtime: RuntimeContext | None = None,
) -> dict[str, Any]:
    task = load_judge_task(proj, judge_task_id)
    if task is None:
        raise _err("judge_task_not_found", f"judge task not found: {judge_task_id}")
    text = file.read_text("utf-8")
    if input_format == "json":
        payload_task_id, submitted = parse_judge_json_submission(text)
    elif input_format == "decisions":
        payload_task_id, submitted = parse_judge_decisions_submission(text)
    elif input_format == "block":
        payload_task_id, submitted = parse_judge_block_submission(text)
    else:
        raise _err("judge_format", "--format must be block, decisions, or json")
    if payload_task_id and payload_task_id != judge_task_id:
        raise _err(
            "judge_task_id_mismatch",
            f"submission judge_task_id {payload_task_id} does not match "
            f"{judge_task_id}",
        )
    enforce_snapshot = runtime is not None and runtime.mode.kind == "profile-root"
    try:
        result = accept_judge_submission(
            proj,
            task,
            submitted,
            bundle=bundle,
            enforce_snapshot=enforce_snapshot,
            input_format=input_format,
        )
    except SubmissionValidationError as exc:
        raise BooktxError(
            "judge_submission_validation",
            "judge submission failed validation: "
            + "; ".join(f.message for f in exc.findings),
        ) from exc
    return {
        "accepted_records": result.accepted_records,
        "version_refs": result.version_refs,
        "record_findings": [
            {
                "severity": f.severity,
                "rule": f.rule,
                "message": f.message,
                "record_id": f.record_id,
            }
            for f in result.record_findings
        ],
        "chapter_id": task.chapter_id,
        "judge_task_id": task.judge_task_id,
    }


def reset_judge_ingest_workflow(
    proj: Project,
    *,
    judge_task_id: str,
    output_format: str,
    write: bool,
) -> dict[str, Any]:
    task = load_judge_task(proj, judge_task_id)
    if task is None:
        raise _err("judge_task_not_found", f"judge task not found: {judge_task_id}")
    edit_path = Path(judge_task_edit_path(proj, task, output_format))
    if write:
        write_text_atomic(edit_path, render_judge_ingest(task, output_format))
    return {
        "judge_task_id": judge_task_id,
        "format": output_format,
        "path": edit_path,
        "write": write,
    }


def accept_identical_judge_records_workflow(
    proj: Project,
    *,
    bundle: StatusBundle,
    sources_csv: str | None,
    chapter: str | None,
    max_words: int,
    max_records: int | None,
    max_rendered_lines: int | None,
    require_all_sources: bool,
    source_access: Literal["live", "snapshot"],
    write: bool,
) -> dict[str, Any]:
    _reject_revision_deterministic(proj, "accept-identical")
    task = create_next_judge_task_workflow(
        proj,
        bundle=bundle,
        sources_csv=sources_csv,
        chapter=chapter,
        max_words=max_words,
        max_records=max_records,
        max_rendered_lines=max_rendered_lines,
        require_all_sources=require_all_sources,
        source_access=source_access,
    )
    submitted: list[SubmittedJudgeRecord] = []
    for record in task.records:
        if not record.candidates:
            continue
        if len(record.candidates) < 2:
            continue
        if any(candidate.validation_findings for candidate in record.candidates):
            continue
        if len({candidate.target_sha256 for candidate in record.candidates}) != 1:
            continue
        submitted.append(
            SubmittedJudgeRecord(
                id=record.id,
                selected=record.candidates[0].label,
                decision_kind="copy",
                target="",
                reason="All available candidates are identical and pass validation.",
            )
        )
    accepted = 0
    version_refs: list[str] = []
    if write and submitted:
        result = accept_judge_submission(
            proj,
            task,
            submitted,
            bundle=bundle,
            enforce_snapshot=source_access == "snapshot",
            input_format="decisions",
        )
        accepted = result.accepted_records
        version_refs = result.version_refs
    return {
        "judge_task_id": task.judge_task_id,
        "task": task,
        "matched_records": len(submitted),
        "accepted_records": accepted,
        "version_refs": version_refs,
        "write": write,
    }


def _chapter_missing_records(
    proj: Project, bundle: StatusBundle, chapter_id: str
) -> int:
    """Count records in ``chapter_id`` not yet selected into the store."""
    record_ids = bundle.index.record_ids_by_chapter.get(chapter_id, [])
    selected = selected_record_ids(proj)
    return sum(1 for rid in record_ids if rid not in selected)


def sweep_identical_judge_records_workflow(
    proj: Project,
    *,
    bundle: StatusBundle,
    sources_csv: str | None,
    from_chapter: str,
    to_chapter: str,
    max_records: int | None,
    require_all_sources: bool,
    source_access: Literal["live", "snapshot"],
    write: bool,
) -> dict[str, Any]:
    """Accept identical records across a contiguous chapter range in process.

    Iterates chapters from ``from_chapter`` to ``to_chapter`` (inclusive) in
    source order and accepts identical valid candidates for each chapter when
    ``write`` is set. Stops at the first chapter that still has missing
    records after identical acceptance (it needs LLM judging) and returns its
    id as ``stopped_chapter``. A chapter with no missing records is treated as
    already complete. ``BooktxError(code=judge_next)`` for a chapter is treated
    as already-complete; every other error propagates unchanged (no masking).

    The caller (command layer) owns building the scoped/global ``judge next``
    command because that needs the runtime context.
    """
    _reject_revision_deterministic(proj, "sweep-identical")
    ordered_chapters = list(bundle.index.record_ids_by_chapter.keys())
    try:
        start = ordered_chapters.index(from_chapter)
        end = ordered_chapters.index(to_chapter)
    except ValueError as exc:
        raise _err(
            "judge_sweep",
            f"sweep-identical chapter range {from_chapter}..{to_chapter} "
            "is not contained in the source chapter order",
        ) from exc
    if start > end:
        raise _err(
            "judge_sweep",
            f"--from-chapter {from_chapter} must not come after "
            f"--to-chapter {to_chapter} in source order",
        )
    range_chapters = ordered_chapters[start : end + 1]

    rows: list[dict[str, Any]] = []
    stopped_chapter: str | None = None
    for chapter_id in range_chapters:
        try:
            payload = accept_identical_judge_records_workflow(
                proj,
                bundle=bundle,
                sources_csv=sources_csv,
                chapter=chapter_id,
                max_words=10**9,
                max_records=max_records,
                max_rendered_lines=None,
                require_all_sources=require_all_sources,
                source_access=source_access,
                write=write,
            )
        except BooktxError as exc:
            if exc.code == "judge_next":
                rows.append(
                    {
                        "chapter_id": chapter_id,
                        "matched_records": 0,
                        "accepted_records": 0,
                        "remaining_records": 0,
                        "status": "complete",
                    }
                )
                continue
            raise
        accepted = int(payload["accepted_records"])
        matched = int(payload["matched_records"])
        current_missing = _chapter_missing_records(proj, bundle, chapter_id)
        # After this step: with --write, identicals are now selected so the
        # store already reflects the gap; in a dry run, subtract what would
        # have been accepted.
        remaining = current_missing if write else max(current_missing - matched, 0)
        if remaining > 0:
            rows.append(
                {
                    "chapter_id": chapter_id,
                    "matched_records": matched,
                    "accepted_records": accepted,
                    "remaining_records": remaining,
                    "status": "needs_judging",
                }
            )
            stopped_chapter = chapter_id
            break
        rows.append(
            {
                "chapter_id": chapter_id,
                "matched_records": matched,
                "accepted_records": accepted,
                "remaining_records": 0,
                "status": "complete",
            }
        )

    return {
        "rows": rows,
        "stopped_chapter": stopped_chapter,
        "write": write,
    }


def _prefill_dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        value = raw.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


@dataclass(slots=True)
class _PrefillDecision:
    record_id: str
    selected: str
    decision_kind: Literal["copy", "edited"]
    target: str
    reason: str


@dataclass(slots=True)
class _PrefillHint:
    record_id: str
    summary: str


def _prefill_forbidden_hits(
    target: str, record: JudgeTaskRecord
) -> list[tuple[str, str, bool]]:
    """Literal forbidden targets present in ``target`` with their replacement.

    Returns ``(forbidden_value, approved_replacement, case_sensitive)`` tuples.
    Only entries with exactly one approved replacement are considered, so an
    ambiguous entry never produces a hit.
    """
    hits: list[tuple[str, str, bool]] = []
    for entry in record.applicable_glossary:
        approved = _prefill_dedupe([entry.target or "", *entry.target_variants])
        if len(approved) != 1:
            continue
        replacement = approved[0]
        for forbidden in _prefill_dedupe(entry.forbidden_targets):
            if iter_boundary_matches(
                target, forbidden, case_sensitive=entry.case_sensitive
            ):
                hits.append((forbidden, replacement, entry.case_sensitive))
    for snapshot in record.applicable_termbase:
        approved = _prefill_dedupe(snapshot.target_preferred)
        if len(approved) != 1:
            continue
        replacement = approved[0]
        for forbidden in _prefill_dedupe(snapshot.target_forbidden):
            if iter_boundary_matches(
                target, forbidden, case_sensitive=snapshot.case_sensitive
            ):
                hits.append((forbidden, replacement, snapshot.case_sensitive))
    return hits


def _prefill_span_overlaps_token(text: str, span: tuple[int, int]) -> bool:
    for match in TOKEN_RE.finditer(text):
        if span[0] < match.end() and match.start() < span[1]:
            return True
    return False


def _prefill_edited_target_blocks(
    record: JudgeTaskRecord,
    edited: str,
    validation_context: TranslationContext | None,
) -> bool:
    """True when the edited target would fail judge-insert validation."""
    source_record = Record(id=record.id, source=record.source)
    translated = TranslatedRecord(id=record.id, target=edited)
    findings = list(
        validate_record_pair(
            source_record, translated, record.chunk_id, validation_context
        )
    )
    findings.extend(
        validate_termbase_record_pair(
            source_text=record.source,
            target_text=edited,
            snapshots=record.applicable_termbase,
            chunk_id=record.chunk_id,
            record_id=record.id,
        )
    )
    blocking_rules = {"glossary_target_missing", "forbidden_term_used"}
    return any(
        finding.severity == Severity.ERROR or finding.rule in blocking_rules
        for finding in findings
    )


def _prefill_try_repair(
    record: JudgeTaskRecord,
    candidate: JudgeTaskCandidate,
    validation_context: TranslationContext | None,
) -> _PrefillDecision | None:
    target = candidate.target
    hits = _prefill_forbidden_hits(target, record)
    if len(hits) != 1:
        return None
    forbidden, replacement, case_sensitive = hits[0]
    matches = list(
        iter_boundary_matches(target, forbidden, case_sensitive=case_sensitive)
    )
    if len(matches) != 1:
        return None
    span = matches[0].span()
    # XHTML/placeholder safety: never auto-edit a span that touches a
    # placeholder token (inline non-translatable spans encode XHTML markup).
    if _prefill_span_overlaps_token(target, span):
        return None
    edited = target[: span[0]] + replacement + target[span[1] :]
    if collect_tokens(target) != collect_tokens(edited):
        return None
    if _prefill_edited_target_blocks(record, edited, validation_context):
        return None
    return _PrefillDecision(
        record_id=record.id,
        selected=candidate.label,
        decision_kind="edited",
        target=edited,
        reason=f"replaced forbidden target {forbidden!r} with approved {replacement!r}",
    )


def _build_prefill_decisions(
    proj: Project, task: JudgeTask
) -> tuple[list[_PrefillDecision], list[_PrefillHint]]:
    decisions: list[_PrefillDecision] = []
    hints: list[_PrefillHint] = []
    validation_context = load_validation_context(
        proj, context_view_path=task.context_view_path
    )
    for record in task.records:
        if not record.candidates:
            hints.append(_PrefillHint(record.id, "no candidates"))
            continue
        if len(record.candidates) != 1:
            hints.append(
                _PrefillHint(record.id, "multiple candidates; choose manually")
            )
            continue
        candidate = record.candidates[0]
        if not candidate.validation_findings:
            decisions.append(
                _PrefillDecision(
                    record_id=record.id,
                    selected=candidate.label,
                    decision_kind="copy",
                    target="",
                    reason="single clean candidate",
                )
            )
            continue
        decision = _prefill_try_repair(record, candidate, validation_context)
        if decision is not None:
            decisions.append(decision)
        else:
            hints.append(_PrefillHint(record.id, "policy conflict; judge manually"))
    return decisions, hints


def _render_prefill_decisions(
    task: JudgeTask, decisions: list[_PrefillDecision]
) -> str:
    lines: list[str] = [
        "# booktx judge decisions (prefilled policy fixes)",
        f"judge_task_id: {task.judge_task_id}",
        "# Only deterministic records are included; judge the rest manually.",
        "",
    ]
    for decision in decisions:
        lines.extend(
            [
                f"## {decision.record_id}",
                f"selected: {decision.selected}",
                f"decision_kind: {decision.decision_kind}",
                f"reason: {decision.reason}",
                "TARGET:",
                decision.target,
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _render_prefill_policy_hints(task: JudgeTask, hints: list[_PrefillHint]) -> str:
    lines: list[str] = [
        "# booktx judge policy hints",
        f"judge_task_id: {task.judge_task_id}",
        "# These records need manual judging; no deterministic fix was safe.",
        "",
    ]
    for hint in hints:
        lines.extend([f"## {hint.record_id}", f"hint: {hint.summary}", ""])
    return "\n".join(lines).rstrip() + "\n"


def prefill_judge_policy_fixes_workflow(
    proj: Project,
    *,
    judge_task_id: str,
    write: bool,
) -> dict[str, Any]:
    """Prefill deterministic glossary/termbase repair decisions for a judge task.

    Writes ``<task>.decisions.txt`` for records that have a single clean
    candidate (copied) or a single candidate whose only policy issue is one
    literal forbidden target with exactly one approved replacement, a single
    occurrence, a placeholder/XHTML-safe swap, and a validation-passing result
    (edited). Every other record is summarized in
    ``<task>.policy-hints.txt`` for manual judging.
    """
    _reject_revision_deterministic(proj, "prefill-policy-fixes")
    task = load_judge_task(proj, judge_task_id)
    if task is None:
        raise _err("judge_task_not_found", f"judge task not found: {judge_task_id}")
    decisions, hints = _build_prefill_decisions(proj, task)
    decisions_text = _render_prefill_decisions(task, decisions)
    hints_text = _render_prefill_policy_hints(task, hints)
    decisions_path = Path(judge_ingest_decisions_path(proj, task.judge_task_id))
    hints_path = decisions_path.with_name(
        decisions_path.stem.removesuffix(".decisions") + ".policy-hints.txt"
    )
    written_decisions = ""
    written_hints = ""
    if write:
        # Always overwrite both artifacts so the output reflects the prefill
        # result rather than the original ``judge next`` ingest template.
        write_text_atomic(decisions_path, decisions_text)
        written_decisions = str(decisions_path)
        write_text_atomic(hints_path, hints_text)
        written_hints = str(hints_path)
    return {
        "judge_task_id": judge_task_id,
        "decisions": [decision.record_id for decision in decisions],
        "hints": [hint.record_id for hint in hints],
        "decisions_path": written_decisions,
        "hints_path": written_hints,
        "write": write,
    }


def finish_chapter_plan_workflow(
    proj: Project,
    runtime: RuntimeContext,
    *,
    chapter: str,
    status_payload: dict[str, Any],
) -> dict[str, Any]:
    """Build a deterministic finish-the-chapter plan with profile-local paths.

    The plan lists an identical-sweep step, a judge-next step, the edit/insert
    step, and a stop condition. Every printed command uses the project
    argument ``.`` and never references sibling profiles, absolute paths, or
    parent directories.
    """
    source_profiles = list(status_payload.get("source_profiles") or [])
    source_access = status_payload.get("source_access")
    if source_access not in {"live", "snapshot"}:
        source_access = "live"
    resolved_source_access = cast(Literal["live", "snapshot"], source_access)
    next_cmd = build_chapter_next_command(
        proj, runtime, chapter=chapter, status_payload=status_payload
    )
    if not next_cmd:
        next_cmd = (
            f"booktx judge next . --unit chapter --chapter {chapter} "
            "--max-records 8 --format decisions"
        )
    if selection_purpose(proj) == "revise":
        # Revise profiles never sweep; start with judge next and stop only when
        # every record has matching judge-decision provenance.
        plan_lines = [
            f"chapter {chapter} finish plan:",
            f"1. judge records: {next_cmd}",
            "2. edit judge-ingest/TASK.decisions.txt and submit with the "
            "printed `booktx judge insert . ...` command.",
            f"stop condition: chapter {chapter} has no records missing "
            "matching judge-decision provenance.",
        ]
        return {"chapter": chapter, "plan_lines": plan_lines}
    sweep_cmd = _build_sweep_hint_command(
        runtime, proj, source_profiles, chapter, chapter, resolved_source_access
    )
    plan_lines = [
        f"chapter {chapter} finish plan:",
        f"1. sweep identical records: {sweep_cmd}",
        f"2. judge remaining records: {next_cmd}",
        "3. edit judge-ingest/TASK.decisions.txt and submit with the "
        "printed `booktx judge insert . ...` command.",
        f"stop condition: chapter {chapter} has no missing records.",
    ]
    return {"chapter": chapter, "plan_lines": plan_lines}
