"""Audit and safe migration helpers for EPUB inline XHTML targets."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from booktx.config import Project, _err, translation_store_path
from booktx.epub_inline_xhtml import (
    INLINE_XHTML_CODEC,
    inline_skeleton,
    sanitize_target_fragment,
    strip_inline_xhtml,
)
from booktx.errors import BooktxError
from booktx.io_utils import write_json_model_atomic, write_text_atomic
from booktx.models import Chunk, Record, TranslatedChunk, TranslatedRecord
from booktx.progress import load_source_chunks
from booktx.store import open_translation_store
from booktx.translation_store import (
    EffectiveCandidateError,
    effective_candidate_selection,
    find_candidate,
    find_review_candidate,
    sha256_text,
)
from booktx.validate import (
    _resolve_validation_scope,
    load_effective_translated_chunks,
    strict_load_translated,
    validate_record_pair,
)


@dataclass(slots=True)
class InlineAuditResult:
    records_with_inline_source: int = 0
    valid_active_targets: int = 0
    missing_inline_tags: int = 0
    invalid_xhtml_targets: int = 0
    opaque_changed: int = 0
    needs_review: int = 0
    findings: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "records_with_inline_source": self.records_with_inline_source,
            "valid_active_targets": self.valid_active_targets,
            "missing_inline_tags": self.missing_inline_tags,
            "invalid_xhtml_targets": self.invalid_xhtml_targets,
            "opaque_changed": self.opaque_changed,
            "needs_review": self.needs_review,
            "findings": self.findings,
        }


def _collect_inline_record_ids(
    project: Project,
    chunks: list[Chunk],
    effective_chunks: dict[str, TranslatedChunk],
    resolved_chapter: str | None,
    resolved_record_ids: set[str] | None,
) -> tuple[dict[str, str], set[str]]:
    """Collect (record_id -> chunk_id, all_inline_ids) using manifest + markup.

    Uses the EPUB span manifest as the primary authority so old projects
    without record-level source_markup are still audited.
    """
    from booktx.epub_preflight import assemble_epub_replacements

    inline_record_ids: set[str] = set()
    inline_record_chunk: dict[str, str] = {}
    try:
        assembled = assemble_epub_replacements(
            project,
            source_chunks={c.chunk_id: c for c in chunks},
            effective_chunks=effective_chunks,
        )
    except Exception:  # noqa: BLE001 - audit must not crash on a bad manifest
        assembled = []
    for span in assembled:
        if span.span_ref.source_markup != INLINE_XHTML_CODEC:
            continue
        if resolved_chapter and span.chapter_id != resolved_chapter:
            continue
        for record in span.records:
            if resolved_record_ids and record.id not in resolved_record_ids:
                continue
            inline_record_ids.add(record.id)
            inline_record_chunk[record.id] = record.id.split("-", 1)[0]
    # Defense-in-depth: also pick up records with explicit source_markup.
    for chunk in chunks:
        for source in chunk.records:
            if source.source_markup == INLINE_XHTML_CODEC:
                if resolved_chapter:
                    continue
                if resolved_record_ids and source.id not in resolved_record_ids:
                    continue
                inline_record_ids.add(source.id)
                inline_record_chunk.setdefault(source.id, chunk.chunk_id)
    return inline_record_chunk, inline_record_ids


def audit_inline_xhtml(
    project: Project,
    *,
    chapter_id: str | None = None,
    task_id: str | None = None,
) -> InlineAuditResult:
    """Audit active translations for required EPUB inline XHTML semantics.

    Uses the EPUB span manifest (the authority) and/or propagated
    ``Record.source_markup`` to identify inline-source records, so old
    projects without record-level markup are still audited.
    """

    from booktx.epub_preflight import validate_epub_inline_preflight

    chunks = load_source_chunks(project)
    effective = load_effective_translated_chunks(project)
    targets = {
        record.id: record
        for chunk in effective.chunks.values()
        for record in chunk.records
    }
    resolved_chapter, resolved_record_ids = _resolve_validation_scope(
        project, chapter_id=chapter_id, record_ids=None, task_id=task_id
    )
    inline_record_chunk, inline_record_ids = _collect_inline_record_ids(
        project, chunks, effective.chunks, resolved_chapter, resolved_record_ids
    )
    result = InlineAuditResult()
    result.records_with_inline_source = len(inline_record_ids)

    # Use the span-level preflight for build-grade findings.
    preflight_findings = validate_epub_inline_preflight(
        project,
        chapter_id=resolved_chapter,
        record_ids=resolved_record_ids,
        source_chunks={c.chunk_id: c for c in chunks},
        effective_chunks=effective.chunks,
    )
    for pf in preflight_findings:
        rules = {pf.rule}
        if "inline_xhtml_preserved" in rules:
            result.missing_inline_tags += 1
        if "inline_xhtml_parseable" in rules:
            result.invalid_xhtml_targets += 1
        if "inline_xhtml_opaque_preserved" in rules:
            result.opaque_changed += 1
        result.needs_review += 1
        result.findings.append(
            {"record_id": pf.record_id, "rule": pf.rule, "message": pf.message}
        )
    # Per-record validation for record-level findings (existing markup).
    _audit_inline_records(
        result, inline_record_ids, inline_record_chunk, chunks, targets
    )
    return result


def _audit_inline_records(
    result: InlineAuditResult,
    inline_record_ids: set[str],
    inline_record_chunk: dict[str, str],
    chunks: list[Chunk],
    targets: dict[str, TranslatedRecord],
) -> None:
    """Validate each inline-source record and update the audit result."""
    source_by_id: dict[str, Record] = {}
    for chunk in chunks:
        for rec in chunk.records:
            source_by_id[rec.id] = rec
    for record_id in sorted(inline_record_ids):
        source_rec = source_by_id.get(record_id)
        if source_rec is None:
            continue
        target = targets.get(record_id)
        if target is None:
            result.needs_review += 1
            result.findings.append({"record_id": record_id, "rule": "missing_target"})
            continue
        chunk_id = inline_record_chunk.get(record_id, record_id.split("-", 1)[0])
        findings = validate_record_pair(source_rec, target, chunk_id)
        errors = [f for f in findings if f.severity == "error"]
        if not errors:
            result.valid_active_targets += 1
            continue
        result.needs_review += 1
        rules = {f.rule for f in errors}
        if "inline_xhtml_preserved" in rules:
            result.missing_inline_tags += 1
        if "inline_xhtml_parseable" in rules:
            result.invalid_xhtml_targets += 1
        if "inline_xhtml_opaque_preserved" in rules:
            result.opaque_changed += 1
        for f in errors:
            result.findings.append(
                {"record_id": record_id, "rule": f.rule, "message": f.message}
            )


def _single_full_wrapper(source: str) -> tuple[str, tuple[tuple[str, str], ...]] | None:
    skeleton = inline_skeleton(source)
    if len(skeleton) != 2 or skeleton[0].kind != "start" or skeleton[1].kind != "end":
        return None
    if skeleton[0].tag != skeleton[1].tag:
        return None
    stripped = source.strip()
    if not stripped.startswith(f"<{skeleton[0].tag}") or not stripped.endswith(
        f"</{skeleton[0].tag}>"
    ):
        return None
    return skeleton[0].tag, skeleton[0].attrs


def _format_attrs(attrs: tuple[tuple[str, str], ...]) -> str:
    if not attrs:
        return ""
    return "".join(f' {name}="{value}"' for name, value in attrs)


def safe_migrated_target(source: str, target: str) -> str | None:
    wrapper = _single_full_wrapper(source)
    if wrapper is not None and "<" not in target and ">" not in target:
        tag, attrs = wrapper
        migrated = f"<{tag}{_format_attrs(attrs)}>{target}</{tag}>"
        if not [
            issue
            for issue in sanitize_target_fragment(migrated, source).issues
            if issue.severity == "error"
        ]:
            return migrated
    skeleton = inline_skeleton(source)
    if len(skeleton) == 2 and skeleton[0].kind == "start" and skeleton[1].kind == "end":
        tag, attrs = skeleton[0].tag, skeleton[0].attrs
        start = source.find(f"<{tag}")
        if start >= 0:
            start = source.find(">", start) + 1
            end = source.find(f"</{tag}>", start)
            phrase = (
                strip_inline_xhtml(source[start:end]).strip() if end >= start else ""
            )
            if phrase and target.count(phrase) == 1:
                migrated = target.replace(
                    phrase, f"<{tag}{_format_attrs(attrs)}>{phrase}</{tag}>"
                )
                if not [
                    issue
                    for issue in sanitize_target_fragment(migrated, source).issues
                    if issue.severity == "error"
                ]:
                    return migrated
    return None


def _dependent_review_refs(
    record_id: str,
    stored: Any,
    *,
    base_kind: str,
    base_ref: str,
) -> list[str]:
    refs = sorted(
        review.review_ref
        for review in stored.reviews
        if review.base_kind == base_kind and review.base_ref == base_ref
    )
    if base_kind not in {"translation", "review"}:
        raise _err(
            "inline_xhtml_migration_invalid_selection",
            f"record {record_id} has unsupported candidate kind {base_kind!r}",
        )
    return refs


def _plan_canonical_migrations(
    project: Project,
    pending: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    try:
        repo = open_translation_store(project)
    except BooktxError as exc:
        if exc.code != "translation_store_missing":
            raise
        return [], [
            {
                "record_id": item["record_id"],
                "chunk_id": item["chunk_id"],
                "old_target": item["old_target"],
                "proposed_target": item["new_target"],
                "reason": "canonical_translation_store_missing",
            }
            for item in pending
        ]

    mapped: list[dict[str, str]] = []
    review: list[dict[str, str]] = []
    for item in pending:
        record_id = item["record_id"]
        stored = repo.get_record(record_id)
        if stored is None:
            review.append(
                {
                    "record_id": record_id,
                    "chunk_id": item["chunk_id"],
                    "old_target": item["old_target"],
                    "proposed_target": item["new_target"],
                    "reason": "canonical_store_record_missing",
                }
            )
            continue
        selection = effective_candidate_selection(stored, strict_active_review=True)
        if selection is None:
            review.append(
                {
                    "record_id": record_id,
                    "chunk_id": item["chunk_id"],
                    "old_target": item["old_target"],
                    "proposed_target": item["new_target"],
                    "reason": "no_effective_canonical_candidate",
                }
            )
            continue
        if isinstance(selection, EffectiveCandidateError):
            review.append(
                {
                    "record_id": record_id,
                    "chunk_id": item["chunk_id"],
                    "old_target": item["old_target"],
                    "proposed_target": item["new_target"],
                    "reason": selection.rule,
                    "message": selection.message,
                }
            )
            continue
        if selection.candidate.target != item["old_target"]:
            review.append(
                {
                    "record_id": record_id,
                    "chunk_id": item["chunk_id"],
                    "old_target": item["old_target"],
                    "proposed_target": item["new_target"],
                    "reason": "effective_output_not_canonical",
                    "candidate_kind": selection.selected_kind,
                    "candidate_ref": selection.selected_ref,
                }
            )
            continue
        dependent_refs = _dependent_review_refs(
            record_id,
            stored,
            base_kind=selection.selected_kind,
            base_ref=selection.selected_ref,
        )
        if dependent_refs:
            review.append(
                {
                    "record_id": record_id,
                    "chunk_id": item["chunk_id"],
                    "old_target": item["old_target"],
                    "proposed_target": item["new_target"],
                    "reason": "dependent_reviews_would_drift",
                    "candidate_kind": selection.selected_kind,
                    "candidate_ref": selection.selected_ref,
                    "dependent_review_refs": ", ".join(dependent_refs),
                }
            )
            continue
        mapped.append(
            {
                **item,
                "candidate_kind": selection.selected_kind,
                "candidate_ref": selection.selected_ref,
            }
        )
    return mapped, review


def _apply_canonical_migrations(
    project: Project,
    *,
    chunks: list[Chunk],
    mapped: list[dict[str, str]],
    translated_by_chunk: dict[str, TranslatedChunk],
    updated_at: str,
    timestamp: str,
    reports_dir: Any,
) -> bool:
    if not mapped:
        return False
    store_path = translation_store_path(project)
    if project.profile_dir is not None and store_path.is_file():
        backup = reports_dir / f"translation-store.before-inline-xhtml-{timestamp}.json"
        backup.write_text(store_path.read_text("utf-8"), "utf-8")

    repo = open_translation_store(project)
    planned = {item["record_id"]: item for item in mapped}

    def _mutate(store: Any) -> None:
        for record_id, item in planned.items():
            stored = store.records.get(record_id)
            if stored is None:
                raise _err(
                    "inline_xhtml_migration_conflict",
                    "record "
                    f"{record_id} disappeared before the canonical migration ran",
                )
            selection = effective_candidate_selection(stored, strict_active_review=True)
            if selection is None or isinstance(selection, EffectiveCandidateError):
                raise _err(
                    "inline_xhtml_migration_conflict",
                    "record "
                    f"{record_id} no longer has a writable effective canonical "
                    "candidate",
                )
            if (
                selection.selected_kind != item["candidate_kind"]
                or selection.selected_ref != item["candidate_ref"]
                or selection.candidate.target != item["old_target"]
            ):
                raise _err(
                    "inline_xhtml_migration_conflict",
                    f"record {record_id} changed before the canonical migration ran",
                )
            if selection.selected_kind == "translation":
                candidate = find_candidate(stored, selection.selected_ref)
                if candidate is None:
                    raise _err(
                        "inline_xhtml_migration_conflict",
                        "record "
                        f"{record_id} lost translation candidate "
                        f"{selection.selected_ref}",
                    )
                candidate.target = item["new_target"]
                candidate.updated_at = updated_at
            else:
                review = find_review_candidate(stored, selection.selected_ref)
                if review is None:
                    raise _err(
                        "inline_xhtml_migration_conflict",
                        "record "
                        f"{record_id} lost review candidate {selection.selected_ref}",
                    )
                review.target = item["new_target"]
                review.target_sha256 = sha256_text(item["new_target"])
                review.updated_at = updated_at

    repo.edit_records(
        sorted(planned),
        _mutate,
        summary="migrate inline xhtml targets",
    )

    by_chunk = {chunk.chunk_id: chunk for chunk in chunks}
    changed_chunks = sorted({item["chunk_id"] for item in mapped})
    if project.translated_dir is not None:
        project.translated_dir.mkdir(parents=True, exist_ok=True)
        for chunk_id in changed_chunks:
            source_chunk = by_chunk.get(chunk_id)
            translated = (
                _materialize_translated_chunk(
                    source_chunk=source_chunk,
                    repo=repo,
                    legacy_chunk=translated_by_chunk.get(chunk_id),
                )
                if source_chunk is not None
                else None
            )
            if translated is not None:
                write_json_model_atomic(
                    project.translated_dir / f"{chunk_id}.json",
                    translated,
                )
    return True


def _materialize_translated_chunk(
    *,
    source_chunk: Chunk,
    repo: Any,
    legacy_chunk: TranslatedChunk | None,
) -> TranslatedChunk | None:
    legacy_by_id = (
        {record.id: record for record in legacy_chunk.records}
        if legacy_chunk is not None
        else {}
    )
    records: list[TranslatedRecord] = []
    for source in source_chunk.records:
        target_text: str | None = None
        stored = repo.get_record(source.id)
        if stored is not None:
            selection = effective_candidate_selection(stored, strict_active_review=True)
            if selection is not None and not isinstance(
                selection, EffectiveCandidateError
            ):
                target_text = selection.candidate.target
        if target_text is None:
            legacy = legacy_by_id.get(source.id)
            if legacy is not None:
                target_text = legacy.target
        if target_text is not None:
            records.append(TranslatedRecord(id=source.id, target=target_text))
    if not records:
        return None
    return TranslatedChunk(chunk_id=source_chunk.chunk_id, records=records)


def migrate_inline_xhtml(
    project: Project, *, write_safe: bool = False
) -> dict[str, Any]:
    chunks = load_source_chunks(project)
    translated_by_chunk: dict[str, TranslatedChunk] = {}
    for path in project.translated():
        translated, _err = strict_load_translated(path)
        if translated is not None:
            translated_by_chunk[path.stem] = translated.model_copy(deep=True)
    try:
        repo = open_translation_store(project)
    except BooktxError as exc:
        if exc.code != "translation_store_missing":
            raise
        repo = None
    pending: list[dict[str, str]] = []
    review: list[dict[str, str]] = []
    for chunk in chunks:
        translated = translated_by_chunk.get(chunk.chunk_id)
        by_id = (
            {record.id: record for record in translated.records}
            if translated is not None
            else {}
        )
        for source in chunk.records:
            if source.source_markup != INLINE_XHTML_CODEC:
                continue
            target_text: str | None = None
            if repo is not None:
                stored = repo.get_record(source.id)
                if stored is not None:
                    selection = effective_candidate_selection(
                        stored, strict_active_review=True
                    )
                    if selection is not None and not isinstance(
                        selection, EffectiveCandidateError
                    ):
                        target_text = selection.candidate.target
            if target_text is None:
                target = by_id.get(source.id)
                if target is not None:
                    target_text = target.target
            if target_text is None:
                continue
            if not [
                issue
                for issue in sanitize_target_fragment(target_text, source.source).issues
                if issue.severity == "error"
            ]:
                continue
            migrated = safe_migrated_target(source.source, target_text)
            if migrated is None:
                review.append(
                    {
                        "record_id": source.id,
                        "chunk_id": chunk.chunk_id,
                        "old_target": target_text,
                        "reason": "unsafe_or_ambiguous",
                    }
                )
                continue
            pending.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "record_id": source.id,
                    "old_target": target_text,
                    "new_target": migrated,
                }
            )
    mapped, canonical_review = _plan_canonical_migrations(project, pending)
    review.extend(canonical_review)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    updated_at = now.isoformat().replace("+00:00", "Z")
    report = {
        "timestamp": timestamp,
        "mapped_records": mapped,
        "targets_requiring_review": review,
        "written": False,
    }
    reports_dir = (
        project.profile_dir / "reports"
        if project.profile_dir is not None
        else project.booktx_dir / "reports"
    )
    reports_dir.mkdir(parents=True, exist_ok=True)
    write_text_atomic(
        reports_dir / f"inline-xhtml-migration-{timestamp}.json",
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
    )
    if write_safe:
        report["written"] = _apply_canonical_migrations(
            project,
            chunks=chunks,
            mapped=mapped,
            translated_by_chunk=translated_by_chunk,
            updated_at=updated_at,
            timestamp=timestamp,
            reports_dir=reports_dir,
        )
        write_text_atomic(
            reports_dir / f"inline-xhtml-migration-{timestamp}.json",
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        )
    return report
