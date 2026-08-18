"""Presentation helpers for judge command output."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from booktx.cli_support import _project_relative, console
from booktx.runtime import RuntimeContext
from booktx.workflows.judge import (
    judge_task_block_paths,
    judge_task_decisions_path,
    judge_task_json_path,
)


def render_judge_path(path: Path, runtime: RuntimeContext) -> str:
    """Render a judge artifact path without leaking parent/sibling paths."""

    if runtime.mode.kind == "profile-root":
        assert runtime.mode.profile_root is not None
        profile_root = runtime.mode.profile_root
        try:
            return Path(path).relative_to(profile_root).as_posix()
        except ValueError:
            return Path(path).name
    return _project_relative(Path(path), runtime.project.root)


def sync_render_payload(
    result: Any, runtime: RuntimeContext, *, write: bool
) -> dict[str, Any]:
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
        "planned_writes": [render_judge_path(p, runtime) for p in result.written],
        "planned_prunes": list(result.pruned),
        "next": (
            f"booktx judge prepare-isolation . --profile {result.profile} --write"
            if not write
            else f"cd translations/{result.profile}"
        ),
    }


def render_snapshot_status(payload: dict[str, object]) -> None:
    snapshot = payload.get("snapshot")
    if snapshot is None or not isinstance(snapshot, dict):
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


def render_status_blockers(payload: dict[str, Any]) -> None:
    blocked_by = payload.get("blocked_by")
    if not isinstance(blocked_by, list) or not blocked_by:
        return
    mode = payload.get("mode")
    profile = payload.get("profile") or "PROFILE"
    messages = {
        "context_missing": "initialize and approve context before judging",
        "context_not_ready": "approve or sync context before judging",
        "revision_source_incomplete": (
            "the pinned revision source has missing effective targets; "
            "complete and revalidate the source profile, then refresh the judge "
            "snapshot"
        ),
        "snapshot_missing": (
            "return to the project root and run "
            "`booktx judge prepare-isolation` for this profile"
            if mode == "profile-root"
            else (
                "run from project root: "
                f"booktx judge prepare-isolation . --profile {profile} --write"
            )
        ),
        "snapshot_invalid": (
            "refresh the judge snapshot from the project root for this profile"
            if mode == "profile-root"
            else (
                "run from project root: "
                f"booktx judge prepare-isolation . --profile {profile} --write"
            )
        ),
    }
    rendered = [messages.get(code, str(code).replace("_", " ")) for code in blocked_by]
    console.print("blocked: " + "; ".join(rendered), soft_wrap=True, markup=False)


def print_judge_status_payload(payload: dict[str, Any]) -> None:
    console.print(f"selection profile: {payload['profile']}")
    console.print(f"mode: {payload['mode']}")
    purpose = payload.get("selection_purpose") or "compare"
    console.print(f"purpose: {purpose}")
    if purpose == "revise":
        focus = payload.get("revision_focus") or "general"
        console.print(f"revision focus: {focus}")
        review_mode = (
            "explicit grammar-only judge decisions required"
            if focus == "grammar"
            else "explicit judge decisions required"
        )
        console.print(f"review mode: {review_mode}")
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
    if purpose == "revise":
        console.print(f"copy decisions: {payload['decisions_copy']}")
        console.print(f"edited decisions: {payload['decisions_edited']}")
        console.print(f"decision edit rate: {payload['decision_edit_rate']}")
    render_snapshot_status(payload)
    render_status_blockers(payload)
    if payload["next_command"]:
        console.print(
            f"next command: {payload['next_command']}", soft_wrap=True, markup=False
        )
    sweep_hint = payload.get("sweep_hint")
    if sweep_hint:
        console.print(f"identical sweep: {sweep_hint}", soft_wrap=True, markup=False)


def first_missing_chapter(payload: dict[str, Any]) -> str | None:
    chapters = payload.get("chapters")
    if not isinstance(chapters, list):
        return None
    for entry in chapters:
        if isinstance(entry, dict) and int(entry.get("missing_records", 0)) > 0:
            chapter_id = entry.get("chapter_id")
            if isinstance(chapter_id, str):
                return chapter_id
    return None


def print_judge_task(
    task: Any, proj: Any, runtime: RuntimeContext, output_format: str
) -> None:
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
        f"read:   {render_judge_path(Path(src_path), runtime)}",
        soft_wrap=True,
        markup=False,
    )
    console.print(
        f"edit:   {render_judge_path(Path(edit_path), runtime)}",
        soft_wrap=True,
        markup=False,
    )
    if runtime.mode.kind == "profile-root":
        submit = (
            f"booktx judge insert . --judge-task-id {task.judge_task_id} "
            f"--file {render_judge_path(Path(edit_path), runtime)} "
            f"--format {output_format}"
        )
    else:
        submit = (
            f"booktx judge insert . --profile {proj.profile} "
            f"--judge-task-id {task.judge_task_id} "
            f"--file {render_judge_path(Path(edit_path), runtime)} "
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
                "hint: copy decisions can leave TARGET empty; booktx will copy "
                "the selected candidate exactly",
                soft_wrap=True,
                markup=False,
            )
