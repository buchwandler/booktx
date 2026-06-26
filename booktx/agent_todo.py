"""Pure functions for bounded multi-chapter agent-run todo management.

This module builds and persists the durable run-control artifacts written by
``booktx translate todo-next``.  It does **not** create translation tasks or
ingest files — that is the responsibility of ``booktx.tasks`` and the existing
``translate next`` command.

Design note: ``TranslationTodo`` stores only durable run-control data. It must
remain loadable in a fresh CLI process without command-specific import side
effects.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from booktx.models import (
    TranslationTodo,
    TranslationTodoChapter,
)
from booktx.path_display import display_path

if TYPE_CHECKING:
    from booktx.config import Project
    from booktx.runtime import RuntimeMode
    from booktx.status import (
        ChapterProgress,
        StatusBundle,
    )

__all__ = [
    "build_translation_todo",
    "make_todo_id",
    "render_translation_todo_markdown",
    "select_todo_chapters",
    "write_translation_todo",
]


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------


def make_todo_id(profile: str, first_chapter_id: str, chapter_ids: list[str]) -> str:
    """Derive a deterministic, path-safe todo id.

    Mirrors :func:`booktx.tasks.make_task_id`: a stable ``blake2s`` digest
    (``digest_size=4``) of the joined chapter ids, plus a seconds-precision
    UTC timestamp.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.blake2s(
        "|".join(chapter_ids).encode("utf-8"), digest_size=4
    ).hexdigest()
    return f"bt-todo-{stamp}-{profile}-{first_chapter_id}-{digest}"


# ---------------------------------------------------------------------------
# Chapter selection
# ---------------------------------------------------------------------------


def select_todo_chapters(
    bundle: StatusBundle,
    *,
    chapters: int,
    skip_current: bool = False,
    start_chapter: str | None = None,
) -> list[ChapterProgress]:
    """Select the next *chapters* incomplete chapters in reading order.

    Raises :class:`ValueError` when no chapters have remaining records or when
    the start chapter is not found.
    """
    if chapters < 1:
        raise ValueError("chapters must be >= 1")

    all_chapters = bundle.index.chapter_summaries

    # Build the eligible list: only chapters with remaining records.
    eligible = [c for c in all_chapters if c.records_remaining > 0]

    if start_chapter is not None:
        # Find the start chapter in reading order.
        start_idx = next(
            (i for i, c in enumerate(eligible) if c.chapter_id == start_chapter),
            None,
        )
        if start_idx is None:
            raise ValueError(
                f"start chapter {start_chapter!r} not found or has no remaining records"
            )
        eligible = eligible[start_idx:]

    if skip_current and eligible:
        eligible = eligible[1:]

    if not eligible:
        return []

    return eligible[:chapters]


# ---------------------------------------------------------------------------
# Todo construction
# ---------------------------------------------------------------------------


