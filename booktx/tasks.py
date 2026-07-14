"""Translation-task creation, durable task paths, and submission hints.

Centralizes the durable-file layout and id derivation for translation tasks
so the command layer stops reconstructing paths and submit-hints inline.

Profile-layout projects (primary):

    translations/<profile>/tasks/<id>.json
    translations/<profile>/tasks/<id>.source.block.txt
    translations/<profile>/ingest/<id>.json
    translations/<profile>/ingest/<id>.block.txt

Legacy single-layout projects (compatibility only):

    .booktx/tasks/<id>.json
    .booktx/tasks/<id>.source.block.txt
    .booktx/ingest/<id>.json
    .booktx/ingest/<id>.block.txt

All task-path access goes through ``translation_task_dir(project)`` which
enforces the profile-required guard for source-only projects. The
``TaskPaths`` value object bundles the four per-task files and renders the
project-relative display strings and submit commands the CLI prints.
"""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from booktx.block_protocol import SOURCE_ONLY_DIRECTIVE_PREFIXES
from booktx.command_hints import translate_insert_command, translate_lint_block_command
from booktx.config import (
    Project,
    load_translation_version_ledger,
    translation_ingest_block_path,
    translation_ingest_path,
    translation_task_agent_brief_path,
    translation_task_source_block_path,
)
from booktx.context import (
    TranslationContext,
    ensure_context_view_snapshot,
    load_context,
)
from booktx.glossary_match import live_mandatory_glossary_sha256
from booktx.glossary_tasking import applicable_glossary_snapshots
from booktx.io_utils import write_json_text_atomic, write_text_atomic
from booktx.models import (
    ApplicableGlossaryEntrySnapshot,
    ApplicableTermbaseEntrySnapshot,
    TranslationTask,
    TranslationTaskRecord,
)
from booktx.path_display import display_path
from booktx.termbase_tasking import collect_applicable_termbase_for_record_sources
from booktx.validate import load_validation_context
from booktx.versioning import canonical_json_sha256, resolve_current_version

if TYPE_CHECKING:
    from booktx.progress import SourceRecordView
    from booktx.runtime import RuntimeMode
    from booktx.status import ChapterProgress, StatusBundle

__all__ = [
    "TaskPaths",
    "make_task_id",
    "task_paths",
    "project_relative",
    "limit_records_by_words",
    "select_translation_record_ids",
    "create_translation_task",
    "write_ingest_template",
    "write_block_ingest_template",
    "write_task_source_block",
]


def project_relative(path: Path, root: Path) -> str:
    """Return a stable project-relative display path when possible."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def make_task_id(chapter_id: str, first_record_id: str, record_ids: list[str]) -> str:
    """Derive a deterministic, path-safe task id.

    Uses a stable ``blake2s`` digest (``digest_size=4``) of the joined record
    ids instead of Python's process-randomized ``hash()``, plus a
    seconds-precision UTC timestamp so same-day collisions are extremely
    unlikely.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    record_part = first_record_id.replace("-", "")
    digest = hashlib.blake2s(
        "|".join(record_ids).encode("utf-8"), digest_size=4
    ).hexdigest()
    return f"bt-task-{stamp}-{chapter_id}-{record_part}-{digest}"


