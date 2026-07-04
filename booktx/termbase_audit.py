"""Source scanning and effective-target auditing for the translation termbase."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from booktx.config import Project, load_translation_store
from booktx.models import TranslationReviewCandidate
from booktx.termbase import (
    EffectiveTranslationTermbase,
    TermbaseEntry,
    effective_approved_entries,
)
from booktx.termbase_match import (
    TermbaseRuleEvaluation,
    evaluate_entry_policy,
    termbase_source_matches,
)
from booktx.translation_store import effective_target_candidate

__all__ = [
    "TermbaseMatch",
    "TermbaseSourceMatch",
    "TermbaseAuditResult",
    "TermbaseSourceScanResult",
    "audit_termbase",
    "scan_source_termbase",
]


class TermbaseSourceMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str
    record_id: str
    chapter_id: str
    source_text: str
    source_match: str
    source_span: tuple[int, int]
    shadowed: bool = False


class TermbaseMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str
    record_id: str
    chapter_id: str
    source_text: str
    target_text: str = ""
    source_match: str
    source_span: tuple[int, int]
    rule_id: str = ""
    context_id: str = ""
    shadowed: bool = False
    target_forbidden_found: list[str] = Field(default_factory=list)
    target_required_found: list[str] = Field(default_factory=list)
    target_allowed_found: list[str] = Field(default_factory=list)
    severity: Literal["info", "warn", "error"]
    reason: str
    status: Literal["forbidden_target", "preferred_missing", "clean"]
    rule_code: str
    effective_candidate_ref: str = ""
    prompt: str = ""
    fallback: bool = False


class TermbaseSourceScanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records_scanned: int = 0
    matched_records: int = 0
    matches: list[TermbaseSourceMatch] = Field(default_factory=list)


class TermbaseAuditResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records_scanned: int = 0
    source_matched_records: int = 0
    audited_records: int = 0
    clean_records: int = 0
    finding_count: int = 0
    matches: list[TermbaseMatch] = Field(default_factory=list)


def _relevant_entries(
    project: Project,
    effective: EffectiveTranslationTermbase,
    *,
    entry_ids: set[str] | None = None,
) -> list[TermbaseEntry]:
    source_language = project.config.source_language
    entries = effective_approved_entries(effective, source_language=source_language)
    target_locale = project.config.target_locale or project.config.target_language
    filtered = [
        entry
        for entry in entries
        if entry.target_language == project.config.target_language
        and entry.target_locale in {"", target_locale}
        and (entry_ids is None or entry.id in entry_ids)
    ]
    return filtered


def _chapter_ids(bundle, chapter_id: str | None) -> list[str]:
    return (
        [chapter_id]
        if chapter_id is not None
        else list(bundle.index.record_ids_by_chapter)
    )


def scan_source_termbase(
    project: Project,
    bundle,
    effective: EffectiveTranslationTermbase,
    *,
    chapter_id: str | None = None,
    entry_ids: set[str] | None = None,
) -> TermbaseSourceScanResult:
    """Scan source records for applicable termbase matches without targets."""
    entries = _relevant_entries(project, effective, entry_ids=entry_ids)
    result = TermbaseSourceScanResult()
    for cid in _chapter_ids(bundle, chapter_id):
        for record_id in bundle.index.record_ids_by_chapter.get(cid, []):
            source_view = bundle.index.source_by_id[record_id]
            result.records_scanned += 1
            matches = termbase_source_matches(source_view.source, entries)
            non_shadowed = [match for match in matches if not match.shadowed]
            if non_shadowed:
                result.matched_records += 1
            result.matches.extend(
                TermbaseSourceMatch(
                    entry_id=match.entry_id,
                    record_id=record_id,
                    chapter_id=cid,
                    source_text=source_view.source,
                    source_match=match.source_match,
                    source_span=match.source_span,
                    shadowed=match.shadowed,
                )
                for match in matches
            )
    return result


def _effective_ref(candidate: object) -> str:
    if isinstance(candidate, TranslationReviewCandidate):
        return candidate.review_ref
    return getattr(candidate, "version_ref", "")


def _materialize_match(
    evaluation: TermbaseRuleEvaluation,
    *,
    record_id: str,
    chapter_id: str,
    source_text: str,
    target_text: str,
    effective_ref: str,
) -> TermbaseMatch:
    return TermbaseMatch(
        entry_id=evaluation.entry_id,
        record_id=record_id,
        chapter_id=chapter_id,
        source_text=source_text,
        target_text=target_text,
        source_match=evaluation.source_match,
        source_span=evaluation.source_span,
        rule_id=evaluation.rule_id,
        context_id=evaluation.context_id,
        target_forbidden_found=evaluation.forbidden_target_found,
        target_required_found=evaluation.required_target_found,
        target_allowed_found=evaluation.allowed_target_found,
        severity=evaluation.severity,  # type: ignore[arg-type]
        reason=evaluation.reason,
        status=evaluation.status,  # type: ignore[arg-type]
        rule_code=f"termbase.{evaluation.status}",
        effective_candidate_ref=effective_ref,
        prompt=evaluation.prompt,
        fallback=evaluation.fallback,
    )


def audit_termbase(
    project: Project,
    bundle,
    effective: EffectiveTranslationTermbase,
    *,
    chapter_id: str | None = None,
    entry_ids: set[str] | None = None,
) -> TermbaseAuditResult:
    """Audit effective targets for records whose source matches the termbase."""
    entries = _relevant_entries(project, effective, entry_ids=entry_ids)
    entry_by_id = {entry.id: entry for entry in entries}
    store = load_translation_store(project)
    result = TermbaseAuditResult()
    clean_records: set[str] = set()
    violation_records: set[str] = set()
    source_matched_records: set[str] = set()
    audited_records: set[str] = set()
    for cid in _chapter_ids(bundle, chapter_id):
        for record_id in bundle.index.record_ids_by_chapter.get(cid, []):
            source_view = bundle.index.source_by_id[record_id]
            result.records_scanned += 1
            matches = [
                match
                for match in termbase_source_matches(source_view.source, entries)
                if not match.shadowed
            ]
            if not matches:
                continue
            source_matched_records.add(record_id)
            stored = store.records.get(record_id)
            if stored is None:
                continue
            effective_candidate = effective_target_candidate(stored)
            if effective_candidate is None:
                continue
            audited_records.add(record_id)
            record_has_violation = False
            occurrence_index_by_entry: dict[str, int] = {}
            for match in matches:
                entry = entry_by_id[match.entry_id]
                occurrence_index = occurrence_index_by_entry.get(match.entry_id, 0)
                occurrence_index_by_entry[match.entry_id] = occurrence_index + 1
                evaluations = evaluate_entry_policy(
                    effective_candidate.target,
                    entry,
                    source_match=match.source_match,
                    source_span=match.source_span,
                    occurrence_index=occurrence_index,
                )
                for evaluation in evaluations:
                    finding = _materialize_match(
                        evaluation,
                        record_id=record_id,
                        chapter_id=cid,
                        source_text=source_view.source,
                        target_text=effective_candidate.target,
                        effective_ref=_effective_ref(effective_candidate),
                    )
                    if finding.status != "clean":
                        record_has_violation = True
                    result.matches.append(finding)
            if record_has_violation:
                violation_records.add(record_id)
            else:
                clean_records.add(record_id)
    result.source_matched_records = len(source_matched_records)
    result.audited_records = len(audited_records)
    result.clean_records = len(clean_records - violation_records)
    result.finding_count = len(
        [match for match in result.matches if match.status != "clean"]
    )
    return result
