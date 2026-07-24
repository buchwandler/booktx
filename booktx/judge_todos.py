"""Durable bounded scope records for judge runs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def todo_dir(project: Any) -> Path:
    profile_dir = project.profile_dir
    assert isinstance(profile_dir, Path)
    return profile_dir / "judge-todos"


def _path(project: Any, todo_id: str) -> Path:
    return todo_dir(project) / f"{todo_id}.json"


@dataclass(frozen=True)
class JudgeTodo:
    """Immutable, snapshot-pinned scope for a multi-batch judge run."""

    todo_id: str
    profile: str
    purpose: str
    revision_focus: str
    snapshot_id: str | None
    context_sha256: str | None
    chapter_ids: tuple[str, ...]
    max_records: int | None
    max_sentences: int | None
    max_words: int
    created_at: str
    schema_version: int = 2
    from_chapter: str | None = None
    through_chapter: str | None = None
    batch_records: int | None = None
    batch_sentences: int | None = None
    batch_words: int | None = None
    batch_rendered_lines: int | None = None
    updated_at: str | None = None

    def __post_init__(self) -> None:
        """Normalize v1 aliases into the v2 batch-policy view."""
        if self.batch_records is None:
            object.__setattr__(self, "batch_records", self.max_records)
        if self.batch_sentences is None:
            object.__setattr__(self, "batch_sentences", self.max_sentences)
        if self.batch_words is None:
            object.__setattr__(self, "batch_words", self.max_words)
        if self.from_chapter is None and self.chapter_ids:
            object.__setattr__(self, "from_chapter", self.chapter_ids[0])
        if self.through_chapter is None and self.chapter_ids:
            object.__setattr__(self, "through_chapter", self.chapter_ids[-1])

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "booktx.judge-todo.v2",
            "todo_id": self.todo_id,
            "profile": self.profile,
            "purpose": self.purpose,
            "revision_focus": self.revision_focus,
            "snapshot_id": self.snapshot_id,
            "context_sha256": self.context_sha256,
            "chapter_ids": list(self.chapter_ids),
            "from_chapter": self.from_chapter,
            "through_chapter": self.through_chapter,
            "batch_records": self.batch_records,
            "batch_sentences": self.batch_sentences,
            "batch_words": self.batch_words,
            "batch_rendered_lines": self.batch_rendered_lines,
            # Keep v1 names in new artifacts for older readers and tooling.
            "max_records": self.max_records,
            "max_sentences": self.max_sentences,
            "max_words": self.max_words,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def write_todo(project: Any, todo: JudgeTodo) -> Path:
    path = _path(project, todo.todo_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(todo.as_dict(), indent=2) + "\n", encoding="utf-8")
    return path


def load_todo(project: Any, todo_id: str) -> JudgeTodo | None:
    path = _path(project, todo_id)
    if not path.is_file():
        return None
    raw = json.loads(path.read_text("utf-8"))
    batch_records = raw.get("batch_records", raw.get("max_records"))
    batch_sentences = raw.get("batch_sentences", raw.get("max_sentences"))
    batch_words = int(raw.get("batch_words", raw.get("max_words", 900)))
    return JudgeTodo(
        todo_id=raw["todo_id"],
        profile=raw["profile"],
        purpose=raw["purpose"],
        revision_focus=raw.get("revision_focus", "general"),
        snapshot_id=raw.get("snapshot_id"),
        context_sha256=raw.get("context_sha256"),
        chapter_ids=tuple(raw["chapter_ids"]),
        max_records=raw.get("max_records", batch_records),
        max_sentences=raw.get("max_sentences", batch_sentences),
        max_words=int(raw.get("max_words", batch_words)),
        created_at=raw["created_at"],
        schema_version=int(raw.get("schema", "booktx.judge-todo.v1").rsplit("v", 1)[-1])
        if isinstance(raw.get("schema"), str) and "v" in raw["schema"]
        else 1,
        from_chapter=raw.get("from_chapter"),
        through_chapter=raw.get("through_chapter"),
        batch_records=batch_records,
        batch_sentences=batch_sentences,
        batch_words=batch_words,
        batch_rendered_lines=raw.get("batch_rendered_lines"),
        updated_at=raw.get("updated_at"),
    )


def latest_todo(project: Any) -> JudgeTodo | None:
    paths = (
        sorted(todo_dir(project).glob("judge-todo-*.json"))
        if todo_dir(project).is_dir()
        else []
    )
    return load_todo(project, paths[-1].stem) if paths else None


def new_todo(
    project: Any,
    *,
    purpose: str,
    revision_focus: str,
    snapshot_id: str | None,
    context_sha256: str | None,
    chapter_ids: list[str],
    max_records: int | None = None,
    max_sentences: int | None = None,
    max_words: int = 900,
    max_rendered_lines: int | None = None,
    from_chapter: str | None = None,
    through_chapter: str | None = None,
    batch_records: int | None = None,
    batch_sentences: int | None = None,
    batch_words: int | None = None,
    batch_rendered_lines: int | None = None,
) -> JudgeTodo:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    effective_records = batch_records if batch_records is not None else max_records
    effective_sentences = (
        batch_sentences if batch_sentences is not None else max_sentences
    )
    effective_words = batch_words if batch_words is not None else max_words
    effective_lines = (
        batch_rendered_lines if batch_rendered_lines is not None else max_rendered_lines
    )
    todo = JudgeTodo(
        todo_id=f"judge-todo-{stamp}",
        profile=project.profile or "",
        purpose=purpose,
        revision_focus=revision_focus,
        snapshot_id=snapshot_id,
        context_sha256=context_sha256,
        chapter_ids=tuple(chapter_ids),
        max_records=effective_records,
        max_sentences=effective_sentences,
        max_words=effective_words,
        created_at=datetime.now(timezone.utc).isoformat(),
        from_chapter=from_chapter or (chapter_ids[0] if chapter_ids else None),
        through_chapter=through_chapter or (chapter_ids[-1] if chapter_ids else None),
        batch_records=effective_records,
        batch_sentences=effective_sentences,
        batch_words=effective_words,
        batch_rendered_lines=effective_lines,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    write_todo(project, todo)
    return todo