@dataclass(frozen=True, slots=True)
class TaskPaths:
    """The durable files owned by one translation task."""

    task_json: Path
    agent_brief: Path
    source_block: Path
    ingest_json: Path
    ingest_block: Path

    def display(
        self, root: Path, *, mode: RuntimeMode | None = None
    ) -> TaskPathDisplay:
        return TaskPathDisplay(
            task_json=(
                display_path(self.task_json, mode)
                if mode is not None
                else project_relative(self.task_json, root)
            ),
            agent_brief=(
                display_path(self.agent_brief, mode)
                if mode is not None
                else project_relative(self.agent_brief, root)
            ),
            source_block=(
                display_path(self.source_block, mode)
                if mode is not None
                else project_relative(self.source_block, root)
            ),
            ingest_json=(
                display_path(self.ingest_json, mode)
                if mode is not None
                else project_relative(self.ingest_json, root)
            ),
            ingest_block=(
                display_path(self.ingest_block, mode)
                if mode is not None
                else project_relative(self.ingest_block, root)
            ),
        )

    def block_submit_hint(
        self,
        task_id: str,
        root: Path,
        *,
        mode: RuntimeMode | None = None,
    ) -> str:
        profile_part = ""
        if mode is None and self.task_json.parent.parent.name != ".booktx":
            profile_part = f" --profile {self.task_json.parent.parent.name}"
        return (
            f"booktx translate insert .{profile_part} --task-id {task_id} "
            f"--file "
            f"{display_path(self.ingest_block, mode) if mode is not None else project_relative(self.ingest_block, root)} "
            "--format block"
        )

    def json_submit_hint(
        self,
        task_id: str,
        root: Path,
        *,
        mode: RuntimeMode | None = None,
    ) -> str:
        profile_part = ""
        if mode is None and self.task_json.parent.parent.name != ".booktx":
            profile_part = f" --profile {self.task_json.parent.parent.name}"
        return (
            f"booktx translate insert .{profile_part} --task-id {task_id} "
            f"--json-file "
            f"{display_path(self.ingest_json, mode) if mode is not None else project_relative(self.ingest_json, root)}"
        )

    def block_stdin_submit_hint(
        self,
        task_id: str,
        *,
        mode: RuntimeMode | None = None,
    ) -> str:
        profile_part = ""
        if mode is None and self.task_json.parent.parent.name != ".booktx":
            profile_part = f" --profile {self.task_json.parent.parent.name}"
        return (
            f"booktx translate insert .{profile_part} --task-id {task_id} "
            "--stdin --format block <<'BOOKTX'"
        )


@dataclass(frozen=True, slots=True)
class TaskPathDisplay:
    """Project-relative display strings for a task's durable files."""

    task_json: str
    agent_brief: str
    source_block: str
    ingest_json: str
    ingest_block: str


def task_paths(project: Project, task_id: str) -> TaskPaths:
    """Return the :class:`TaskPaths` bundle for ``task_id``.

    Routes through ``translation_task_dir(project)`` so a source-only
    project (no selected profile) hits the profile-required guard instead of
    silently assuming legacy ``.booktx/tasks`` paths.
    """
    from booktx.config import translation_task_dir

    return TaskPaths(
        task_json=translation_task_dir(project) / f"{task_id}.json",
        agent_brief=translation_task_agent_brief_path(project, task_id),
        source_block=translation_task_source_block_path(project, task_id),
        ingest_json=translation_ingest_path(project, task_id),
        ingest_block=translation_ingest_block_path(project, task_id),
    )


def _context_markdown_path(task: TranslationTask) -> str:
    if not task.context_view_path:
        return ""
    return task.context_view_path.replace("context.json", "context.md")


def _style_directives_for_record(record: TranslationTaskRecord) -> list[str]:
    directives: list[str] = []
    if "\u2013" in record.source or "\u2014" in record.source:
        directives.append(
            "source contains an en/em dash; preserve a German dash cue "
            "(– or —) in the target unless the meaning is truly removed"
        )
    return directives


def _render_glossary_snapshot(snapshot: ApplicableGlossaryEntrySnapshot) -> str:
    approved = (
        " / ".join(
            [item for item in [snapshot.target, *snapshot.target_variants] if item]
        )
        or "(none)"
    )
    forbidden = " / ".join(snapshot.forbidden_targets) or "(none)"
    policy: list[str] = []
    if snapshot.require_target:
        policy.append("required")
    if snapshot.forbidden_targets:
        policy.append("forbidden")
    policy_text = ", ".join(policy) or "binding"
    match_policy = "case-sensitive" if snapshot.case_sensitive else "case-insensitive"
    note = f"; note: {snapshot.notes}" if snapshot.notes else ""
    return (
        f"# glossary: {snapshot.source} -> {approved}; "
        f"{policy_text}; forbidden: {forbidden}; "
        f"matched source cue: {snapshot.matched_source_cue}; "
        f"target match is literal, boundary-aware, {match_policy}{note}"
    )


def _render_termbase_snapshot(snapshot: ApplicableTermbaseEntrySnapshot) -> str:
    note = snapshot.sense or snapshot.rationale
    return f"# termbase: {snapshot.entry_id} — {note}".rstrip(" —")


def _record_group_label(record: TranslationTaskRecord) -> str | None:
    if record.block_id:
        return f"Block {record.block_id}"
    if record.span_index is not None:
        return f"Span {record.span_index}"
    return None


def _load_task_context_snapshot(
    project: Project, task: TranslationTask
) -> TranslationContext | None:
    if task.context_view_path:
        return load_validation_context(
            project, context_view_path=task.context_view_path
        )
    return load_context(project)


