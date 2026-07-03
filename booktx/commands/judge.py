# ruff: noqa: E501
"""Typer commands for judge/selection-profile workflows."""

from __future__ import annotations

import json
from pathlib import Path

import typer

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
from booktx.errors import BooktxError
from booktx.judge_sources import (
    configured_selection_sources,
    sync_judge_source_snapshots,
)
from booktx.judge_store import parse_sources_csv
from booktx.runtime import RuntimeContext
from booktx.workflows.judge import (
    _source_access_from_runtime,
    accept_judge_submission_workflow,
    build_judge_status_workflow,
    create_judge_profile_workflow,
    create_next_judge_task_workflow,
    create_record_judge_task_workflow,
    judge_task_block_paths,
    judge_task_json_path,
    prepare_judge_isolation_workflow,
)

judge_app = typer.Typer(help="Judge and selection-profile workflows.")


# --------------------------------------------------------------------------
# shared runtime helpers
# --------------------------------------------------------------------------


def _require_selection_runtime(runtime: RuntimeContext) -> None:
    """Reject judge commands unless the active profile is a selection profile."""
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
    select: bool = typer.Option(False, "--select", help="Select the created profile."),
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
            select=select,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(f"created selection profile: {project.profile}")
    if select:
        console.print(f"selected active profile: {project.profile}")


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
    console.print(f"selection profile: {payload['profile']}")
    console.print("source profiles: " + ", ".join(payload["source_profiles"]))
    console.print(
        f"records selected: {payload['records_selected']}/{payload['records_total']}"
    )
    console.print(f"records missing: {payload['records_missing']}")
    console.print(
        f"records with candidate gaps: {payload['records_with_candidate_gaps']}"
    )
    _render_snapshot_status(payload)
    if payload["next_command"]:
        console.print(
            f"next command: {payload['next_command']}", soft_wrap=True, markup=False
        )


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
    output_format: str = typer.Option("block", "--format", help="block|json."),
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
    source_access = _source_access_from_runtime(runtime)
    sources_csv = None if runtime.mode.kind == "profile-root" else sources
    try:
        task = create_next_judge_task_workflow(
            proj,
            bundle=_project_status_snapshot(proj),
            sources_csv=sources_csv,
            chapter=chapter,
            max_words=max_words,
            require_all_sources=require_all_sources,
            source_access=source_access,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    _print_judge_task(task, proj, runtime, output_format)


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
    _print_judge_task(task, proj, runtime, output_format="block")


def _print_judge_task(task, proj, runtime: RuntimeContext, output_format: str) -> None:
    src_path, ingest_block = judge_task_block_paths(proj, task)
    edit_path = (
        ingest_block if output_format == "block" else judge_task_json_path(proj, task)
    )
    console.print(f"judge task: {task.judge_task_id}")
    console.print(f"records: {len(task.records)}")
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
    input_format: str = typer.Option("block", "--format", help="block|json."),
) -> None:
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    _require_selection_runtime(runtime)
    proj = runtime.project
    _require_chunks(proj)
    _require_no_source_drift(proj)
    _require_ready_context(proj)
    file_path = resolve_judge_submission_path(runtime, file)
    try:
        payload = accept_judge_submission_workflow(
            proj,
            bundle=_project_status_snapshot(proj),
            judge_task_id=judge_task_id,
            file=file_path,
            input_format=input_format,
            runtime=runtime,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(f"accepted: {payload['accepted_records']} record(s)")
    if payload["version_refs"]:
        console.print("versions: " + ", ".join(payload["version_refs"]))
