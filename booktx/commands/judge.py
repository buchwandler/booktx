# ruff: noqa: E501
"""Typer commands for judge/selection-profile workflows."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.table import Table

from booktx.cli_support import (
    _die,
    _handle_booktx_error,
    _load_runtime_or_exit,
    _project_relative,
    _project_status_snapshot,
    _reject_if_isolated,
    _require_chunks,
    _require_no_source_drift,
    _require_ready_context,
    console,
)
from booktx.config import load_judge_task
from booktx.errors import BooktxError
from booktx.judge_sources import (
    configured_selection_sources,
    sync_judge_source_snapshots,
)
from booktx.judge_store import parse_sources_csv
from booktx.runtime import RuntimeContext
from booktx.workflows.judge import (
    _source_access_from_runtime,
    accept_identical_judge_records_workflow,
    accept_judge_submission_workflow,
    build_chapter_next_command,
    build_judge_status_workflow,
    create_judge_profile_workflow,
    create_next_judge_task_workflow,
    create_record_judge_task_workflow,
    finish_chapter_plan_workflow,
    judge_task_block_paths,
    judge_task_decisions_path,
    judge_task_json_path,
    prefill_judge_policy_fixes_workflow,
    prepare_judge_isolation_workflow,
    reset_judge_ingest_workflow,
    sweep_identical_judge_records_workflow,
)

judge_app = typer.Typer(help="Judge and selection-profile workflows.")


# --------------------------------------------------------------------------
# shared runtime helpers
# --------------------------------------------------------------------------


def _require_selection_runtime(runtime: RuntimeContext) -> None:
    """Reject judge commands unless the resolved profile is a selection profile."""
    cfg = runtime.project.profile_config
    if cfg is None or cfg.kind != "selection":
        _die("judge workflows require a selection profile")


def _render_judge_path(path: Path, runtime: RuntimeContext) -> str:
    """Render a judge artifact path without leaking parent/sibling paths.

    In profile-root mode the path is shown relative to the profile root (e.g.
    ``judge-ingest/TASK.block.txt``); in project-root mode it is shown relative
    to the project root.
    """
    if runtime.mode.kind == "profile-root":
        assert runtime.mode.profile_root is not None
        profile_root = runtime.mode.profile_root
        try:
            return Path(path).relative_to(profile_root).as_posix()
        except ValueError:
            # Never leak a parent path; fall back to the basename only.
            return Path(path).name
    return _project_relative(Path(path), runtime.project.root)


def _profile_root_die(message: str) -> None:
    """Die with a message that never includes parent/sibling/absolute paths."""
    _die(message)


def _resolve_judge_format(
    runtime: RuntimeContext,
    value: str | None,
    *,
    project_root_default: str = "block",
) -> str:
    if value is not None:
        if value not in {"block", "decisions", "json"}:
            _die("--format must be block, decisions, or json")
        return value
    return "decisions" if runtime.mode.kind == "profile-root" else project_root_default


def resolve_judge_submission_path(runtime: RuntimeContext, value: Path) -> Path:
    """Resolve a judge submission ``--file`` with profile-root confinement.

    In profile-root mode the path must be a profile-local relative path to an
    existing regular file inside the current profile. Absolute paths, any ``..``
    component, symlinks, and resolved paths that escape the profile root are
    rejected; error messages contain only profile-local/redacted text.

    In project-root mode the existing project-root-relative resolution is used.
    """
    if runtime.mode.kind != "profile-root":
        return (
            value if value.is_absolute() else (runtime.project.root / value).resolve()
        )

    profile_root = runtime.mode.profile_root
    assert profile_root is not None
    root = profile_root.resolve()
    if value.is_absolute():
        _profile_root_die("submission --file must be a profile-local relative path")
    if ".." in value.parts:
        _profile_root_die("submission --file must not contain a '..' component")
    # Walk each component, refusing any symlink or path escape.
    candidate = profile_root
    for part in value.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            _profile_root_die("submission --file must not traverse a symlink")
    final = candidate.resolve()
    if final != root and root not in final.parents:
        _profile_root_die("submission --file must stay inside the current profile")
    if not final.is_file():
        _profile_root_die("submission --file must be an existing regular file")
    return final


def _sync_render_payload(proj, runtime, result, *, write: bool) -> dict[str, object]:
    profiles_payload = [
        {
            "profile": snap.profile,
            "records_total": snap.records_total,
            "effective_candidates_total": snap.effective_candidates_total,
            "translation_store_sha256": snap.translation_store_sha256,
        }
        for snap in result.profiles
    ]
    if runtime.mode.kind == "profile-root":
        manifest_display = "judge-sources/manifest.json"
    else:
        manifest_display = f"translations/{result.profile}/judge-sources/manifest.json"
    return {
        "selection_profile": result.profile,
        "source_profiles": list(result.source_profiles),
        "snapshot_id": result.snapshot_id,
        "manifest_sha256": result.manifest_sha256,
        "changed": result.changed,
        "write": write,
        "manifest": manifest_display,
        "profiles": profiles_payload,
        "planned_writes": [_render_judge_path(p, runtime) for p in result.written],
        "planned_prunes": list(result.pruned),
        "next": (
            f"booktx judge prepare-isolation . --profile {result.profile} --write"
            if not write
            else f"cd translations/{result.profile}"
        ),
    }


# --------------------------------------------------------------------------
# create-profile (project-root only)
# --------------------------------------------------------------------------


@judge_app.command(name="create-profile")
def judge_create_profile(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile_name: str = typer.Argument(..., help="Selection profile name."),
    target: str = typer.Option(..., "--target", help="Target language code."),
    target_locale: str | None = typer.Option(
        None, "--target-locale", help="Target locale."
    ),
    sources: str = typer.Option(
        ..., "--sources", help="Comma-separated source profiles."
    ),
    model: str | None = typer.Option(None, "--model", help="Judge model label."),
    context_from: str | None = typer.Option(
        None,
        "--context-from",
        help="Copy approved judge policy from this source profile.",
    ),
    purpose: str = typer.Option(
        "compare",
        "--purpose",
        help="Selection purpose: compare or revise.",
    ),
) -> None:
    runtime = _load_runtime_or_exit(project_dir, require_profile=False)
    _reject_if_isolated(runtime)
    try:
        project = create_judge_profile_workflow(
            runtime.project.root,
            profile_name,
            target_language=target,
            target_locale=target_locale,
            sources_csv=sources,
            model=model,
            context_from=context_from,
            purpose=purpose,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(f"created selection profile: {project.profile}")


# --------------------------------------------------------------------------
# sync-sources (project-root admin only)
# --------------------------------------------------------------------------


@judge_app.command(name="sync-sources")
def judge_sync_sources(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Selection profile name."
    ),
    sources: str | None = typer.Option(
        None,
        "--sources",
        help="Comma-separated source profiles (must equal configured list, same order).",
    ),
    prune: bool = typer.Option(
        True, "--prune/--no-prune", help="Prune inactive snapshot generations."
    ),
    write: bool = typer.Option(
        False, "--write", help="Publish the snapshot; without it, print a dry-run plan."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    _reject_if_isolated(runtime)  # reads sibling source profiles
    proj = runtime.project
    _require_selection_runtime(runtime)
    configured = configured_selection_sources(proj)
    if sources is not None:
        requested = parse_sources_csv(sources)
        if requested != configured:
            _die(
                "admin --sources must equal the configured [selection].sources list "
                "in the same order"
            )
        source_profiles = requested
    else:
        source_profiles = configured
    try:
        result = sync_judge_source_snapshots(
            proj, source_profiles=source_profiles, prune=prune, write=write
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    payload = _sync_render_payload(proj, runtime, result, write=write)
    if as_json:
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return
    console.print(f"selection profile: {payload['selection_profile']}")
    console.print("source profiles: " + ", ".join(payload["source_profiles"]))  # type: ignore[arg-type]
    if write:
        if result.changed:
            console.print(
                f"published: {len(payload['profiles'])} source store(s) "  # type: ignore[arg-type]
                f"(snapshot_id {payload['snapshot_id']})"  # type: ignore[arg-type]
            )
        else:
            console.print("snapshot unchanged: no writes performed")
    else:
        console.print(
            f"dry-run: would publish {len(payload['profiles'])} source store(s)"
        )  # type: ignore[arg-type]
        console.print(f"changed: {payload['changed']}")
    console.print(f"manifest: {payload['manifest']}")
    for snap in payload["profiles"]:  # type: ignore[assignment]
        console.print(
            f"  - {snap['profile']}: {snap['records_total']} records, "
            f"{snap['effective_candidates_total']} effective candidates"
        )
    console.print(f"next: {payload['next']}", soft_wrap=True, markup=False)


# --------------------------------------------------------------------------
# prepare-isolation (project-root admin only)
# --------------------------------------------------------------------------


@judge_app.command(name="prepare-isolation")
def judge_prepare_isolation(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str = typer.Option(..., "--profile", help="Selection profile name."),
    context_from: str | None = typer.Option(
        None,
        "--context-from",
        help="Copy approved judge policy from this source profile first.",
    ),
    write: bool = typer.Option(
        False,
        "--write",
        help="Publish the snapshot and AGENTS.md; without it, print a dry-run plan.",
    ),
    replace_unmanaged: bool = typer.Option(
        False, "--replace-unmanaged", help="Overwrite an unmanaged AGENTS.md."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    _reject_if_isolated(runtime)  # admin command: project-root only
    try:
        payload = prepare_judge_isolation_workflow(
            runtime.project.root,
            profile=runtime.project.profile or profile,
            context_from=context_from,
            write=write,
            replace_unmanaged=replace_unmanaged,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    if as_json:
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return
    console.print(f"selection profile: {payload['profile']}")
    console.print("source profiles: " + ", ".join(payload["source_profiles"]))
    if payload["write"]:
        console.print(
            "snapshot: published" if payload["changed"] else "snapshot: unchanged"
        )
        console.print(f"agents.md: written for {payload['profile']}")
    else:
        console.print("dry-run: would publish snapshot and write judge AGENTS.md")
        console.print(f"agents.md state: {payload['agents_state']}")
    console.print(f"next: {payload['next']}", soft_wrap=True, markup=False)


# --------------------------------------------------------------------------
# status (allowed in profile-root for selection profiles)


@judge_app.command(name="status")
def judge_status(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Selection profile name."
    ),
    sources: str | None = typer.Option(
        None, "--sources", help="Comma-separated source profiles."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    _require_selection_runtime(runtime)
    # In profile-root mode, ignore any --profile flag value (never print it).
    sources_csv = None if runtime.mode.kind == "profile-root" else sources
    try:
        payload = build_judge_status_workflow(
            runtime.project,
            runtime,
            bundle=_project_status_snapshot(runtime.project),
            sources_csv=sources_csv,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    if as_json:
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return
    _print_judge_status_payload(payload)


def _render_snapshot_status(payload: dict[str, object]) -> None:
    snapshot = payload.get("snapshot")
    if snapshot is None:
        return
    if not isinstance(snapshot, dict):
        return
    state = snapshot.get("state")
    if state == "valid":
        console.print(
            f"judge source snapshot: valid (generated {snapshot.get('generated_at')})"
        )
        for entry in snapshot.get("profiles", []):
            console.print(
                f"  - {entry['profile']}: {entry['records_total']} records, "
                f"{entry['effective_candidates_total']} effective candidates"
            )
    elif state == "missing":
        console.print("judge source snapshot: missing")
        console.print(
            "return to the project root and run "
            "`booktx judge prepare-isolation` for this profile",
            soft_wrap=True,
            markup=False,
        )
    else:
        console.print("judge source snapshot: invalid")
        console.print(
            "return to the project root and run "
            "`booktx judge prepare-isolation` for this profile",
            soft_wrap=True,
            markup=False,
        )


def _render_status_blockers(payload: dict[str, object]) -> None:
    blocked_by = payload.get("blocked_by")
    if not isinstance(blocked_by, list) or not blocked_by:
        return
    mode = payload.get("mode")
    profile = payload.get("profile") or "PROFILE"
    messages = {
        "context_missing": "initialize and approve context before judging",
        "context_not_ready": "approve or sync context before judging",
        "snapshot_missing": (
            "return to the project root and run `booktx judge prepare-isolation` for this profile"
            if mode == "profile-root"
            else f"run from project root: booktx judge prepare-isolation . --profile {profile} --write"
        ),
        "snapshot_invalid": (
            "refresh the judge snapshot from the project root for this profile"
            if mode == "profile-root"
            else f"run from project root: booktx judge prepare-isolation . --profile {profile} --write"
        ),
    }
    rendered = [messages.get(code, str(code).replace("_", " ")) for code in blocked_by]
    console.print("blocked: " + "; ".join(rendered), soft_wrap=True, markup=False)


def _print_judge_status_payload(payload: dict[str, object]) -> None:
    console.print(f"selection profile: {payload['profile']}")
    console.print(f"mode: {payload['mode']}")
    purpose = payload.get("selection_purpose") or "compare"
    console.print(f"purpose: {purpose}")
    if purpose == "revise":
        console.print("review mode: explicit judge decisions required")
    console.print("source profiles: " + ", ".join(payload["source_profiles"]))
    context = payload["context"]
    console.print(f"context: {'READY' if context['ready'] else 'NOT READY'}")
    console.print(
        f"records selected: {payload['records_selected']}/{payload['records_total']}"
    )
    console.print(f"records missing: {payload['records_missing']}")
    console.print(
        f"records with candidate gaps: {payload['records_with_candidate_gaps']}"
    )
    _render_snapshot_status(payload)
    _render_status_blockers(payload)
    if payload["next_command"]:
        console.print(
            f"next command: {payload['next_command']}", soft_wrap=True, markup=False
        )
    sweep_hint = payload.get("sweep_hint")
    if sweep_hint:
        console.print(f"identical sweep: {sweep_hint}", soft_wrap=True, markup=False)


def _first_missing_chapter(payload: dict[str, object]) -> str | None:
    chapters = payload.get("chapters")
    if not isinstance(chapters, list):
        return None
    for entry in chapters:
        if isinstance(entry, dict) and int(entry.get("missing_records", 0)) > 0:
            chapter_id = entry.get("chapter_id")
            if isinstance(chapter_id, str):
                return chapter_id
    return None


# --------------------------------------------------------------------------
# next / record (allowed in profile-root for selection profiles)
# --------------------------------------------------------------------------


@judge_app.command(name="next")
def judge_next(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Selection profile name."
    ),
    sources: str | None = typer.Option(
        None, "--sources", help="Comma-separated source profiles."
    ),
    unit: str = typer.Option("chapter", "--unit", help="Task unit; currently chapter."),
    chapter: str | None = typer.Option(None, "--chapter", help="Optional chapter id."),
    max_words: int = typer.Option(900, "--max-words", help="Maximum source words."),
    max_records: int | None = typer.Option(
        None, "--max-records", help="Optional maximum number of records."
    ),
    max_rendered_lines: int | None = typer.Option(
        None,
        "--max-rendered-lines",
        help="Optional maximum rendered source-block lines before trimming trailing records.",
    ),
    output_format: str | None = typer.Option(
        None, "--format", help="block|decisions|json."
    ),
    require_all_sources: bool = typer.Option(
        False,
        "--require-all-sources",
        help=(
            "Fail if any selected record is missing a candidate from a source profile."
        ),
    ),
) -> None:
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    _require_selection_runtime(runtime)
    proj = runtime.project
    _require_chunks(proj)
    _require_no_source_drift(proj)
    _require_ready_context(proj)
    if unit != "chapter":
        _die("--unit must be chapter")
    resolved_format = _resolve_judge_format(runtime, output_format)
    source_access = _source_access_from_runtime(runtime)
    sources_csv = None if runtime.mode.kind == "profile-root" else sources
    try:
        task = create_next_judge_task_workflow(
            proj,
            bundle=_project_status_snapshot(proj),
            sources_csv=sources_csv,
            chapter=chapter,
            max_words=max_words,
            max_records=max_records,
            max_rendered_lines=max_rendered_lines,
            require_all_sources=require_all_sources,
            source_access=source_access,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    _print_judge_task(task, proj, runtime, resolved_format)


@judge_app.command(name="continue")
def judge_continue(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Selection profile name."
    ),
    sources: str | None = typer.Option(
        None, "--sources", help="Comma-separated source profiles."
    ),
    unit: str = typer.Option("chapter", "--unit", help="Task unit; currently chapter."),
    chapter: str | None = typer.Option(None, "--chapter", help="Optional chapter id."),
    max_words: int = typer.Option(900, "--max-words", help="Maximum source words."),
    max_records: int | None = typer.Option(
        None, "--max-records", help="Optional maximum number of records."
    ),
    max_rendered_lines: int | None = typer.Option(
        None,
        "--max-rendered-lines",
        help="Optional maximum rendered source-block lines before trimming trailing records.",
    ),
    output_format: str | None = typer.Option(
        None, "--format", help="block|decisions|json."
    ),
    require_all_sources: bool = typer.Option(
        False,
        "--require-all-sources",
        help=(
            "Fail if any selected record is missing a candidate from a source profile."
        ),
    ),
) -> None:
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    _require_selection_runtime(runtime)
    proj = runtime.project
    _require_chunks(proj)
    _require_no_source_drift(proj)
    if unit != "chapter":
        _die("--unit must be chapter")
    sources_csv = None if runtime.mode.kind == "profile-root" else sources
    try:
        status_payload = build_judge_status_workflow(
            proj,
            runtime,
            bundle=_project_status_snapshot(proj),
            sources_csv=sources_csv,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    _print_judge_status_payload(status_payload)
    if status_payload["blocked_by"]:
        _die("judge work is blocked; resolve the blockers shown above")
    next_chapter = chapter or _first_missing_chapter(status_payload)
    if next_chapter is None:
        console.print("no missing records remain")
        return
    resolved_format = _resolve_judge_format(runtime, output_format)
    source_access = _source_access_from_runtime(runtime)
    try:
        task = create_next_judge_task_workflow(
            proj,
            bundle=_project_status_snapshot(proj),
            sources_csv=sources_csv,
            chapter=next_chapter,
            max_words=max_words,
            max_records=max_records,
            max_rendered_lines=max_rendered_lines,
            require_all_sources=require_all_sources,
            source_access=source_access,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    _print_judge_task(task, proj, runtime, resolved_format)


@judge_app.command(name="record")
def judge_record(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    record_id: str = typer.Option(..., "--record", help="Record id to judge."),
    profile: str | None = typer.Option(
        None, "--profile", help="Selection profile name."
    ),
    sources: str | None = typer.Option(
        None, "--sources", help="Comma-separated source profiles."
    ),
    output_format: str | None = typer.Option(
        None, "--format", help="block|decisions|json."
    ),
    require_all_sources: bool = typer.Option(
        False,
        "--require-all-sources",
        help="Fail if the record is missing a candidate from a source profile.",
    ),
) -> None:
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    _require_selection_runtime(runtime)
    proj = runtime.project
    _require_chunks(proj)
    _require_no_source_drift(proj)
    _require_ready_context(proj)
    resolved_format = _resolve_judge_format(runtime, output_format)
    source_access = _source_access_from_runtime(runtime)
    sources_csv = None if runtime.mode.kind == "profile-root" else sources
    try:
        task = create_record_judge_task_workflow(
            proj,
            bundle=_project_status_snapshot(proj),
            sources_csv=sources_csv,
            record_id=record_id,
            require_all_sources=require_all_sources,
            source_access=source_access,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    _print_judge_task(task, proj, runtime, output_format=resolved_format)


def _print_judge_task(task, proj, runtime: RuntimeContext, output_format: str) -> None:
    src_path, ingest_block = judge_task_block_paths(proj, task)
    decisions_path = judge_task_decisions_path(proj, task)
    if output_format == "block":
        edit_path = ingest_block
    elif output_format == "decisions":
        edit_path = decisions_path
    else:
        edit_path = judge_task_json_path(proj, task)
    rendered_lines = len(Path(src_path).read_text("utf-8").splitlines())
    console.print(f"judge task: {task.judge_task_id}")
    console.print(f"records: {len(task.records)}")
    console.print(f"rendered_lines: {rendered_lines}")
    console.print(
        f"read:   {_render_judge_path(Path(src_path), runtime)}",
        soft_wrap=True,
        markup=False,
    )
    console.print(
        f"edit:   {_render_judge_path(Path(edit_path), runtime)}",
        soft_wrap=True,
        markup=False,
    )
    if runtime.mode.kind == "profile-root":
        submit = (
            f"booktx judge insert . --judge-task-id {task.judge_task_id} "
            f"--file {_render_judge_path(Path(edit_path), runtime)} "
            f"--format {output_format}"
        )
    else:
        submit = (
            f"booktx judge insert . --profile {proj.profile} "
            f"--judge-task-id {task.judge_task_id} "
            f"--file {_render_judge_path(Path(edit_path), runtime)} "
            f"--format {output_format}"
        )
    console.print(f"submit: {submit}", soft_wrap=True, markup=False)
    if runtime.mode.kind == "profile-root" and output_format == "block":
        if task.selection_purpose == "revise":
            console.print(
                "hint: revision profile - every record needs an explicit copy or "
                "edited decision; copy keeps the BASE_TARGET and leaves TARGET empty",
                soft_wrap=True,
                markup=False,
            )
        else:
            console.print(
                "hint: copy decisions can leave TARGET empty; booktx will copy the selected candidate exactly",
                soft_wrap=True,
                markup=False,
            )


@judge_app.command(name="show")
def judge_show(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    judge_task_id: str = typer.Option(..., "--judge-task-id", help="Judge task id."),
    record_id: str = typer.Option(
        ..., "--record", help="Record id inside the judge task."
    ),
    profile: str | None = typer.Option(
        None, "--profile", help="Selection profile name."
    ),
) -> None:
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    _require_selection_runtime(runtime)
    task = load_judge_task(runtime.project, judge_task_id)
    if task is None:
        _die(f"judge task not found: {judge_task_id}")
    record = next((item for item in task.records if item.id == record_id), None)
    if record is None:
        _die(f"record {record_id} is not part of judge task {judge_task_id}")
    console.print(record.id)
    console.print(f"SOURCE: {record.source}")
    for candidate in record.candidates:
        console.print(f"{candidate.label}: {candidate.target}")
    if record.candidates:
        summary = ", ".join(
            f"{candidate.label} "
            + (
                "ok"
                if not candidate.validation_findings
                else "; ".join(
                    f"{finding.severity}:{finding.rule}"
                    for finding in candidate.validation_findings
                )
            )
            for candidate in record.candidates
        )
        console.print(f"validation: {summary}")
    if task.selection_purpose == "revise":
        console.print("REVISION MODE:", soft_wrap=True, markup=False)
        console.print(
            "- proofread the BASE_TARGET for every record; do not skip records.",
            soft_wrap=True,
            markup=False,
        )
        console.print(
            "- copy: selected=A and decision_kind=copy; leave TARGET empty to keep the base target.",
            soft_wrap=True,
            markup=False,
        )
        console.print(
            "- edited: selected=A (or edited) and decision_kind=edited; TARGET is the complete corrected target.",
            soft_wrap=True,
            markup=False,
        )
    else:
        console.print("DECISION MODES:", soft_wrap=True, markup=False)
        console.print(
            "- copy: selected must be a candidate label; leave TARGET empty.",
            soft_wrap=True,
            markup=False,
        )
        console.print(
            "- edited from candidate: selected is a candidate label; decision_kind is edited; TARGET is the corrected full target.",
            soft_wrap=True,
            markup=False,
        )
        console.print(
            "- new judge target: selected is edited; decision_kind is edited; TARGET is the full new target.",
            soft_wrap=True,
            markup=False,
        )
        console.print(
            "Never paste a copy candidate into TARGET. Use TARGET only for edited/new targets.",
            soft_wrap=True,
            markup=False,
        )


def _accept_identical_next_message(
    *,
    status_payload: dict[str, object],
    runtime: RuntimeContext,
    requested_chapter: str | None,
) -> list[str]:
    """Lines to print after `accept-identical --write`.

    With an explicit `--chapter`, stay scoped to that chapter: if it still has
    missing records point at it; if it is complete say so and fall through to
    the global next command. Without `--chapter`, preserve the historical
    global `next command:` output.
    """
    global_next = str(status_payload.get("next_command") or "")
    if not requested_chapter:
        return [f"next command: {global_next}"] if global_next else []

    chapters = status_payload.get("chapters")
    chapter_status = next(
        (
            item
            for item in (chapters or [])
            if isinstance(item, dict) and item.get("chapter_id") == requested_chapter
        ),
        None,
    )
    if chapter_status is None:
        return [f"chapter {requested_chapter}: not found"]

    missing = int(chapter_status.get("missing_records") or 0)
    if missing > 0:
        scoped_next = build_chapter_next_command(
            runtime.project,
            runtime,
            chapter=requested_chapter,
            status_payload=status_payload,
        )
        if scoped_next:
            return [f"next command for chapter {requested_chapter}: {scoped_next}"]
        return [f"next command for chapter {requested_chapter}"]

    lines = [f"chapter {requested_chapter} complete"]
    if global_next:
        lines.append(f"next global command: {global_next}")
    return lines


@judge_app.command(name="accept-identical")
def judge_accept_identical(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Selection profile name."
    ),
    sources: str | None = typer.Option(
        None, "--sources", help="Comma-separated source profiles."
    ),
    unit: str = typer.Option("chapter", "--unit", help="Task unit; currently chapter."),
    chapter: str | None = typer.Option(None, "--chapter", help="Optional chapter id."),
    max_words: int = typer.Option(900, "--max-words", help="Maximum source words."),
    max_records: int | None = typer.Option(
        None, "--max-records", help="Optional maximum number of records."
    ),
    max_rendered_lines: int | None = typer.Option(
        None,
        "--max-rendered-lines",
        help="Optional maximum rendered source-block lines before trimming trailing records.",
    ),
    require_all_sources: bool = typer.Option(
        False,
        "--require-all-sources",
        help=(
            "Fail if any selected record is missing a candidate from a source profile."
        ),
    ),
    write: bool = typer.Option(
        False, "--write", help="Accept identical valid candidates into the store."
    ),
) -> None:
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    _require_selection_runtime(runtime)
    proj = runtime.project
    _require_chunks(proj)
    _require_no_source_drift(proj)
    _require_ready_context(proj)
    if unit != "chapter":
        _die("--unit must be chapter")
    source_access = _source_access_from_runtime(runtime)
    sources_csv = None if runtime.mode.kind == "profile-root" else sources
    try:
        payload = accept_identical_judge_records_workflow(
            proj,
            bundle=_project_status_snapshot(proj),
            sources_csv=sources_csv,
            chapter=chapter,
            max_words=max_words,
            max_records=max_records,
            max_rendered_lines=max_rendered_lines,
            require_all_sources=require_all_sources,
            source_access=source_access,
            write=write,
        )
    except BooktxError as exc:
        if chapter and exc.code == "judge_next":
            # A scoped --chapter with no missing records is a normal
            # completion, not a command failure. Mirror sweep-identical.
            status_payload = build_judge_status_workflow(
                proj,
                runtime,
                bundle=_project_status_snapshot(proj),
                sources_csv=sources_csv,
            )
            for line in _accept_identical_next_message(
                status_payload=status_payload,
                runtime=runtime,
                requested_chapter=chapter,
            ):
                console.print(line, soft_wrap=True, markup=False)
            return
        _handle_booktx_error(exc)
        return
    console.print(f"judge task: {payload['judge_task_id']}")
    if write:
        console.print(f"accepted: {payload['accepted_records']} record(s)")
        if payload["version_refs"]:
            console.print("versions: " + ", ".join(payload["version_refs"]))
        status_payload = build_judge_status_workflow(
            proj,
            runtime,
            bundle=_project_status_snapshot(proj),
            sources_csv=sources_csv,
        )
        for line in _accept_identical_next_message(
            status_payload=status_payload,
            runtime=runtime,
            requested_chapter=chapter,
        ):
            console.print(line, soft_wrap=True, markup=False)
    else:
        console.print(f"matched identical records: {payload['matched_records']}")
        console.print(
            "next: "
            "booktx judge accept-identical . --unit chapter"
            + (f" --chapter {chapter}" if chapter else "")
            + (f" --max-words {max_words}" if max_words != 900 else "")
            + (f" --max-records {max_records}" if max_records is not None else "")
            + (
                f" --max-rendered-lines {max_rendered_lines}"
                if max_rendered_lines is not None
                else ""
            )
            + (" --require-all-sources" if require_all_sources else "")
            + " --write",
            soft_wrap=True,
            markup=False,
        )


@judge_app.command(name="sweep-identical")
def judge_sweep_identical(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Selection profile name."
    ),
    sources: str | None = typer.Option(
        None, "--sources", help="Comma-separated source profiles."
    ),
    unit: str = typer.Option("chapter", "--unit", help="Task unit; currently chapter."),
    from_chapter: str = typer.Option(
        ..., "--from-chapter", help="First chapter id (inclusive)."
    ),
    to_chapter: str = typer.Option(
        ..., "--to-chapter", help="Last chapter id (inclusive)."
    ),
    max_records: int | None = typer.Option(
        None, "--max-records", help="Optional maximum number of records per chapter."
    ),
    require_all_sources: bool = typer.Option(
        False,
        "--require-all-sources",
        help=(
            "Fail if any selected record is missing a candidate from a source profile."
        ),
    ),
    write: bool = typer.Option(
        False, "--write", help="Accept identical valid candidates into the store."
    ),
) -> None:
    """Accept identical records across a chapter range in process.

    Iterates chapters from --from-chapter to --to-chapter (inclusive), accepts
    identical valid candidates for each chapter, and stops on the first chapter
    that still needs LLM judging. Replaces hand-written chapter loops.
    """
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    _require_selection_runtime(runtime)
    proj = runtime.project
    _require_chunks(proj)
    _require_no_source_drift(proj)
    _require_ready_context(proj)
    if unit != "chapter":
        _die("--unit must be chapter")
    source_access = _source_access_from_runtime(runtime)
    sources_csv = None if runtime.mode.kind == "profile-root" else sources
    try:
        payload = sweep_identical_judge_records_workflow(
            proj,
            bundle=_project_status_snapshot(proj),
            sources_csv=sources_csv,
            from_chapter=from_chapter,
            to_chapter=to_chapter,
            max_records=max_records,
            require_all_sources=require_all_sources,
            source_access=source_access,
            write=write,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("chapter")
    table.add_column("matched", justify="right")
    table.add_column("accepted", justify="right")
    table.add_column("remaining", justify="right")
    table.add_column("status")
    for row in payload["rows"]:
        table.add_row(
            str(row["chapter_id"]),
            str(row["matched_records"]),
            str(row["accepted_records"]),
            str(row["remaining_records"]),
            str(row["status"]),
        )
    console.print(table)

    if not write:
        console.print(
            "dry run: re-run with --write to accept the matched identical records",
            soft_wrap=True,
            markup=False,
        )
        return

    stopped = payload["stopped_chapter"]
    status_payload = build_judge_status_workflow(
        proj,
        runtime,
        bundle=_project_status_snapshot(proj),
        sources_csv=sources_csv,
    )
    if stopped is not None:
        scoped_next = build_chapter_next_command(
            proj,
            runtime,
            chapter=stopped,
            status_payload=status_payload,
        )
        line = (
            f"next command for chapter {stopped}: {scoped_next}"
            if scoped_next
            else f"next command for chapter {stopped}"
        )
        console.print(line, soft_wrap=True, markup=False)
    else:
        global_next = str(status_payload.get("next_command") or "")
        if global_next:
            console.print(
                f"next global command: {global_next}",
                soft_wrap=True,
                markup=False,
            )
        else:
            console.print(
                "all chapters in range complete",
                soft_wrap=True,
                markup=False,
            )


# --------------------------------------------------------------------------
# insert (allowed in profile-root for selection profiles; confined paths)
# --------------------------------------------------------------------------


@judge_app.command(name="insert")
def judge_insert(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    judge_task_id: str = typer.Option(..., "--judge-task-id", help="Judge task id."),
    file: Path = typer.Option(..., "--file", help="Judge submission file."),
    profile: str | None = typer.Option(
        None, "--profile", help="Selection profile name."
    ),
    input_format: str | None = typer.Option(
        None, "--format", help="block|decisions|json."
    ),
) -> None:
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    _require_selection_runtime(runtime)
    proj = runtime.project
    _require_chunks(proj)
    _require_no_source_drift(proj)
    _require_ready_context(proj)
    file_path = resolve_judge_submission_path(runtime, file)
    resolved_format = _resolve_judge_format(runtime, input_format)
    try:
        payload = accept_judge_submission_workflow(
            proj,
            bundle=_project_status_snapshot(proj),
            judge_task_id=judge_task_id,
            file=file_path,
            input_format=resolved_format,
            runtime=runtime,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(f"accepted: {payload['accepted_records']} record(s)")
    if payload["version_refs"]:
        console.print("versions: " + ", ".join(payload["version_refs"]))
    record_findings = payload.get("record_findings") or []
    warn_findings = [f for f in record_findings if f["severity"] == "warn"]
    if warn_findings:
        console.print(f"qa: {len(warn_findings)} warning(s)")
        for finding in warn_findings:
            console.print(
                f"  warning: {finding['record_id']}: {finding['message']}",
                soft_wrap=True,
                markup=False,
            )
    chapter_id = payload.get("chapter_id") or ""
    status_payload = build_judge_status_workflow(
        proj,
        runtime,
        bundle=_project_status_snapshot(proj),
        sources_csv=None,
    )
    if chapter_id:
        for line in _accept_identical_next_message(
            status_payload=status_payload,
            runtime=runtime,
            requested_chapter=chapter_id,
        ):
            console.print(line, soft_wrap=True, markup=False)
    elif status_payload.get("next_command"):
        console.print(
            f"next command: {status_payload['next_command']}",
            soft_wrap=True,
            markup=False,
        )


@judge_app.command(name="reset-ingest")
def judge_reset_ingest(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    judge_task_id: str = typer.Option(..., "--judge-task-id", help="Judge task id."),
    profile: str | None = typer.Option(
        None, "--profile", help="Selection profile name."
    ),
    output_format: str | None = typer.Option(
        None, "--format", help="block|decisions|json."
    ),
    write: bool = typer.Option(
        False, "--write", help="Rewrite the editable ingest file for the task."
    ),
) -> None:
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    _require_selection_runtime(runtime)
    resolved_format = _resolve_judge_format(
        runtime, output_format, project_root_default="decisions"
    )
    try:
        payload = reset_judge_ingest_workflow(
            runtime.project,
            judge_task_id=judge_task_id,
            output_format=resolved_format,
            write=write,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    rendered_path = _render_judge_path(Path(payload["path"]), runtime)
    console.print(f"judge task: {payload['judge_task_id']}")
    if write:
        console.print(f"rewrote: {rendered_path}", soft_wrap=True, markup=False)
    else:
        console.print(f"edit:   {rendered_path}", soft_wrap=True, markup=False)
        console.print(
            f"next: booktx judge reset-ingest . --judge-task-id {judge_task_id} --format {resolved_format} --write",
            soft_wrap=True,
            markup=False,
        )


@judge_app.command(name="prefill-policy-fixes")
def judge_prefill_policy_fixes(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    judge_task_id: str = typer.Option(..., "--judge-task-id", help="Judge task id."),
    profile: str | None = typer.Option(
        None, "--profile", help="Selection profile name."
    ),
    write: bool = typer.Option(
        False,
        "--write",
        help="Write <task>.decisions.txt and <task>.policy-hints.txt.",
    ),
) -> None:
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    _require_selection_runtime(runtime)
    try:
        payload = prefill_judge_policy_fixes_workflow(
            runtime.project,
            judge_task_id=judge_task_id,
            write=write,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(f"judge task: {payload['judge_task_id']}")
    console.print(f"prefilled decisions: {len(payload['decisions'])} record(s)")
    console.print(f"policy hints: {len(payload['hints'])} record(s)")
    if write:
        if payload["decisions_path"]:
            console.print(
                f"wrote: {_render_judge_path(Path(payload['decisions_path']), runtime)}",
                soft_wrap=True,
                markup=False,
            )
        if payload["hints_path"]:
            console.print(
                f"wrote: {_render_judge_path(Path(payload['hints_path']), runtime)}",
                soft_wrap=True,
                markup=False,
            )
    else:
        console.print(
            "next: booktx judge prefill-policy-fixes . "
            f"--judge-task-id {judge_task_id} --write",
            soft_wrap=True,
            markup=False,
        )


@judge_app.command(name="finish-chapter-plan")
def judge_finish_chapter_plan(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    chapter: str = typer.Option(..., "--chapter", help="Chapter id to plan."),
    profile: str | None = typer.Option(
        None, "--profile", help="Selection profile name."
    ),
    sources: str | None = typer.Option(
        None, "--sources", help="Comma-separated source profiles."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    _require_selection_runtime(runtime)
    _require_chunks(runtime.project)
    _require_no_source_drift(runtime.project)
    _require_ready_context(runtime.project)
    sources_csv = None if runtime.mode.kind == "profile-root" else sources
    try:
        status_payload = build_judge_status_workflow(
            runtime.project,
            runtime,
            bundle=_project_status_snapshot(runtime.project),
            sources_csv=sources_csv,
        )
        payload = finish_chapter_plan_workflow(
            runtime.project,
            runtime,
            chapter=chapter,
            status_payload=status_payload,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    if as_json:
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return
    for line in payload["plan_lines"]:
        console.print(line, soft_wrap=True, markup=False)