def _task_relevant_glossary(
    task: TranslationTask,
) -> list[ApplicableGlossaryEntrySnapshot]:
    seen: set[tuple[object, ...]] = set()
    snapshots: list[ApplicableGlossaryEntrySnapshot] = []
    for record in task.records:
        for snapshot in record.applicable_glossary:
            key = (
                snapshot.source,
                tuple(snapshot.source_variants),
                snapshot.matched_source_cue,
                snapshot.target,
                tuple(snapshot.target_variants),
                snapshot.require_target,
                tuple(snapshot.forbidden_targets),
                snapshot.enforce,
                snapshot.case_sensitive,
                snapshot.notes,
            )
            if key in seen:
                continue
            seen.add(key)
            snapshots.append(snapshot)
    snapshots.sort(key=lambda item: (item.source.casefold(), item.matched_source_cue))
    return snapshots


def _task_relevant_termbase(
    task: TranslationTask,
) -> list[ApplicableTermbaseEntrySnapshot]:
    snapshots: dict[str, ApplicableTermbaseEntrySnapshot] = {}
    for record in task.records:
        for snapshot in record.applicable_termbase:
            snapshots.setdefault(snapshot.entry_id, snapshot)
    return [snapshots[key] for key in sorted(snapshots)]


def _render_action_contract_section(
    display: TaskPathDisplay,
    lint_hint: str,
    submit_hint: str,
    task: TranslationTask,
) -> list[str]:
    lines = [
        "# booktx translation task brief",
        "",
        "## Action contract",
        "",
        f"- Read this brief first: {display.agent_brief}",
        f"- Edit only: {display.ingest_block}",
        f"- Source reference only: {display.source_block}",
        "- Write only translated target prose under each `>>>` header.",
        "- Reserved directives are source-only and must never be copied into target text: "
        + ", ".join(SOURCE_ONLY_DIRECTIVE_PREFIXES),
        f"- Lint before submit: {lint_hint}",
        f"- Submit only after lint passes: {submit_hint}",
    ]
    if task.context_view_path:
        lines.append(
            f"- Full immutable context snapshot: {_context_markdown_path(task)}"
        )
    return lines


def _render_task_identity_section(task: TranslationTask) -> list[str]:
    return [
        "",
        "## Task identity",
        "",
        f"- task: {task.task_id}",
        f"- todo: {task.todo_id or 'none'}",
        f"- chapter: {task.chapter_id} {task.chapter_title}".rstrip(),
        f"- record count: {task.record_count}",
        f"- source words: {task.source_words}",
        f"- target locale: {task.target_locale or task.target_language}",
        f"- context view sha256: {task.context_view_sha256 or ''}",
        f"- source sha256: {task.source_sha256 or ''}",
    ]


def _render_global_policy_section(
    context: TranslationContext | None,
) -> list[str]:
    if context is None:
        return []
    style = context.style
    lines: list[str] = ["", "## Global translation policy", ""]
    if style.prose_style:
        lines.append(f"- prose style: {style.prose_style}")
    if style.dialogue_style:
        lines.append(f"- dialogue: {style.dialogue_style}")
    if style.register_level:
        lines.append(f"- register: {style.register_level}")
    if style.sentence_policy:
        lines.append(f"- sentence policy: {style.sentence_policy}")
    if style.punctuation_policy:
        lines.append(f"- punctuation: {style.punctuation_policy}")
    if style.units_policy:
        lines.append(f"- units: {style.units_policy}")
    for rule in context.global_rules:
        lines.append(f"- global rule: {rule}")
    answered = [
        question
        for question in context.questions
        if question.status == "answered" and (question.answer or "").strip()
    ]
    if answered:
        lines.append("- answered decisions:")
        for question in answered:
            topic = question.topic.replace("_", " ")
            lines.append(f"  - {topic}: {question.answer}")
    return lines


