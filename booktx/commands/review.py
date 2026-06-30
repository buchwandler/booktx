"""Typer commands for the quality-review pass (Phase 3 slice 6).

Thin command layer for the ``review`` group (configure / status / next /
insert / activate / deactivate / revise-record / todo-next / todo-status /
todo-resume). Each command parses options, delegates the actual work to
:mod:`booktx.workflows.review` and :mod:`booktx.cli_support`, and maps
:class:`booktx.errors.BooktxError` to a non-zero exit.

The review-gap API boundary is preserved: commands never call
``booktx.review_status.build_review_gap_index``; that lives behind the
workflow layer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from pydantic import ValidationError

from booktx.acceptance import SubmittedRecord
from booktx.cli_support import (
    _die,
    _handle_booktx_error,
    _load_project_or_exit,
    _load_runtime_or_exit,
    _maybe_auto_export_indexes,
    _project_relative,
    _project_status_snapshot,
    _render_submission_failures,
    _require_chunks,
    _require_no_source_drift,
    _require_ready_context,
    _staged_preflight_check,
    console,
)
from booktx.command_hints import check_command, review_next_command
from booktx.errors import BooktxError
from booktx.workflows.review import (
    ReviewValidationError,
    accept_review_submission_workflow,
    activate_review_workflow,
    build_review_status_snapshot,
    build_review_todo_workflow,
    compute_review_todo_status_workflow,
    configure_review_workflow,
    create_next_review_task_workflow,
    deactivate_review_workflow,
    load_review_todo_for_status,
    require_quality_review_enabled,
    resume_review_todo_workflow,
    review_task_block_paths,
    revise_review_record_workflow,
    validate_review_revision_workflow,
    write_review_todo_workflow,
)

review_app = typer.Typer(help="Quality review pass workflow.")


@review_app.command(name="configure")
def review_configure(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    show: bool = typer.Option(
        False, "--show", help="Show current quality review config."
    ),
    enable: bool = typer.Option(False, "--enable", help="Enable quality review."),
    disable: bool = typer.Option(False, "--disable", help="Disable quality review."),
    pass_number: int | None = typer.Option(
        None, "--pass", help="Review pass number to configure."
    ),
    name: str | None = typer.Option(None, "--name", help="Review pass name."),
    mode: str | None = typer.Option(
        None, "--mode", help="manual|after_chapter|before_build."
    ),
    enforce: str | None = typer.Option(None, "--enforce", help="off|warn|error."),
    before: int | None = typer.Option(None, "--before", help="Context records before."),
    after: int | None = typer.Option(None, "--after", help="Context records after."),
    batch_words: int | None = typer.Option(
        None, "--batch-words", help="Default review batch size."
    ),
    instructions: str | None = typer.Option(
        None, "--instructions", help="Pass instructions."
    ),
    base: str | None = typer.Option(
        None, "--base", help="active_translation|active_review."
    ),
    required_base_pass: int | None = typer.Option(
        None, "--required-base-pass", help="Required prior pass."
    ),
) -> None:
    """Show or update profile quality-review configuration without manual TOML edits."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    try:
        payload = configure_review_workflow(
            runtime.project,
            show=show,
            enable=enable,
            disable=disable,
            pass_number=pass_number,
            name=name,
            mode=mode,
            enforce=enforce,
            before=before,
            after=after,
            batch_words=batch_words,
            instructions=instructions,
            base=base,
            required_base_pass=required_base_pass,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    if payload is None:
        console.print("quality review: not configured")
        console.print(
            "next: booktx review configure . --enable --pass 1 "
            '--name "Flow review" --mode manual --enforce warn'
        )
        return
    console.print(
        "quality review: " + ("enabled" if payload["enabled"] else "disabled")
    )
    active = ", ".join(str(p) for p in payload["active_passes"]) or "none"
    console.print(f"active passes: {active}")
    for p in payload["passes"]:
        console.print(f"pass {p['pass_number']} {p['name']}".rstrip())
        console.print(
            f"  mode: {p['mode']}  enforce: {p['enforce']}  base: {p['base']}"
        )
        console.print(
            f"  context: before={p['before_records']} after={p['after_records']}"
            f" batch_words={p['batch_words']}"
        )
        if p["required_base_pass"] is not None:
            console.print(f"  required base pass: {p['required_base_pass']}")
    if payload["enabled"]:
        first_pass = payload["active_passes"][0] if payload["active_passes"] else 1
        console.print("next: booktx review status .", soft_wrap=True, markup=False)
        console.print(
            f"next review: booktx review next . --pass {first_pass}",
            soft_wrap=True,
            markup=False,
        )
    else:
        console.print(
            "next: booktx review configure . --enable --pass 1 "
            '--name "Flow review" --mode manual --enforce warn',
            soft_wrap=True,
            markup=False,
        )


@review_app.command(name="status")
def review_status(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """Report review coverage by pass."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    proj = runtime.project
    bundle = _project_status_snapshot(proj)
    snapshot = build_review_status_snapshot(proj, runtime, bundle=bundle)
    if as_json:
        console.print_json(
            json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False)
        )
        return
    if not snapshot.enabled:
        console.print("quality review: disabled")
        return
    console.print("quality review: enabled")
    console.print(f"active passes: {', '.join(str(p) for p in snapshot.active_passes)}")
    for p in snapshot.passes:
        console.print(f"pass {p.pass_number} {p.name}".rstrip())
        console.print(f"  eligible base records: {p.eligible_records}")
        console.print(f"  reviewed records: {p.reviewed_records}")
        console.print(f"  missing review: {p.missing_review_records}")
        console.print(f"  stale review: {p.stale_review_records}")
        if p.blocked_records:
            console.print(f"  blocked waiting for prior pass: {p.blocked_records}")
        if p.first_missing_record is not None:
            console.print(
                "  next: "
                + review_next_command(
                    proj,
                    mode=runtime.mode,
                    pass_number=p.pass_number,
                    chapter_id=p.first_missing_chapter,
                ),
                soft_wrap=True,
                markup=False,
            )
        elif p.blocked_records and p.reviewed_records == 0:
            console.print("  next: finish prior pass first")


@review_app.command(name="next")
def review_next(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    pass_number: int = typer.Option(..., "--pass", help="Review pass number."),
    chapter: str | None = typer.Option(None, "--chapter", help="Optional chapter id."),
    max_words: int = typer.Option(
        900, "--max-words", help="Maximum source words to return."
    ),
    selection: str = typer.Option(
        "missing",
        "--selection",
        help="missing|stale|reviewed|all|changed-base; default missing.",
    ),
    base: str | None = typer.Option(
        None,
        "--base",
        help="active_translation|active_review|pass:N (default from pass config).",
    ),
) -> None:
    """Create the next durable review task for a pass."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    proj = runtime.project
    _require_chunks(proj)
    _require_no_source_drift(proj)
    bundle = _project_status_snapshot(proj)
    try:
        task = create_next_review_task_workflow(
            proj,
            runtime,
            bundle=bundle,
            pass_number=pass_number,
            chapter=chapter,
            max_words=max_words,
            selection=selection,
            base=base,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(f"review task: {task.review_task_id}")
    console.print(
        f"records: {task.record_count}  pass: R{task.pass_number} "
        f"{task.pass_name}".rstrip()
    )
    src_path, ingest_path = review_task_block_paths(proj, task)
    src_path = _project_relative(Path(src_path), proj.root)
    ingest_path = _project_relative(Path(ingest_path), proj.root)
    console.print(f"read:   {src_path}", soft_wrap=True, markup=False)
    console.print(f"edit:   {ingest_path}", soft_wrap=True, markup=False)
    console.print(
        f"submit: booktx review insert . --review-task-id {task.review_task_id} "
        f"--file {ingest_path} --format block",
        soft_wrap=True,
        markup=False,
    )
    console.print(
        "check:  "
        + check_command(
            proj, mode=runtime.mode, chapter_id=task.chapter_id, fail_on_warnings=True
        ),
        soft_wrap=True,
        markup=False,
    )
    console.print("status: booktx review status .", soft_wrap=True, markup=False)


@review_app.command(name="insert")
def review_insert(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    review_task_id: str = typer.Option(..., "--review-task-id", help="Review task id."),
    file: Path = typer.Option(..., "--file", help="Review block submission file."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    output_format: str = typer.Option(
        "block", "--format", help="Submission format: block."
    ),
    activate: bool = typer.Option(
        True, "--activate", help="Activate the review candidate (default)."
    ),
    no_activate: bool = typer.Option(
        False, "--no-activate", help="Do not activate the review candidate."
    ),
    export_index: bool = typer.Option(
        False, "--export-index", help="Export editor QA indexes after acceptance."
    ),
) -> None:
    """Parse a review task submission and create review candidates."""
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    bundle = _project_status_snapshot(proj)
    try:
        result = accept_review_submission_workflow(
            proj,
            bundle=bundle,
            review_task_id=review_task_id,
            file=file,
            activate=activate,
            no_activate=no_activate,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(f"accepted {result['accepted_records']} review candidate(s)")
    if result["activated"]:
        console.print(f"activated: {', '.join(result['review_refs'])}")
    console.print("next: booktx validate .")
    _maybe_auto_export_indexes(proj, export_index=export_index, trigger="review")


@review_app.command(name="activate")
def review_activate(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    record_ref: str = typer.Argument(..., help="Record ref such as 74@38."),
    review_ref: str = typer.Argument(..., help="Review ref such as R1.2."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
) -> None:
    """Activate an existing review candidate for a single record."""
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    try:
        message = activate_review_workflow(
            proj, record_ref=record_ref, review_ref=review_ref
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(message)


@review_app.command(name="deactivate")
def review_deactivate(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    record_ref: str = typer.Argument(..., help="Record ref such as 74@38."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
) -> None:
    """Deactivate the active review for a record.

    Falls back to the active translation version.
    """
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    proj = runtime.project
    bundle = _project_status_snapshot(proj)
    try:
        message, record_id = deactivate_review_workflow(
            proj, bundle=bundle, record_ref=record_ref
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(message)
    chapter_id = bundle.index.record_to_chapter.get(record_id, "")
    console.print(
        "recheck: "
        + check_command(
            proj,
            mode=runtime.mode,
            chapter_id=chapter_id or None,
            fail_on_warnings=True,
        ),
        soft_wrap=True,
        markup=False,
    )


@review_app.command(name="revise-record")
def review_revise_record(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    record_ref: str = typer.Argument(..., help="Record ref such as 74@38."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    base_review: str = typer.Option(
        ...,
        "--base-review",
        help="Existing review ref to base the revision on, e.g. R1.2.",
    ),
    stdin: bool = typer.Option(
        False, "--stdin", help="Read the revised target from stdin."
    ),
    target: str | None = typer.Option(
        None, "--target", help="Inline revised target text (short texts only)."
    ),
    activate: bool = typer.Option(
        True,
        "--activate/--no-activate",
        help="Activate the new review candidate after writing.",
    ),
) -> None:
    """Revise an accepted review candidate by creating a new same-pass rerun."""
    if (target is None) == (not stdin):
        _die("provide exactly one of --stdin or --target")
        return
    if target is not None:
        target_text = target
    else:
        target_text = sys.stdin.read()
        if target_text.endswith("\r\n"):
            target_text = target_text[:-2]
        elif target_text.endswith("\n"):
            target_text = target_text[:-1]
    if not target_text.strip():
        _die("empty target")
        return

    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    proj = runtime.project
    _require_chunks(proj)
    _require_ready_context(proj)
    bundle = _project_status_snapshot(proj)

    from booktx.record_refs import parse_record_ref as _parse_record_ref

    record_id = _parse_record_ref(record_ref).canonical_id

    try:
        validate_review_revision_workflow(
            proj, bundle=bundle, record_id=record_id, target_text=target_text
        )
    except ReviewValidationError as exc:
        _render_submission_failures(exc.findings)
        raise typer.Exit(code=1) from exc
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return

    try:
        _staged_preflight_check(
            proj,
            [SubmittedRecord(id=record_id, target=target_text)],
            {record_id},
            fail_on_warnings=True,
        )
    except ValidationError:
        console.print(
            "[red]error:[/red] internal preflight staging failed while "
            "validating submitted EPUB inline XHTML"
        )
        raise typer.Exit(code=1) from None

    try:
        message, record_id = revise_review_record_workflow(
            proj,
            bundle=bundle,
            record_ref=record_ref,
            base_review=base_review,
            target_text=target_text,
            activate=activate,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(message)
    chapter_id = bundle.index.record_to_chapter.get(record_id, "")
    console.print(
        "recheck: "
        + check_command(
            proj,
            mode=runtime.mode,
            chapter_id=chapter_id or None,
            fail_on_warnings=True,
        ),
        soft_wrap=True,
        markup=False,
    )


@review_app.command(name="todo-next")
def review_todo_next(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    passes: str = typer.Option(
        "1", "--passes", help="Comma-separated pass numbers (default: 1)."
    ),
    chapters: int = typer.Option(
        2, "--chapters", help="Number of chapters to include (default: 2)."
    ),
    batch_words: int = typer.Option(
        900, "--batch-words", help="Max source words per review task (default: 900)."
    ),
    selection: str = typer.Option(
        "missing", "--selection", help="missing|stale|reviewed|all|changed-base."
    ),
    base: str | None = typer.Option(
        None, "--base", help="active_translation|active_review|pass:N."
    ),
    write: bool = typer.Option(False, "--write", help="Write todo files to disk."),
) -> None:
    """Create a bounded multi-pass review todo with chapter selection."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    proj = runtime.project
    bundle = _project_status_snapshot(proj)
    pass_numbers = [int(p.strip()) for p in passes.split(",") if p.strip()]
    if not pass_numbers:
        _die("at least one pass number is required")
        return
    try:
        todo = build_review_todo_workflow(
            proj, bundle=bundle, chapters=chapters, batch_words=batch_words
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    if write:
        json_path, md_path = write_review_todo_workflow(proj, runtime, todo)
        console.print(f"review todo: {todo.review_todo_id}")
        console.print(f"json: {json_path}")
        console.print(f"markdown: {md_path}")
    else:
        console.print_json(json.dumps(todo.model_dump(mode="json"), ensure_ascii=False))


@review_app.command(name="todo-status")
def review_todo_status(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    review_todo_id: str | None = typer.Option(
        None, "--review-todo-id", help="Review todo id."
    ),
    latest: bool = typer.Option(
        False, "--latest", help="Show status for the latest incomplete review todo."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """Report progress for a bounded review todo."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    proj = runtime.project
    bundle = _project_status_snapshot(proj)
    try:
        quality = require_quality_review_enabled(proj)
        todo = load_review_todo_for_status(
            proj,
            bundle=bundle,
            quality=quality,
            review_todo_id=review_todo_id,
            latest=latest,
        )
        status = compute_review_todo_status_workflow(
            proj, bundle=bundle, quality=quality, runtime=runtime, todo=todo
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    if as_json:
        console.print_json(json.dumps(status.as_dict(), ensure_ascii=False))
        return
    console.print(f"review todo: {todo.review_todo_id}")
    console.print(f"state: {status.state}")
    console.print(f"goal complete: {status.goal_complete}")
    if status.current_chapter is not None:
        console.print(
            f"current chapter: {status.current_chapter.chapter_id}"
            f" {status.current_chapter.title}"
        )
    for ch in status.chapters:
        mark = " [complete]" if ch.complete else ""
        console.print(
            f"  {ch.chapter_id} {ch.title}:"
            f" missing_review={ch.missing_review_now}"
            f" pending_passes={ch.pending_passes_now}{mark}"
        )
    if status.next_safe_command:
        console.print(f"next: {status.next_safe_command}", soft_wrap=True, markup=False)


@review_app.command(name="todo-resume")
def review_todo_resume(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    review_todo_id: str | None = typer.Option(
        None, "--review-todo-id", help="Review todo id."
    ),
    latest: bool = typer.Option(
        False, "--latest", help="Resume the latest incomplete review todo."
    ),
) -> None:
    """Create the next bounded review task for an open review todo."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    proj = runtime.project
    bundle = _project_status_snapshot(proj)
    try:
        quality = require_quality_review_enabled(proj)
        task = resume_review_todo_workflow(
            proj,
            bundle=bundle,
            quality=quality,
            runtime=runtime,
            review_todo_id=review_todo_id,
            latest=latest,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(f"review task: {task.review_task_id}")
    console.print(f"pass: R{task.pass_number} {task.pass_name}".rstrip())
    console.print(f"chapter: {task.chapter_id} {task.chapter_title}".rstrip())
    console.print(f"records: {task.record_count}")
    console.print(
        "next:",
        f"booktx review insert . --review-task-id {task.review_task_id}"
        f" --file reviews/{task.review_task_id}.block.txt --format block",
        soft_wrap=True,
        markup=False,
    )
