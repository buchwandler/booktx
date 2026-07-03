"""Source scanning and effective-target auditing for the translation lexicon."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from booktx.config import Project, load_translation_store
from booktx.lexicon import (
    EffectiveTranslationLexicon,
    LexiconEntry,
    effective_approved_entries,
)
from booktx.lexicon_match import (
    LexiconSourceSpan,
    entry_preferred_absence,
    entry_target_forbidden_hits,
    lexicon_source_matches,
)
from booktx.models import TranslationReviewCandidate
from booktx.translation_store import effective_target_candidate

__all__ = [
    "LexiconMatch",
    "LexiconSourceMatch",
    "LexiconAuditResult",
    "LexiconSourceScanResult",
    "audit_lexicon",
    "scan_source_lexicon",
]


class LexiconSourceMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str
    record_id: str
    chapter_id: str
    source_text: str
    source_match: str
    source_span: tuple[int, int]
    shadowed: bool = False


class LexiconMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str
    record_id: str
    chapter_id: str
    source_text: str
    target_text: str = ""
    source_match: str
    source_span: tuple[int, int]
    shadowed: bool = False
    target_forbidden_found: list[str] = Field(default_factory=list)
    target_preferred_found: list[str] = Field(default_factory=list)
    severity: Literal["info", "warn", "error"]
    reason: str
    status: Literal["forbidden_target", "preferred_missing", "clean"]
    rule_code: str
    effective_candidate_ref: str = ""


class LexiconSourceScanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records_scanned: int = 0
    matched_records: int = 0
    matches: list[LexiconSourceMatch] = Field(default_factory=list)


class LexiconAuditResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records_scanned: int = 0
    source_matched_records: int = 0
    audited_records: int = 0
    clean_records: int = 0
    finding_count: int = 0
    matches: list[LexiconMatch] = Field(default_factory=list)


def _relevant_entries(
    project: Project,
    effective: EffectiveTranslationLexicon,
    *,
    entry_ids: set[str] | None = None,
) -> list[LexiconEntry]:
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


def scan_source_lexicon(
    project: Project,
    bundle,
    effective: EffectiveTranslationLexicon,
    *,
    chapter_id: str | None = None,
    entry_ids: set[str] | None = None,
) -> LexiconSourceScanResult:
    """Scan source records for applicable lexicon matches without targets."""
    entries = _relevant_entries(project, effective, entry_ids=entry_ids)
    result = LexiconSourceScanResult()
    for cid in _chapter_ids(bundle, chapter_id):
        for record_id in bundle.index.record_ids_by_chapter.get(cid, []):
            source_view = bundle.index.source_by_id[record_id]
            result.records_scanned += 1
            matches = lexicon_source_matches(source_view.source, entries)
            non_shadowed = [match for match in matches if not match.shadowed]
            if non_shadowed:
                result.matched_records += 1
            result.matches.extend(
                LexiconSourceMatch(
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


def _classify_match(
    match: LexiconSourceSpan,
    entry: LexiconEntry,
    *,
    record_id: str,
    chapter_id: str,
    source_text: str,
    target_text: str,
    effective_ref: str,
) -> LexiconMatch:
    forbidden_hits = entry_target_forbidden_hits(target_text, entry)
    preferred_hits, preferred_missing = entry_preferred_absence(target_text, entry)
    if forbidden_hits:
        return LexiconMatch(
            entry_id=entry.id,
            record_id=record_id,
            chapter_id=chapter_id,
            source_text=source_text,
            target_text=target_text,
            source_match=match.source_match,
            source_span=match.source_span,
            target_forbidden_found=forbidden_hits,
            target_preferred_found=preferred_hits,
            severity=entry.severity,
            reason="effective target contains a forbidden lexicon expression",
            status="forbidden_target",
            rule_code="lexicon.forbidden_target",
            effective_candidate_ref=effective_ref,
        )
    if preferred_missing:
        severity: Literal["info", "warn", "error"] = (
            "warn" if entry.preferred_policy == "advisory" else entry.severity
        )
        return LexiconMatch(
            entry_id=entry.id,
            record_id=record_id,
            chapter_id=chapter_id,
            source_text=source_text,
            target_text=target_text,
            source_match=match.source_match,
            source_span=match.source_span,
            target_preferred_found=preferred_hits,
            severity=severity,
            reason="effective target lacks a preferred or allowed lexicon expression",
            status="preferred_missing",
            rule_code="lexicon.preferred_missing",
            effective_candidate_ref=effective_ref,
        )
    return LexiconMatch(
        entry_id=entry.id,
        record_id=record_id,
        chapter_id=chapter_id,
        source_text=source_text,
        target_text=target_text,
        source_match=match.source_match,
        source_span=match.source_span,
        target_preferred_found=preferred_hits,
        severity="info",
        reason="effective target satisfies the applicable lexicon policy",
        status="clean",
        rule_code="lexicon.clean",
        effective_candidate_ref=effective_ref,
    )


def audit_lexicon(
    project: Project,
    bundle,
    effective: EffectiveTranslationLexicon,
    *,
    chapter_id: str | None = None,
    entry_ids: set[str] | None = None,
) -> LexiconAuditResult:
    """Audit effective targets for records whose source matches the lexicon."""
    entries = _relevant_entries(project, effective, entry_ids=entry_ids)
    entry_by_id = {entry.id: entry for entry in entries}
    store = load_translation_store(project)
    result = LexiconAuditResult()
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
                for match in lexicon_source_matches(source_view.source, entries)
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
            for match in matches:
                entry = entry_by_id[match.entry_id]
                finding = _classify_match(
                    match,
                    entry,
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