def _render_terminology_section(
    glossary: list[ApplicableGlossaryEntrySnapshot],
    termbase: list[ApplicableTermbaseEntrySnapshot],
    protected_terms: list[str],
) -> list[str]:
    lines: list[str] = ["", "## Task-relevant terminology", ""]
    if glossary:
        lines.append("### Glossary")
        for snapshot in glossary:
            approved = (
                " / ".join(
                    [
                        item
                        for item in [snapshot.target, *snapshot.target_variants]
                        if item
                    ]
                )
                or "(none)"
            )
            forbidden = " / ".join(snapshot.forbidden_targets) or "(none)"
            lines.append(
                f"- {snapshot.source} -> {approved}; matched: {snapshot.matched_source_cue}; "
                f"forbidden: {forbidden}"
            )
        lines.append("")
    if termbase:
        lines.append("### Termbase")
        for snapshot in termbase:
            preferred = " / ".join(snapshot.target_preferred) or "(none)"
            forbidden = " / ".join(snapshot.target_forbidden) or "(none)"
            note = snapshot.sense or snapshot.rationale
            lines.append(
                f"- {snapshot.entry_id}: {snapshot.source} -> {preferred}; "
                f"forbidden: {forbidden}; matched: {snapshot.matched_source_cue}; "
                f"note: {note or '(none)'}"
            )
        lines.append("")
    if protected_terms:
        lines.append("### Protected terms and names")
        for term in protected_terms:
            lines.append(f"- {term}")
        lines.append("")
    return lines


def _render_continuity_section(
    context: TranslationContext | None,
    task: TranslationTask,
) -> list[str]:
    lines: list[str] = ["## Continuity", ""]
    if context is not None and context.chapter_contexts:
        previous = context.chapter_contexts[-1]
        lines.append(
            f"- previous chapter note: {previous.chapter_id} {previous.title}".rstrip()
        )
        if previous.translation_summary:
            lines.append(
                f"- previous translation summary: {previous.translation_summary}"
            )
        if previous.decisions_added:
            lines.append("- previous decisions: " + "; ".join(previous.decisions_added))
        open_issues = [
            issue for note in context.chapter_contexts for issue in note.open_issues
        ]
        if open_issues:
            lines.append("- unresolved issues:")
            for issue in open_issues:
                lines.append(f"  - {issue}")
    else:
        lines.append("- no prior chapter note is available for this task.")
    if task.context_view_path:
        lines.append(f"- full context snapshot: {_context_markdown_path(task)}")
    return lines


def _render_source_records_section(task: TranslationTask) -> list[str]:
    lines: list[str] = ["", "## Source records", ""]
    current_group: str | None = None
    for record in task.records:
        label = _record_group_label(record)
        if label and label != current_group:
            if current_group is not None:
                lines.append("")
            lines.extend([f"### {label}", ""])
            current_group = label
        lines.append(f">>> {record.id}")
        lines.append(f"SOURCE: {record.source}")
        if record.applicable_glossary:
            lines.append("GLOSSARY:")
            for snapshot in record.applicable_glossary:
                lines.append("  " + _render_glossary_snapshot(snapshot)[2:])
        else:
            lines.append("GLOSSARY: (none)")
        if record.applicable_termbase:
            lines.append("TERMBASE:")
            for snapshot in record.applicable_termbase:
                lines.append("  " + _render_termbase_snapshot(snapshot)[2:])
        else:
            lines.append("TERMBASE: (none)")
        styles = _style_directives_for_record(record)
        if styles:
            lines.append("STYLE:")
            for style in styles:
                lines.append(f"  - {style}")
        else:
            lines.append("STYLE: (none)")
        lines.append("")
    return lines


def _render_task_agent_brief(
    project: Project,
    task: TranslationTask,
    *,
    mode: RuntimeMode | None = None,
) -> str:
    paths = task_paths(project, task.task_id)
    display = paths.display(project.root, mode=mode)
    lint_hint = translate_lint_block_command(
        project,
        mode=mode,
        task_id=task.task_id,
        file_path=display.ingest_block,
    )
    submit_hint = translate_insert_command(
        project,
        mode=mode,
        task_id=task.task_id,
        file_path=display.ingest_block,
        input_format="block",
    )
    context = _load_task_context_snapshot(project, task)
    glossary = _task_relevant_glossary(task)
    termbase = _task_relevant_termbase(task)
    protected_terms = sorted(
        {term for record in task.records for term in record.protected_terms},
        key=str.casefold,
    )
    lines = _render_action_contract_section(display, lint_hint, submit_hint, task)
    lines.extend(_render_task_identity_section(task))
    lines.extend(_render_global_policy_section(context))
    lines.extend(_render_terminology_section(glossary, termbase, protected_terms))
    lines.extend(_render_continuity_section(context, task))
    lines.extend(_render_source_records_section(task))
    return "\n".join(lines).rstrip() + "\n"


