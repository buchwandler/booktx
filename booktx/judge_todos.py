"""Durable bounded scope records for judge runs."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def todo_dir(project: Any) -> Path:
    assert project.profile_dir is not None
    return project.profile_dir / "judge-todos"


def _path(project: Any, todo_id: str) -> Path:
    return todo_dir(project) / f"{todo_id}.json"


@dataclass(frozen=True)
class JudgeTodo:
    todo_id: str
    profile: str
    purpose: str
    revision_focus: str
    snapshot_id: str | None
    context_sha256: str | None
    chapter_ids: tuple[str, ...]
    max_records: int | None
    max_words: int
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return {"schema": "booktx.judge-todo.v1", **self.__dict__, "chapter_ids": list(self.chapter_ids)}


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
    return JudgeTodo(
        todo_id=raw["todo_id"], profile=raw["profile"], purpose=raw["purpose"],
        revision_focus=raw["revision_focus"], snapshot_id=raw.get("snapshot_id"),
        context_sha256=raw.get("context_sha256"), chapter_ids=tuple(raw["chapter_ids"]),
        max_records=raw.get("max_records"), max_words=int(raw["max_words"]),
        created_at=raw["created_at"],
    )


def latest_todo(project: Any) -> JudgeTodo | None:
    paths = sorted(todo_dir(project).glob("judge-todo-*.json")) if todo_dir(project).is_dir() else []
    return load_todo(project, paths[-1].stem) if paths else None


def new_todo(project: Any, *, purpose: str, revision_focus: str, snapshot_id: str | None,
             context_sha256: str | None, chapter_ids: list[str], max_records: int | None,
             max_words: int) -> JudgeTodo:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    todo = JudgeTodo(f"judge-todo-{stamp}", project.profile or "", purpose, revision_focus,
                     snapshot_id, context_sha256, tuple(chapter_ids), max_records, max_words,
                     datetime.now(timezone.utc).isoformat())
    write_todo(project, todo)
    return todo
