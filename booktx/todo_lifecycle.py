"""Lifecycle state and overlap handling for translation todos.

Todo JSON and Markdown files describe an immutable requested scope. This
module stores the mutable operational state in adjacent sidecars so a restart
or supersession is auditable without rewriting historical scope artifacts.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from booktx.config import (
    Project,
    _err,
    translation_todo_lifecycle_path,
)
from booktx.io_utils import write_json_model_atomic
from booktx.models import TranslationTodo, TranslationTodoLifecycle
from booktx.versioning import canonical_json_sha256

if TYPE_CHECKING:
    pass

__all__ = [
    "TodoLifecycleEntry",
    "todo_scope_fingerprint",
    "load_todo_lifecycle",
    "write_todo_lifecycle",
    "todo_lifecycle_state",
    "list_todo_lifecycle",
    "open_todo_ids",
    "supersede_todos_atomically",
    "abandon_todo",
]


class TodoLifecycleEntry:
    """A todo paired with its lifecycle sidecar state."""

    __slots__ = ("todo", "lifecycle")

    def __init__(self, todo: TranslationTodo, lifecycle: TranslationTodoLifecycle):
        self.todo = todo
        self.lifecycle = lifecycle


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def todo_scope_fingerprint(todo: TranslationTodo) -> str:
    """Return a stable hash for the requested scope and pinned inputs."""
    payload = {
        "profile": todo.profile,
        "chapter_ids": [chapter.chapter_id for chapter in todo.chapters],
        "batch_words": todo.batch_words,
        "max_run_words": todo.max_run_words,
        "source_sha256": todo.source_sha256,
        "context_sha256": todo.context_sha256,
        "mandatory_glossary_sha256": todo.mandatory_glossary_sha256,
        "baseline_sha256": todo.baseline_sha256,
    }
    return canonical_json_sha256(payload)


def load_todo_lifecycle(
    project: Project, todo: TranslationTodo
) -> TranslationTodoLifecycle:
    """Load a sidecar, treating legacy todos without one as open."""
    path = translation_todo_lifecycle_path(project, todo.todo_id)
    if not path.is_file():
        return TranslationTodoLifecycle(
            todo_id=todo.todo_id, updated_at=todo.created_at
        )
    try:
        lifecycle = TranslationTodoLifecycle.model_validate_json(
            path.read_text("utf-8")
        )
    except (ValidationError, ValueError) as exc:
        raise _err(
            "invalid_todo_lifecycle", f"todo lifecycle {path.name} is invalid: {exc}"
        ) from exc
    if lifecycle.todo_id != todo.todo_id:
        raise _err(
            "invalid_todo_lifecycle",
            f"todo lifecycle {path.name} references {lifecycle.todo_id}, "
            f"expected {todo.todo_id}",
        )
    return lifecycle


def write_todo_lifecycle(
    project: Project,
    todo: TranslationTodo,
    *,
    state: str = "open",
    reason: str | None = None,
    superseded_by: str | None = None,
    actor: str | None = None,
) -> Path:
    """Write one lifecycle sidecar atomically and return its path."""
    if state not in {"open", "completed", "abandoned", "superseded"}:
        raise _err("invalid_todo_lifecycle_state", f"unsupported todo state: {state}")
    lifecycle = TranslationTodoLifecycle(
        todo_id=todo.todo_id,
        state=state,  # type: ignore[arg-type]
        updated_at=_now(),
        reason=reason,
        superseded_by=superseded_by,
        actor=actor,
    )
    path = translation_todo_lifecycle_path(project, todo.todo_id)
    write_json_model_atomic(path, lifecycle)
    return path


def todo_lifecycle_state(project: Project, todo: TranslationTodo) -> str:
    return load_todo_lifecycle(project, todo).state


def list_todo_lifecycle(
    project: Project, todos: Iterable[TranslationTodo]
) -> list[TodoLifecycleEntry]:
    return [
        TodoLifecycleEntry(todo, load_todo_lifecycle(project, todo)) for todo in todos
    ]


def open_todo_ids(project: Project, todos: Iterable[TranslationTodo]) -> set[str]:
    return {
        entry.todo.todo_id
        for entry in list_todo_lifecycle(project, todos)
        if entry.lifecycle.state == "open"
    }


def supersede_todos_atomically(
    project: Project,
    todos: Iterable[TranslationTodo],
    *,
    superseded_by: str,
    reason: str,
    actor: str | None = None,
) -> None:
    """Mark all supplied todos superseded, rolling back sidecars on failure."""
    todo_list = list(todos)
    original: dict[Path, bytes | None] = {}
    for todo in todo_list:
        path = translation_todo_lifecycle_path(project, todo.todo_id)
        original[path] = path.read_bytes() if path.exists() else None
    try:
        for todo in todo_list:
            write_todo_lifecycle(
                project,
                todo,
                state="superseded",
                reason=reason,
                superseded_by=superseded_by,
                actor=actor,
            )
    except Exception:
        for path, content in original.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
        raise


def abandon_todo(
    project: Project,
    todo: TranslationTodo,
    *,
    reason: str,
    actor: str | None = None,
) -> Path:
    """Abandon a todo idempotently while preserving its scope files."""
    current = load_todo_lifecycle(project, todo)
    if current.state == "abandoned":
        return translation_todo_lifecycle_path(project, todo.todo_id)
    if current.state == "superseded":
        raise _err(
            "todo_lifecycle_conflict",
            f"todo {todo.todo_id} is already superseded",
        )
    return write_todo_lifecycle(
        project, todo, state="abandoned", reason=reason, actor=actor
    )