def write_task_agent_brief(
    project: Project,
    task: TranslationTask,
    *,
    mode: RuntimeMode | None = None,
) -> Path:
    """Create the deterministic read-only task brief for a translation task."""
    path = translation_task_agent_brief_path(project, task.task_id)
    if path.exists():
        return path
    write_text_atomic(path, _render_task_agent_brief(project, task, mode=mode))
    return path


def write_ingest_template(project: Project, task: TranslationTask) -> Path:
    """Create the durable JSON submission file for a task without overwriting work."""
    path = translation_ingest_path(project, task.task_id)
    if path.exists():
        return path
    payload = {
        "schema_version": 2,
        "profile": task.profile or None,
        "task_id": task.task_id,
        "translation_version": task.translation_version,
        "records": [{"id": record.id, "target": ""} for record in task.records],
    }
    import json

    write_json_text_atomic(path, json.dumps(payload, indent=2, ensure_ascii=False))
    return path


def write_block_ingest_template(
    project: Project,
    task: TranslationTask,
    *,
    mode: RuntimeMode | None = None,
) -> Path:
    """Create the durable block submission file for a task without overwriting work.

    The file starts with metadata comment headers (ignored by the block parser)
    followed by one ``>>> RECORD_ID`` header per record. The agent fills in the
    target text under each header.
    """
    path = translation_ingest_block_path(project, task.task_id)
    if path.exists():
        return path
    paths = task_paths(project, task.task_id)
    display = paths.display(project.root, mode=mode)
    source_display = display.source_block
    brief_display = display.agent_brief
    block_display = display.ingest_block

    submit_hint = translate_insert_command(
        project,
        mode=mode,
        task_id=task.task_id,
        file_path=block_display,
    )
    lint_hint = translate_lint_block_command(
        project,
        mode=mode,
        task_id=task.task_id,
        file_path=block_display,
    )
    context_display_path = (
        task.context_view_path.replace("context.json", "context.md")
        if task.context_view_path
        else ""
    )
    record_chunks = sorted({record.chunk_id for record in task.records})
    headers = [
        "# editable target-only block",
        "# under each >>> header, write only translated target prose",
        "# do not paste # glossary:, # style:, or # termbase: lines from the source block",
        f"# task brief: {brief_display}",
        f"# source reference: {source_display}",
        f"# lint before submit: {lint_hint}",
        f"# submit after lint passes: {submit_hint}",
        "# metadata below is read-only and ignored by the parser",
        f"# profile: {task.profile or 'none'}",
        f"# target: {task.target_locale or task.target_language}",
        f"# task: {task.task_id}",
        f"# chapter: {task.chapter_id} {task.chapter_title}".rstrip(),
        f"# record_chunks: {', '.join(record_chunks)}",
        "# note: record ids are chunk-based; record id prefixes may differ from chapter ids",
        f"# translation_version: {task.translation_version or 'none'}",
        f"# baseline: {task.baseline_ref or task.translation_version or 'none'}",
        f"# baseline_sha256: {task.baseline_sha256 or ''}",
        f"# context_sha256: {task.context_sha256 or ''}",
        f"# context_view_sha256: {task.context_view_sha256 or ''}",
        f"# context_notes_scope: {task.context_notes_scope or ''}",
        f"# context_target_chapter_id: {task.context_target_chapter_id or ''}",
        f"# context_notes_through_chapter_id: {task.context_notes_through_chapter_id or ''}",
        f"# context_view_path: {task.context_view_path or ''}",
        f"# context_file: {context_display_path}",
        f"# source_sha256: {task.source_sha256 or ''}",
        f"# source: {source_display}",
        f"# submit: {submit_hint}",
        "# note: submit only this block file; the JSON template targets stay empty unless you use JSON mode",
        "",
    ]
    parts = [f">>> {record.id}" for record in task.records]
    write_text_atomic(path, "\n".join(headers + parts).rstrip() + "\n")
    return path


