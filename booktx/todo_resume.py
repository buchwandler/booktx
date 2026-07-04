"""Resumable bounded-run translation task creation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from booktx.command_hints import (
    check_command,
    translate_todo_resume_command,
)
from booktx.config import Project, _err
from booktx.models import TranslationTask, TranslationTodo
from booktx.status import StatusBundle
from booktx.tasks import create_translation_task, select_translation_record_ids
from booktx.todo_status import (
    build_todo_status,
    current_todo_chapter_id,
    find_incomplete_todo_for_chapter,
    latest_incomplete_todo,
    load_translation_todo,
    recreate_todo_command,
)
from booktx.validate import validate_project

if TYPE_CHECKING:
    from booktx.runtime import RuntimeMode

__all__ = [
    "ensure_single_chapter_todo",
    "resolve_translation_todo",
    "resume_translation_todo",
]


def resolve_translation_todo(
    project: Project,
    bundle: StatusBundle,
    *,
    todo_id: str | None = None,
    latest: bool = False,
) -> TranslationTodo:
    """Resolve a bounded-run todo from ``--todo-id`` or ``--latest``."""
    if bool(todo_id) == bool(latest):
        raise _err(
            "todo_selector_required",
            "pass exactly one of --todo-id or --latest",
        )
    if todo_id is not None:
        todo = load_translation_todo(project, todo_id)
        if todo is None:
            raise _err("unknown_todo", f"unknown todo id: {todo_id}")
        return todo
    todo = latest_incomplete_todo(project, bundle)
    if todo is None:
        raise _err("no_incomplete_todo", "no incomplete translation todo was found")
    return todo


def resume_translation_todo(
    project: Project,
    bundle: StatusBundle,
    *,
    mode: RuntimeMode | None = None,
    todo_id: str | None = None,
    latest: bool = False,
) -> TranslationTask:
    """Create the next bounded translation task pinned to the todo's chapter set."""
    todo = resolve_translation_todo(project, bundle, todo_id=todo_id, latest=latest)
    # Scope validation to the todo's current chapter so unrelated-chapter
    # preflight errors do not block a bounded run.
    scope_chapter = current_todo_chapter_id(todo, bundle)
    report = validate_project(project, chapter_id=scope_chapter)
    status = build_todo_status(
        project,
        todo,
        bundle,
        mode=mode,
        validation_report=report,
        fail_on_warnings=True,
        scope_chapter_id=scope_chapter,
    )
    if status.goal_complete:
        raise _err(
            "todo_complete",
            f"todo {todo.todo_id} is already complete. No further task will be issued.",
        )
    if status.source_drifted:
        raise _err(
            "todo_source_drift",
            (
                f"todo {todo.todo_id} cannot resume because the source changed. "
                "Run `booktx extract .` and create a fresh todo."
            ),
        )
    if status.context_drifted:
        recreate_command = recreate_todo_command(
            project,
            todo,
            mode=mode,
            start_chapter=scope_chapter,
        )
        raise _err(
            "todo_context_drift",
            (
                f"todo {todo.todo_id} cannot resume because the context changed. "
                "Create a fresh bounded todo before requesting more work.\n"
                f"next:\n  {recreate_command}"
            ),
        )
    if report.errors or report.warnings:
        strict_check = check_command(
            project, chapter_id=scope_chapter, fail_on_warnings=True
        )
        raise _err(
            "todo_validation_blocked",
            (
                f"todo {todo.todo_id} cannot resume because "
                f"{strict_check} reports {len(report.errors)} error(s) "
                f"and {len(report.warnings)} warning(s)."
            ),
        )
    current = status.current_chapter
    if current is None:
        raise _err(
            "todo_complete",
            f"todo {todo.todo_id} is already complete. No further task will be issued.",
        )
    selected_chapter = bundle.index.chapters_by_id.get(current.chapter_id)
    if selected_chapter is None:
        raise _err(
            "todo_chapter_missing",
            (
                f"todo {todo.todo_id} cannot resume because planned chapter "
                f"{current.chapter_id} is no longer present."
            ),
        )
    actual_unit, record_ids = select_translation_record_ids(
        bundle,
        selected_chapter,
        unit="batch",
        max_words=todo.batch_words,
    )
    if not record_ids:
        resume_command = translate_todo_resume_command(project, todo_id=todo.todo_id)
        raise _err(
            "todo_no_remaining_records",
            (
                f"todo {todo.todo_id} expected remaining records in chapter "
                f"{selected_chapter.chapter_id}, but none were available. "
                f"Review the todo with `{resume_command}`."
            ),
        )
    return create_translation_task(
        project,
        bundle,
        selected_chapter,
        mode=mode,
        unit=actual_unit,
        record_ids=record_ids,
        requested_max_words=todo.batch_words,
        todo_id=todo.todo_id,
    )


def ensure_single_chapter_todo(
    project: Project,
    bundle: StatusBundle,
    *,
    chapter_id: str,
    batch_words: int,
    max_run_words: int | None = None,
) -> TranslationTodo:
    """Create or reuse a single-chapter todo for an oversized chapter.

    Looks up an existing incomplete todo for the chapter first (prevents
    duplicates on retry). If none exists, builds a new one with ``chapters=1``
    and writes it to disk.
    """
    from booktx.agent_todo import build_translation_todo, write_translation_todo

    existing = find_incomplete_todo_for_chapter(project, bundle, chapter_id)
    if existing is not None:
        return existing
    todo = build_translation_todo(
        project,
        bundle,
        chapters=1,
        batch_words=batch_words,
        max_run_words=max_run_words,
        start_chapter=chapter_id,
    )
    write_translation_todo(project, todo)
    return todo