def build_translation_todo(
    project: Project,
    bundle: StatusBundle,
    *,
    chapters: int,
    batch_words: int,
    max_run_words: int | None = None,
    skip_current: bool = False,
    start_chapter: str | None = None,
) -> TranslationTodo:
    """Build a :class:`TranslationTodo` without writing it.

    Raises :class:`ValueError` when no chapters are pending.
    """
    if chapters < 1:
        raise ValueError("chapters must be >= 1")
    if batch_words < 1:
        raise ValueError("batch_words must be >= 1")

    selected = select_todo_chapters(
        bundle,
        chapters=chapters,
        skip_current=skip_current,
        start_chapter=start_chapter,
    )
    if not selected:
        raise ValueError("no chapters have remaining records")

    todo_chapters = [
        TranslationTodoChapter(
            chapter_id=c.chapter_id,
            title=c.title,
            status=c.status,
            records_total=c.records_total,
            records_translated_at_start=c.records_translated,
            records_remaining_at_start=c.records_remaining,
            source_words_remaining_at_start=c.source_words_remaining,
            pending_chunk_ids=list(c.pending_chunk_ids),
        )
        for c in selected
    ]

    baseline_ref = None
    baseline_sha256 = None
    context_sha256 = None
    source_sha256 = bundle.snapshot.source.source_sha256 or None
    if bundle.snapshot.context.exists and bundle.snapshot.context.ready:
        from booktx.versioning import resolve_current_version

        resolution = resolve_current_version(project)
        baseline_ref = resolution.version_ref
        baseline_sha256 = resolution.baseline_sha256
        context_sha256 = resolution.baseline_sha256

    todo_id = make_todo_id(
        project.profile or "",
        selected[0].chapter_id,
        [c.chapter_id for c in selected],
    )

    target_locale = ""
    if project.profile_config is not None:
        target_locale = (
            project.profile_config.target_locale
            or project.profile_config.target_language
            or ""
        )

    return TranslationTodo(
        todo_id=todo_id,
        profile=project.profile or "",
        target_language=project.config.target_language,
        target_locale=target_locale,
        chapters_requested=chapters,
        batch_words=batch_words,
        max_run_words=max_run_words,
        include_current=not skip_current,
        created_at=datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        baseline_ref=baseline_ref,
        baseline_sha256=baseline_sha256,
        context_sha256=context_sha256,
        source_sha256=source_sha256,
        start_totals=bundle.snapshot.totals,
        chapters=todo_chapters,
    )


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def render_translation_todo_markdown(
    todo: TranslationTodo, project: Project, *, mode: RuntimeMode | None = None
) -> str:
    """Render the human-readable todo markdown.

    The output is the durable loop instruction file an agent reads before
    starting the bounded run.
    """
    from booktx.command_hints import (
        check_command,
        translate_todo_resume_command,
        translate_todo_status_command,
    )

    lines: list[str] = []
    lines.append(f"# booktx agent todo: {todo.todo_id}")
    lines.append("")

    first_chapter = todo.chapters[0] if todo.chapters else None
    max_run_label = f"{todo.max_run_words:,}" if todo.max_run_words else "unlimited"
    lines.append(
        f"Goal: complete {todo.chapters_requested} incomplete chapter(s)"
        f" starting at {first_chapter.chapter_id} {first_chapter.title}".rstrip()
        if first_chapter
        else f"Goal: complete {todo.chapters_requested} incomplete chapter(s)"
    )
    lines.append(f"Per-task budget: {todo.batch_words} source words")
    lines.append(f"Advisory run budget: {max_run_label} source words")
    lines.append(f"Profile: {todo.profile}")
    if project.context_md_path is not None:
        context_path = (
            display_path(project.context_md_path, mode)
            if mode is not None
            else project.context_md_path.relative_to(project.root)
        )
        lines.append(f"Context: {context_path}")
    lines.append("")

    # Stop conditions
    lines.append("## Stop immediately if")
    lines.append("")
    first_chapter_id = todo.chapters[0].chapter_id if todo.chapters else None
    scoped_check = check_command(
        project, mode=mode, chapter_id=first_chapter_id, fail_on_warnings=True
    )
    lines.append(
        f"- `{scoped_check}` reports errors or warnings for the active todo chapter."
    )
    lines.append(
        "- Full-project validation warnings outside this todo"
        " do not block continuing the todo,"
        " but must be resolved before final build."
    )
    lines.append("- `booktx translate insert` rejects the submission.")
    lines.append("- `booktx status` reports source drift or context not ready.")
    lines.append(
        f"- You have completed {todo.chapters_requested} chapter(s) from this todo."
    )
    if todo.max_run_words:
        lines.append(
            f"- The advisory run budget reaches {todo.max_run_words:,} source words; "
            "stop and report progress before requesting more work."
        )
    lines.append("- The source file for the next task is too large to read safely.")
    lines.append("")

    # Loop
    lines.append("## Loop")
    lines.append("")
    lines.append("1. Inspect live todo status:")
    lines.append("")
    lines.append("   ```bash")
    lines.append(
        "   "
        + translate_todo_status_command(
            project,
            mode=mode,
            todo_id=todo.todo_id,
        )
    )
    lines.append("   ```")
    lines.append("")
    lines.append("2. If the todo goal is complete, stop and report progress.")
    lines.append("")
    lines.append("3. Request the next bounded batch:")
    lines.append("")
    lines.append("   ```bash")
    next_cmd = translate_todo_resume_command(
        project,
        mode=mode,
        todo_id=todo.todo_id,
        output_format="block",
    )
    lines.append(f"   {next_cmd}")
    lines.append("   ```")
    lines.append("")
    lines.append("4. Read the printed `Source file`.")
    lines.append("")
    lines.append("5. Fill only the printed `Durable block template`.")
    lines.append("")
    lines.append("6. Submit exactly the printed submit command.")
    lines.append("")
    lines.append("7. Validate the active todo chapter:")
    lines.append("")
    lines.append("   ```bash")
    lines.append(f"   {scoped_check}")
    lines.append("   ```")
    lines.append("   For the final pre-build check, use full-project validation:")
    lines.append("")
    lines.append("   ```bash")
    from booktx.command_hints import validate_command as _validate_command

    lines.append(f"   {_validate_command(project, mode=mode, fail_on_warnings=True)}")
    lines.append("   ```")
    lines.append("")
    lines.append("8. Continue the loop unless a stop condition applies.")
    lines.append("")

    # Planned chapters table
    lines.append("## Planned chapters")
    lines.append("")
    lines.append(
        "| chapter | title | remaining records | "
        "remaining source words | pending chunks |"
    )
    lines.append("|---|---|---:|---:|---|")
    for c in todo.chapters:
        chunks_display = ", ".join(c.pending_chunk_ids) if c.pending_chunk_ids else "-"
        lines.append(
            f"| {c.chapter_id} | {c.title} | {c.records_remaining_at_start} "
            f"| {c.source_words_remaining_at_start:,} | {chunks_display} |"
        )
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# File writing
# ---------------------------------------------------------------------------


def write_translation_todo(
    project: Project,
    todo: TranslationTodo,
    *,
    mode: RuntimeMode | None = None,
) -> tuple[Path, Path]:
    """Persist both the JSON and Markdown todo files atomically.

    Returns ``(json_path, md_path)``.
    """
    from booktx.config import (
        translation_todo_dir,
        translation_todo_json_path,
        translation_todo_markdown_path,
    )
    from booktx.io_utils import write_json_model_atomic, write_text_atomic

    todo_dir = translation_todo_dir(project)
    todo_dir.mkdir(parents=True, exist_ok=True)

    json_path = translation_todo_json_path(project, todo.todo_id)
    md_path = translation_todo_markdown_path(project, todo.todo_id)

    write_json_model_atomic(json_path, todo)
    write_text_atomic(
        md_path, render_translation_todo_markdown(todo, project, mode=mode)
    )

    return json_path, md_path