def write_task_source_block(
    project: Project,
    task: TranslationTask,
    *,
    mode: RuntimeMode | None = None,
) -> Path:
    """Create the durable source-view file for a task without overwriting work.

    Holds the original source text for each record in the task so a coding
    agent can translate against a stable file instead of a large stdout dump.
    """
    path = translation_task_source_block_path(project, task.task_id)
    if path.exists():
        return path
    display = task_paths(project, task.task_id).display(project.root, mode=mode)
    lint_hint = translate_lint_block_command(
        project,
        mode=mode,
        task_id=task.task_id,
        file_path=display.ingest_block,
    )
    submit_hint = translate_insert_command(
        project,
        mode=mode,
        task_id=task.task_id,
        file_path=display.ingest_block,
        input_format="block",
    )
    parts = [
        "# booktx translation source block",
        "# reference only: do not edit or submit this file",
        "# translate each source body into the matching header in the ingest file",
        "# source-only directives:",
        "#   # glossary: binding terminology instruction",
        "#   # style: record-local style instruction",
        "#   # termbase: semantic terminology context",
        "# never copy source-only directives into target text",
        f"# editable file: {display.ingest_block}",
        f"# lint: {lint_hint}",
        f"# submit: {submit_hint}",
        f"# task brief: {display.agent_brief}",
        "",
        f"# profile: {task.profile or 'none'}",
        f"# target: {task.target_locale or task.target_language}",
        f"# task: {task.task_id}",
        f"# chapter: {task.chapter_id} {task.chapter_title}".rstrip(),
        f"# unit: {task.unit}",
        f"# records: {task.record_count}",
        f"# source words: {task.source_words}",
        f"# applicable_termbase_sha256: {task.applicable_termbase_sha256 or ''}",
        "",
    ]
    task_snapshots = {
        snapshot.entry_id: snapshot
        for record in task.records
        for snapshot in record.applicable_termbase
    }
    if task_snapshots:
        parts.extend(
            [
                "# applicable termbase:",
                "# id | source cue | preferred | forbidden | note",
            ]
        )
        for snapshot in sorted(task_snapshots.values(), key=lambda item: item.entry_id):
            preferred = " / ".join(snapshot.target_preferred) or "(none)"
            forbidden = " / ".join(snapshot.target_forbidden) or "(none)"
            note = snapshot.sense or snapshot.rationale
            parts.append(
                f"# {snapshot.entry_id} | {snapshot.source} | {preferred} | {forbidden} | {note}".rstrip()
            )
        parts.append("")

    for idx, record in enumerate(task.records):
        if idx:
            parts.append("")
        parts.append(f">>> {record.id}")
        for snapshot in record.applicable_glossary:
            parts.append(_render_glossary_snapshot(snapshot))
        for style in _style_directives_for_record(record):
            parts.append(f"# style: {style}")
        for snapshot in record.applicable_termbase:
            parts.append(_render_termbase_snapshot(snapshot))
        parts.append(record.source)
    write_text_atomic(path, "\n".join(parts).rstrip() + "\n")
    return path


def limit_records_by_words(
    record_ids: list[str],
    source_by_id: Mapping[str, SourceRecordView],
    max_words: int,
) -> list[str]:
    """Return the longest prefix of ``record_ids`` within ``max_words``.

    The first record is always included when ``record_ids`` is non-empty so a
    single long record still makes progress.
    """
    if max_words < 1:
        raise ValueError("max_words must be >= 1")
    selected: list[str] = []
    total = 0
    for record_id in record_ids:
        words = source_by_id[record_id].source_words
        if selected and total + words > max_words:
            break
        selected.append(record_id)
        total += words
    return selected


def select_translation_record_ids(
    bundle: StatusBundle,
    chapter: ChapterProgress,
    *,
    unit: str,
    max_words: int,
) -> tuple[str, list[str]]:
    """Select the record ids for the next translation task within ``chapter``."""
    source_by_id = bundle.index.source_by_id
    pending = [
        record_id
        for record_id in bundle.index.record_ids_by_chapter[chapter.chapter_id]
        if record_id not in bundle.index.translated_by_id
    ]
    if not pending:
        return (unit, [])
    if unit == "chapter":
        return (unit, pending)
    if unit == "chunk":
        first_chunk_id = source_by_id[pending[0]].chunk_id
        return (
            unit,
            [
                record_id
                for record_id in pending
                if source_by_id[record_id].chunk_id == first_chunk_id
            ],
        )
    if unit == "paragraph":
        first_record = source_by_id[pending[0]]
        if first_record.span_index is None:
            unit = "batch"
        else:
            same_span = [
                record_id
                for record_id in pending
                if source_by_id[record_id].span_index == first_record.span_index
            ]
            return (unit, limit_records_by_words(same_span, source_by_id, max_words))
    return (unit, limit_records_by_words(pending, source_by_id, max_words))


