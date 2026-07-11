"""Judge-task creation and durable artifact rendering."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import blake2s
from typing import TYPE_CHECKING, Any, Literal

from booktx.config import (
    JUDGE_SOURCES_SNAPSHOT_MANIFEST_REL,
    current_source_sha256,
    judge_ingest_block_path,
    judge_ingest_decisions_path,
    judge_ingest_json_path,
    judge_task_source_block_path,
    write_judge_task,
)
from booktx.context import GlossaryEntry, ensure_context_view_snapshot, load_context
from booktx.glossary_match import (
    applicable_entry_indexes,
    source_glossary_matches,
)
from booktx.io_utils import write_text_atomic
from booktx.judge_sources import (
    judge_sources_manifest_sha256,
    judge_task_candidates_sha256,
    load_live_judge_source_views,
    load_snapshot_judge_source_views,
    validate_judge_sources_snapshot,
)
from booktx.judge_store import (
    collect_source_candidates,
    require_selection_profile,
    selected_record_ids,
)
from booktx.models import (
    ApplicableGlossaryEntrySnapshot,
    JudgeTask,
    JudgeTaskRecord,
)
from booktx.progress import count_words
from booktx.record_refs import parse_record_ref
from booktx.selection_mode import revision_focus, selection_purpose
from booktx.status import selected_chapter
from booktx.tasks import limit_records_by_words
from booktx.termbase_tasking import collect_applicable_termbase_for_record_sources
from booktx.validate import load_validation_context
from booktx.versioning import canonical_json_sha256, resolve_current_version

if TYPE_CHECKING:
    from booktx.config import Project
    from booktx.status import StatusBundle

__all__ = [
    "make_judge_task_id",
    "create_judge_task",
    "render_judge_ingest",
    "render_judge_decision_block",
    "render_judge_task_block",
    "render_judge_ingest_json",
]


def make_judge_task_id(
    chapter_id: str, first_record_id: str, record_ids: list[str]
) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = blake2s("|".join(record_ids).encode("utf-8"), digest_size=4).hexdigest()
    return f"bt-judge-{stamp}-{chapter_id}-{first_record_id.replace('-', '')}-{digest}"


def _record_ids_for_task(
    *,
    project: Project,
    bundle: StatusBundle,
    chapter_id: str | None,
    record_id: str | None,
    max_words: int,
    max_records: int | None,
) -> tuple[str, list[str]]:
    if record_id is not None:
        canonical = parse_record_ref(record_id).canonical_id
        chapter = bundle.index.record_to_chapter.get(canonical)
        if chapter is None:
            raise ValueError(f"unknown source record id: {canonical}")
        return chapter, [canonical]

    chapter_obj = selected_chapter(bundle, chapter_id)
    if chapter_obj is None:
        raise ValueError("no eligible chapter found")
    selected_ids = [
        rid
        for rid in bundle.index.record_ids_by_chapter.get(chapter_obj.chapter_id, [])
        if rid not in selected_record_ids(project)
    ]
    limited = limit_records_by_words(selected_ids, bundle.index.source_by_id, max_words)
    if max_records is not None and max_records > 0:
        limited = limited[:max_records]
    return chapter_obj.chapter_id, limited


def _is_grammar_revision_task(task: JudgeTask) -> bool:
    return task.selection_purpose == "revise" and task.revision_focus == "grammar"


def _judge_task_block_header(task: JudgeTask, *, revise: bool) -> list[str]:
    if revise:
        source_name = task.source_profiles[0] if task.source_profiles else ""
        if _is_grammar_revision_task(task):
            lines = [
                "# booktx judge revision task",
                f"judge_task_id: {task.judge_task_id}",
                f"profile: {task.profile}",
                f"source: {source_name}",
                "purpose: revise",
                "revision_focus: grammar",
                (
                    "# Goal: inspect every BASE_TARGET record and correct German "
                    "grammar only."
                ),
                "# BASE_TARGET is authoritative for wording and terminology.",
                "# SOURCE is a semantic guard; do not retranslate from it.",
                "# Use copy whenever BASE_TARGET is grammatically correct.",
                (
                    "# Use edited only for the smallest necessary grammar, "
                    "syntax, agreement,"
                ),
                (
                    "# inflection, orthography, capitalization, or punctuation "
                    "correction."
                ),
                "# Do not change vocabulary, terminology, style, flow, tone, register,",
                (
                    "# meaning, sentence boundaries, names, placeholders, quotes, "
                    "or inline XHTML."
                ),
            ]
        else:
            lines = [
                "# booktx judge revision task",
                f"judge_task_id: {task.judge_task_id}",
                f"profile: {task.profile}",
                f"source: {source_name}",
                "purpose: revise",
                "revision_focus: general",
                "# Goal: proofread and improve the existing target record by record.",
                "# Use copy only when the base target is already good.",
                "# Use edited for grammar, flow, punctuation, style, or terminology "
                "corrections.",
                "# Preserve meaning, names, policy, placeholders, quotes, ",
                "and inline XHTML.",
            ]
    else:
        lines = [
            "# booktx judge task",
            f"judge_task_id: {task.judge_task_id}",
            f"profile: {task.profile}",
            f"sources: {','.join(task.source_profiles)}",
        ]
    if task.source_access == "snapshot":
        lines.append(f"source_access: {task.source_access}")
        lines.append(
            "source_snapshot: "
            + (task.source_snapshot_path or JUDGE_SOURCES_SNAPSHOT_MANIFEST_REL)
        )
    lines.append(f"applicable_termbase_sha256: {task.applicable_termbase_sha256 or ''}")
    lines.append("")
    return lines


def _judge_glossary_lines(record: JudgeTaskRecord) -> list[str]:
    if not record.applicable_glossary:
        return []
    lines = ["GLOSSARY:"]
    for entry in record.applicable_glossary:
        approved = ([entry.target] if entry.target else []) + list(
            entry.target_variants
        )
        lines.append(f"- source: {entry.source} matched: {entry.matched_source_cue}")
        if entry.require_target and approved:
            lines.append(f"  required: {', '.join(approved)}")
        if entry.forbidden_targets:
            lines.append(f"  forbidden: {', '.join(entry.forbidden_targets)}")
        lines.append(f"  enforce: {entry.enforce}")
        if entry.notes:
            lines.append(f"  note: {entry.notes}")
    lines.append("")
    return lines


def _judge_termbase_lines(record: JudgeTaskRecord) -> list[str]:
    if not record.applicable_termbase:
        return []
    lines: list[str] = []
    for snapshot in record.applicable_termbase:
        note = snapshot.sense or snapshot.rationale
        lines.append(f"TERMBASE: {snapshot.entry_id} — {note}".rstrip(" —"))
    lines.append("")
    return lines


def _judge_candidate_lines(record: JudgeTaskRecord, *, revise: bool) -> list[str]:
    lines: list[str] = []
    if revise:
        # Revise profiles render the single base target as BASE_TARGET, not the
        # multi-candidate CANDIDATES block.
        for candidate in record.candidates:
            lines.append(
                f"BASE_TARGET [{candidate.label}] profile={candidate.profile} "
                f"ref={candidate.selected_ref} sha256={candidate.target_sha256}"
            )
            lines.append(candidate.target)
            if candidate.validation_findings:
                lines.append("validation:")
                for finding in candidate.validation_findings:
                    lines.append(
                        f"- {finding.severity} {finding.rule}: {finding.message}"
                    )
            lines.append("")
        return lines
    lines.extend(["CANDIDATES:", ""])
    for candidate in record.candidates:
        lines.append(
            f"[{candidate.label}] profile={candidate.profile} "
            f"ref={candidate.selected_ref} sha256={candidate.target_sha256}"
        )
        lines.append(candidate.target)
        if candidate.validation_findings:
            lines.append("validation:")
            for finding in candidate.validation_findings:
                lines.append(f"- {finding.severity} {finding.rule}: {finding.message}")
        lines.append("")
    return lines


def _judge_decision_comment(*, revise: bool) -> list[str]:
    if revise:
        return [
            "# Decision: selected=A + copy + empty TARGET keeps the base target.",
            "# selected=A + edited + full TARGET revises the base target.",
            "# selected=edited + edited + full TARGET replaces the target entirely.",
            "",
        ]
    return [
        "# Decision modes:",
        "# - copy: selected must be A/B/C; TARGET must be empty.",
        "# - edited from candidate: selected is A/B/C;",
        "#   decision_kind is edited; TARGET is the corrected full target.",
        "# - new judge target: selected is edited;",
        "#   decision_kind is edited; TARGET is the full new target.",
        "# Never paste a copy candidate into TARGET.",
        "# Use TARGET only for edited/new targets.",
        "",
    ]


def _judge_decision_section() -> list[str]:
    return [
        "DECISION:",
        "selected: ",
        "decision_kind: copy",
        "reason: ",
        "",
        "TARGET:",
        "",
        "",
    ]


def render_judge_task_block(task: JudgeTask) -> str:
    revise = task.selection_purpose == "revise"
    lines = _judge_task_block_header(task, revise=revise)
    for record in task.records:
        lines.extend(
            [
                f"## {record.id}",
                "",
                "SOURCE:",
                record.source,
                "",
            ]
        )
        lines.extend(_judge_glossary_lines(record))
        lines.extend(_judge_termbase_lines(record))
        lines.extend(_judge_candidate_lines(record, revise=revise))
        if record.missing_profiles:
            lines.append("missing_profiles: " + ", ".join(record.missing_profiles))
            lines.append("")
        lines.extend(_judge_decision_comment(revise=revise))
        lines.extend(_judge_decision_section())
    return "\n".join(lines).rstrip() + "\n"


def render_judge_decision_block(task: JudgeTask) -> str:
    revise = task.selection_purpose == "revise"
    if revise:
        if _is_grammar_revision_task(task):
            lines = [
                "# booktx judge grammar revision decisions",
                f"judge_task_id: {task.judge_task_id}",
                "# Fill every record.",
                "# copy: keep BASE_TARGET unchanged and leave TARGET empty.",
                "# edited: write the complete minimally corrected target.",
                "# Do not rewrite grammatically valid text for style or fluency.",
                "",
            ]
        else:
            lines = [
                "# booktx judge revision decisions",
                f"judge_task_id: {task.judge_task_id}",
                "# Fill every record. Use copy only when no improvement is needed.",
                "# For edited, TARGET must contain the complete corrected target.",
                "",
            ]
    else:
        lines = [
            "# booktx judge decisions",
            f"judge_task_id: {task.judge_task_id}",
            "# Decision modes:",
            "# - copy: selected must be A/B/C; TARGET must be empty.",
            "# - edited from candidate: selected is A/B/C;",
            "#   decision_kind is edited; TARGET is the corrected full target.",
            "# - new judge target: selected is edited;"
            "#   decision_kind is edited; TARGET is the full new target.",
            "# Never paste a copy candidate into TARGET.",
            "# Use TARGET only for edited/new targets.",
            "",
        ]
    for record in task.records:
        lines.extend(
            [
                f"## {record.id}",
                "selected: ",
                "decision_kind: copy",
                "reason: ",
                "TARGET:",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def render_judge_ingest_json(task: JudgeTask) -> str:
    payload = {
        "judge_task_id": task.judge_task_id,
        "records": [
            {
                "id": record.id,
                "selected": "",
                "decision_kind": "copy",
                "target": "",
                "reason": "",
            }
            for record in task.records
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def render_judge_ingest(task: JudgeTask, output_format: str) -> str:
    if output_format == "block":
        return render_judge_task_block(task)
    if output_format == "decisions":
        return render_judge_decision_block(task)
    if output_format == "json":
        return render_judge_ingest_json(task)
    raise ValueError(f"unsupported judge ingest format: {output_format}")


def _line_count(text: str) -> int:
    return len(text.splitlines())


def applicable_glossary_snapshots(
    source: str, glossary: list[GlossaryEntry]
) -> list[ApplicableGlossaryEntrySnapshot]:
    """Binding glossary entries applicable to ``source``.

    Includes only non-shadowed entries with ``enforce != off`` and either a
    required approved target or a forbidden target, so the judge task shows the
    exact approved/forbidden policy next to the source record.
    """
    spans = source_glossary_matches(source, glossary)
    applicable = applicable_entry_indexes(source, glossary)
    matched_by_entry: dict[int, str] = {}
    for span in spans:
        if span.shadowed:
            continue
        matched_by_entry.setdefault(span.entry_index, span.matched_term)
    snapshots: list[ApplicableGlossaryEntrySnapshot] = []
    for idx, entry in enumerate(glossary):
        if idx not in applicable:
            continue
        if entry.enforce == "off":
            continue
        if not (entry.require_target or entry.forbidden_targets):
            continue
        snapshots.append(
            ApplicableGlossaryEntrySnapshot(
                source=entry.source,
                source_variants=list(entry.source_variants),
                matched_source_cue=matched_by_entry.get(idx, entry.source),
                target=entry.target,
                target_variants=list(entry.target_variants),
                require_target=entry.require_target,
                forbidden_targets=list(entry.forbidden_targets),
                enforce=entry.enforce,
                case_sensitive=entry.case_sensitive,
                notes=entry.notes,
            )
        )
    return snapshots


def _build_judge_task_model(
    *,
    project: Project,
    bundle: StatusBundle,
    task_chapter_id: str,
    resolution: Any,
    context_view: Any,
    source_profiles: list[str],
    source_access: Literal["live", "snapshot"],
    source_snapshot_sha256: str | None,
    source_snapshot_path: str | None,
    applicable_termbase_sha256: str | None,
    records: list[JudgeTaskRecord],
) -> JudgeTask:
    chapter = bundle.index.chapters_by_id[task_chapter_id]
    return JudgeTask(
        judge_task_id=make_judge_task_id(
            task_chapter_id, records[0].id, [record.id for record in records]
        ),
        profile=project.profile or "",
        source_profiles=list(source_profiles),
        source_language=project.config.source_language,
        target_language=project.config.target_language,
        target_locale=project.config.target_locale or project.config.target_language,
        chapter_id=task_chapter_id,
        chapter_title=chapter.title,
        created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        source_sha256=current_source_sha256(project),
        profile_config_sha256=(
            canonical_json_sha256(project.profile_config.model_dump(mode="json"))
            if project.profile_config is not None
            else None
        ),
        source_config_sha256=canonical_json_sha256(
            project.source_config.model_dump(mode="json")
        ),
        context_view_sha256=context_view.context_view_sha256,
        context_view_path=context_view.context_path,
        applicable_termbase_sha256=applicable_termbase_sha256,
        source_access=source_access,
        source_snapshot_sha256=source_snapshot_sha256,
        source_snapshot_path=source_snapshot_path,
        source_candidates_sha256=judge_task_candidates_sha256(records),
        selection_purpose=selection_purpose(project),
        revision_focus=revision_focus(project),
        records=records,
    )


def create_judge_task(
    project: Project,
    bundle: StatusBundle,
    *,
    source_profiles: list[str],
    chapter_id: str | None,
    record_id: str | None,
    max_words: int,
    max_records: int | None = None,
    max_rendered_lines: int | None = None,
    require_all_sources: bool,
    source_access: Literal["live", "snapshot"] = "live",
) -> JudgeTask:
    require_selection_profile(project)
    context_exists = load_context(project) is not None
    if not context_exists:
        raise ValueError("selection profile context is missing")

    task_chapter_id, selected_ids = _record_ids_for_task(
        project=project,
        bundle=bundle,
        chapter_id=chapter_id,
        record_id=record_id,
        max_words=max_words,
        max_records=max_records,
    )
    if not selected_ids:
        raise ValueError("no missing records remain for the requested chapter")

    resolution = resolve_current_version(project)
    context_view = ensure_context_view_snapshot(
        project,
        baseline_ref=resolution.version_ref,
        baseline_sha256=resolution.baseline_sha256,
        target_chapter_id=task_chapter_id,
    )
    if source_access == "live":
        source_views = load_live_judge_source_views(project, source_profiles)
        source_snapshot_sha256: str | None = None
        source_snapshot_path: str | None = None
    else:
        # Validate the full manifest against the configured source list first,
        # then load the requested (possibly subset) views from the snapshot.
        validate_judge_sources_snapshot(project)
        source_views = load_snapshot_judge_source_views(project, source_profiles)
        source_snapshot_sha256 = judge_sources_manifest_sha256(project)
        source_snapshot_path = JUDGE_SOURCES_SNAPSHOT_MANIFEST_REL
    validation_context = load_validation_context(
        project,
        context_view_path=context_view.context_path,
    )
    record_sources = {
        record_ref: bundle.index.source_by_id[record_ref].source
        for record_ref in selected_ids
    }
    applicable_termbase, applicable_termbase_sha256 = (
        collect_applicable_termbase_for_record_sources(project, record_sources)
    )

    records: list[JudgeTaskRecord] = []
    total_words = 0
    for record_ref in selected_ids:
        source_view = bundle.index.source_by_id[record_ref]
        source_chunk = bundle.index.source_chunks[source_view.chunk_id]
        source_record = next(
            item for item in source_chunk.records if item.id == record_ref
        )
        candidates, missing_profiles = collect_source_candidates(
            selection_project=project,
            selection_context=validation_context,
            source_views=source_views,
            source_record=source_record,
            chunk_id=source_view.chunk_id,
            termbase_snapshots=applicable_termbase.get(record_ref, []),
        )
        if require_all_sources and missing_profiles:
            raise ValueError(
                f"record {record_ref} is missing effective candidates for: "
                f"{', '.join(missing_profiles)}",
            )
        if not candidates:
            continue
        next_words = total_words + count_words(source_record.source)
        if records and next_words > max_words:
            break
        total_words = next_words
        records.append(
            JudgeTaskRecord(
                id=record_ref,
                chunk_id=source_view.chunk_id,
                source=source_record.source,
                source_sha256=source_view.source_sha256,
                applicable_termbase=applicable_termbase.get(record_ref, []),
                applicable_glossary=applicable_glossary_snapshots(
                    source_record.source,
                    validation_context.glossary if validation_context else [],
                ),
                candidates=candidates,
                missing_profiles=missing_profiles,
                output_version_ref=resolution.version_ref,
            )
        )
    if not records:
        raise ValueError("no judgeable records found for the requested scope")

    if max_rendered_lines is not None and max_rendered_lines > 0:
        trimmed = list(records)
        while len(trimmed) > 1:
            preview = _build_judge_task_model(
                project=project,
                bundle=bundle,
                task_chapter_id=task_chapter_id,
                resolution=resolution,
                context_view=context_view,
                source_profiles=source_profiles,
                source_access=source_access,
                source_snapshot_sha256=source_snapshot_sha256,
                source_snapshot_path=source_snapshot_path,
                applicable_termbase_sha256=applicable_termbase_sha256,
                records=trimmed,
            )
            if _line_count(render_judge_task_block(preview)) <= max_rendered_lines:
                break
            trimmed.pop()
        records = trimmed

    task = _build_judge_task_model(
        project=project,
        bundle=bundle,
        task_chapter_id=task_chapter_id,
        resolution=resolution,
        context_view=context_view,
        source_profiles=source_profiles,
        source_access=source_access,
        source_snapshot_sha256=source_snapshot_sha256,
        source_snapshot_path=source_snapshot_path,
        applicable_termbase_sha256=applicable_termbase_sha256,
        records=records,
    )
    write_judge_task(project, task)
    write_text_atomic(
        judge_task_source_block_path(project, task.judge_task_id),
        render_judge_task_block(task),
    )
    write_text_atomic(
        judge_ingest_block_path(project, task.judge_task_id),
        render_judge_task_block(task),
    )
    write_text_atomic(
        judge_ingest_decisions_path(project, task.judge_task_id),
        render_judge_decision_block(task),
    )
    write_text_atomic(
        judge_ingest_json_path(project, task.judge_task_id),
        render_judge_ingest_json(task),
    )
    return task
