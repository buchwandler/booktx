"""Workflow layer for judge/selection-profile commands."""

from __future__ import annotations

# ruff: noqa: E501
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from booktx.acceptance import SubmissionValidationError
from booktx.agents_md import AGENTS_FILENAME, inspect_agents_md
from booktx.config import (
    _err,
    judge_ingest_decisions_path,
    load_judge_task,
    load_profile_config,
    load_profile_project,
    profile_dir,
    write_profile_config,
)
from booktx.context import (
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
from booktx.models import JudgeTask, SelectionConfig
from booktx.termbase import publish_termbase_snapshot
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
) -> Project:
    project = create_profile_workflow(
        project_dir,
        profile_name,
        target_language=target_language,
        target_locale=target_locale,
        model=model,
        kind="selection",
    )
    cfg = load_profile_config(project.root, profile_name)
    cfg.selection = SelectionConfig(sources=resolve_sources_csv(sources_csv))
    write_profile_config(project.root, cfg)
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


def _source_access_from_runtime(runtime: RuntimeContext) -> Literal["live", "snapshot"]:
    """ "snapshot" in profile-root mode, "live" in project-root mode."""
    return "snapshot" if runtime.mode.kind == "profile-root" else "live"


def _snapshot_status(
    proj: Project,
    configured: list[str],
    sources_csv: str | None,
) -> tuple[dict[str, Any], dict[str, object]]:
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
    counts = [
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
    selected_ids = selected_record_ids(proj)
    context_info = _judge_context_status(proj)
    blocked_by: list[str] = []
    if not context_info["exists"]:
        blocked_by.append("context_missing")
    elif not context_info["ready"]:
        blocked_by.append("context_not_ready")
    snapshot_info: dict[str, Any] | None = None
    source_views: dict[str, object] = {}
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
                if record_has_candidate_gap(source_views, record_id)  # type: ignore[arg-type]
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
    return {
        "profile": proj.profile or "",
        "source_profiles": source_profiles,
        "records_selected": len(selected_ids),
        "records_total": bundle.snapshot.totals.records_total,
        "records_missing": bundle.snapshot.totals.records_total - len(selected_ids),
        "records_with_candidate_gaps": candidate_gaps,
        "chapters": chapters,
        "next_command": next_command,
        "mode": runtime.mode.kind,
        "source_access": source_access,
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