def create_translation_task(
    project: Project,
    bundle: StatusBundle,
    chapter: ChapterProgress,
    *,
    mode: RuntimeMode | None = None,
    unit: str,
    record_ids: list[str],
    requested_max_words: int | None = None,
    todo_id: str | None = None,
) -> TranslationTask:
    """Build, persist, and render durable files for one translation task."""
    from booktx.config import write_translation_task

    source_by_id = bundle.index.source_by_id
    translation_version = None
    baseline_ref = None
    baseline_sha = None
    context_sha256 = None
    context_view_sha256 = None
    context_view_path = None
    context_notes_scope = None
    context_target_chapter_id = None
    context_notes_through_chapter_id = None
    source_sha256 = bundle.snapshot.source.source_sha256 or None
    if bundle.snapshot.context.exists and bundle.snapshot.context.ready:
        resolution = resolve_current_version(project)
        context_view = ensure_context_view_snapshot(
            project,
            baseline_ref=resolution.version_ref,
            baseline_sha256=resolution.baseline_sha256,
            target_chapter_id=chapter.chapter_id,
        )
        translation_version = resolution.version_ref
        baseline_ref = resolution.version_ref
        baseline_sha = resolution.baseline_sha256
        context_sha256 = context_view.context_view_sha256
        context_view_sha256 = context_view.context_view_sha256
        context_view_path = context_view.context_path
        context_notes_scope = context_view.notes_scope
        context_target_chapter_id = context_view.target_chapter_id
        context_notes_through_chapter_id = context_view.notes_through_chapter_id
    else:
        translation_version = load_translation_version_ledger(project).active_version
    record_sources = {
        record_id: source_by_id[record_id].source for record_id in record_ids
    }
    live_context = load_context(project)
    glossary = list(live_context.glossary) if live_context is not None else []
    applicable_termbase, applicable_termbase_sha256 = (
        collect_applicable_termbase_for_record_sources(project, record_sources)
    )
    mandatory_glossary_fingerprint = live_mandatory_glossary_sha256(project)
    task = TranslationTask(
        task_id=make_task_id(chapter.chapter_id, record_ids[0], record_ids),
        unit=unit,  # type: ignore[arg-type]
        chapter_id=chapter.chapter_id,
        chapter_title=chapter.title,
        profile=project.profile or "",
        source_language=project.config.source_language,
        target_language=project.config.target_language,
        target_locale=project.config.target_locale or project.config.target_language,
        translation_version=translation_version,
        baseline_ref=baseline_ref,
        baseline_sha256=baseline_sha,
        context_sha256=context_sha256,
        context_view_sha256=context_view_sha256,
        context_view_path=context_view_path,
        applicable_termbase_sha256=applicable_termbase_sha256,
        mandatory_glossary_sha256=mandatory_glossary_fingerprint,
        context_notes_scope=context_notes_scope,
        context_target_chapter_id=context_target_chapter_id,
        context_notes_through_chapter_id=context_notes_through_chapter_id,
        source_sha256=source_sha256,
        profile_config_sha256=(
            canonical_json_sha256(project.profile_config.model_dump(mode="json"))
            if project.profile_config is not None
            else None
        ),
        source_config_sha256=canonical_json_sha256(
            project.source_config.model_dump(mode="json")
        ),
        source_words=sum(
            source_by_id[record_id].source_words for record_id in record_ids
        ),
        record_count=len(record_ids),
        requested_max_words=requested_max_words,
        todo_id=todo_id,
        records=[
            TranslationTaskRecord(
                id=record_id,
                chunk_id=source_by_id[record_id].chunk_id,
                source=source_by_id[record_id].source,
                protected_terms=list(source_by_id[record_id].protected_terms),
                placeholders=list(source_by_id[record_id].placeholders),
                applicable_glossary=applicable_glossary_snapshots(
                    source_by_id[record_id].source, glossary
                ),
                applicable_termbase=applicable_termbase.get(record_id, []),
                span_index=source_by_id[record_id].span_index,
                block_id=source_by_id[record_id].block_id,
            )
            for record_id in record_ids
        ],
    )
    write_translation_task(project, task)
    write_ingest_template(project, task)
    write_task_source_block(project, task, mode=mode)
    write_task_agent_brief(project, task, mode=mode)
    write_block_ingest_template(project, task, mode=mode)
    return task
