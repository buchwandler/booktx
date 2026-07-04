"""Glossary audit for a single source term.

``context audit-term`` reports, for one glossary entry, how many effective
records contain the source term and how many are clean vs. violate forbidden
targets vs. miss the approved target. It uses the same shared matcher as
validation and QA scan, and scopes forbidden targets to records where the
source term (or one of its source variants) occurs.

It can also generate a safe correction-block template: the ingest block
contains only record headers and the current (editable) target text, while a
companion source block carries the source text and current target for
reference. The generator never guesses the corrected translation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from booktx.config import Project, load_translation_store
from booktx.context import GlossaryEntry, load_context
from booktx.glossary_match import (
    source_glossary_matches,
    target_terms,
)
from booktx.models import TranslationReviewCandidate
from booktx.termbase import TermbaseEntry
from booktx.termbase_match import TermbaseRuleEvaluation, evaluate_entry_policy
from booktx.translation_store import (
    effective_target_candidate,
)

if TYPE_CHECKING:
    from booktx.status import StatusBundle

__all__ = [
    "GlossaryAuditRecord",
    "GlossaryAuditResult",
    "audit_glossary_term",
    "evaluate_glossary_entry",
    "render_ingest_block",
    "render_source_block",
]


@dataclass(slots=True)
class GlossaryEntryEvaluation:
    matched_span: object
    evaluation: TermbaseRuleEvaluation


def _evaluation_missing_required(item: GlossaryEntryEvaluation) -> bool:
    return not (
        item.evaluation.required_target_found or item.evaluation.allowed_target_found
    )


@dataclass(slots=True)
class GlossaryAuditRecord:
    """One effective or inactive record relevant to the audited term."""

    record_id: str
    chapter_id: str
    source: str
    target: str
    candidate_ref: str
    forbidden_found: list[str] = field(default_factory=list)
    missing_approved: bool = False

    @property
    def violates(self) -> bool:
        return bool(self.forbidden_found) or self.missing_approved


@dataclass(slots=True)
class GlossaryAuditResult:
    """Aggregate audit outcome for one glossary source term."""

    source_term: str
    approved_targets: list[str]
    forbidden_targets: list[str]
    records_with_source_term: int = 0
    effective_clean: int = 0
    forbidden_violation_records: int = 0
    missing_approved_records: int = 0
    records: list[GlossaryAuditRecord] = field(default_factory=list)
    inactive_violation_records: int = 0
    inactive_records: list[GlossaryAuditRecord] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "source_term": self.source_term,
            "approved_targets": list(self.approved_targets),
            "forbidden_targets": list(self.forbidden_targets),
            "records_with_source_term": self.records_with_source_term,
            "effective_clean": self.effective_clean,
            "forbidden_violation_records": self.forbidden_violation_records,
            "missing_approved_records": self.missing_approved_records,
            "records": [_record_dict(r) for r in self.records if r.violates],
            "inactive_violation_records": self.inactive_violation_records,
            "inactive_records": [
                _record_dict(r) for r in self.inactive_records if r.violates
            ],
        }


def _record_dict(r: GlossaryAuditRecord) -> dict[str, object]:
    return {
        "record_id": r.record_id,
        "chapter_id": r.chapter_id,
        "candidate_ref": r.candidate_ref,
        "forbidden_found": list(r.forbidden_found),
        "missing_approved": r.missing_approved,
    }


def _glossary_entry_to_termbase(entry: GlossaryEntry) -> TermbaseEntry:
    return TermbaseEntry(
        id=f"legacy-glossary-{entry.source}",
        kind="flat_term",
        source=entry.source,
        source_variants=list(entry.source_variants),
        source_language="en",
        case_sensitive=entry.case_sensitive,
        target_preferred=target_terms(entry),
        target_forbidden=list(entry.forbidden_targets),
        preferred_policy="required" if entry.require_target else "off",
        target_language="de",
        severity="warn" if entry.enforce == "off" else entry.enforce,
        rationale=entry.notes,
    )


def evaluate_glossary_entry(
    *,
    entry: GlossaryEntry,
    source_text: str,
    target: str,
) -> list[GlossaryEntryEvaluation]:
    adapted = _glossary_entry_to_termbase(entry)
    spans = [
        span
        for span in source_glossary_matches(source_text, [entry])
        if not span.shadowed
    ]
    return [
        GlossaryEntryEvaluation(
            matched_span=span,
            evaluation=evaluate_entry_policy(
                target,
                adapted,
                source_match=span.matched_term,
                source_span=(span.start, span.end),
                occurrence_index=index,
            )[0],
        )
        for index, span in enumerate(spans)
    ]


def audit_glossary_term(
    project: Project,
    bundle: StatusBundle,
    *,
    source_term: str,
    include_inactive: bool = False,
    chapter_id: str | None = None,
) -> GlossaryAuditResult | None:
    """Audit one glossary source term across effective (and optional) inactive records.

    Returns ``None`` when no glossary entry matches ``source_term`` exactly.
    """
    ctx = load_context(project)
    entry: GlossaryEntry | None = None
    if ctx is not None:
        for candidate_entry in ctx.glossary:
            if candidate_entry.source == source_term:
                entry = candidate_entry
                break
    if entry is None:
        return None

    store = load_translation_store(project)
    store_records = store.records
    source_by_id = bundle.index.source_by_id

    result = GlossaryAuditResult(
        source_term=entry.source,
        approved_targets=target_terms(entry),
        forbidden_targets=list(entry.forbidden_targets),
    )

    chapters = (
        [chapter_id]
        if chapter_id is not None
        else list(bundle.index.record_ids_by_chapter)
    )
    seen_record_ids: set[str] = set()
    for cid in chapters:
        for record_id in bundle.index.record_ids_by_chapter.get(cid, []):
            if record_id in seen_record_ids:
                continue
            seen_record_ids.add(record_id)
            source_view = source_by_id.get(record_id)
            source_text = source_view.source if source_view is not None else ""
            evaluations = evaluate_glossary_entry(
                entry=entry,
                source_text=source_text,
                target="",
            )
            if not evaluations:
                continue
            result.records_with_source_term += 1
            stored = store_records.get(record_id)
            if stored is None:
                continue
            effective = effective_target_candidate(stored)
            if effective is None:
                continue
            effective_ref = (
                effective.review_ref
                if isinstance(effective, TranslationReviewCandidate)
                else effective.version_ref
            )
            evaluations = evaluate_glossary_entry(
                entry=entry, source_text=source_text, target=effective.target
            )
            forbidden_found = [
                hit
                for item in evaluations
                for hit in item.evaluation.forbidden_target_found
            ]
            missing_approved = any(
                entry.require_target and _evaluation_missing_required(item)
                for item in evaluations
            )
            rec = GlossaryAuditRecord(
                record_id=record_id,
                chapter_id=cid,
                source=source_text,
                target=effective.target,
                candidate_ref=effective_ref,
                forbidden_found=forbidden_found,
                missing_approved=missing_approved,
            )
            result.records.append(rec)
            if rec.violates:
                if forbidden_found:
                    result.forbidden_violation_records += 1
                if missing_approved:
                    result.missing_approved_records += 1
            else:
                result.effective_clean += 1

            if include_inactive:
                effective_translation_ref = (
                    None
                    if isinstance(effective, TranslationReviewCandidate)
                    else effective.version_ref
                )
                for inactive in stored.versions:
                    if inactive.version_ref == effective_translation_ref:
                        continue
                    inactive_evaluations = evaluate_glossary_entry(
                        entry=entry,
                        source_text=source_text,
                        target=inactive.target,
                    )
                    in_forbidden = [
                        hit
                        for item in inactive_evaluations
                        for hit in item.evaluation.forbidden_target_found
                    ]
                    in_missing = any(
                        entry.require_target and _evaluation_missing_required(item)
                        for item in inactive_evaluations
                    )
                    if in_forbidden or in_missing:
                        result.inactive_records.append(
                            GlossaryAuditRecord(
                                record_id=record_id,
                                chapter_id=cid,
                                source=source_text,
                                target=inactive.target,
                                candidate_ref=inactive.version_ref,
                                forbidden_found=in_forbidden,
                                missing_approved=in_missing,
                            )
                        )
                        result.inactive_violation_records += 1

    return result


def _violating_effective(
    records: list[GlossaryAuditRecord],
) -> list[GlossaryAuditRecord]:
    return [r for r in records if r.violates]


def render_ingest_block(result: GlossaryAuditResult) -> str:
    """Render the editable ingest block for violating effective records.

    Contains only ``>>> <record-id>`` headers and the current target text.
    No ``# source:``/``# current:`` metadata is emitted here: the block parser
    preserves post-header comment lines as target text.
    """
    lines: list[str] = []
    for rec in _violating_effective(result.records):
        lines.append(f">>> {rec.record_id}")
        lines.append(rec.target)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_source_block(result: GlossaryAuditResult) -> str:
    """Render the reference-only companion source block.

    Every line is a comment so the file can never be accidentally ingested.
    """
    approved = " / ".join(result.approved_targets) or "(none)"
    lines: list[str] = [
        f"# glossary audit reference: {result.source_term} -> {approved}",
        "# Reference only; do not ingest this file. Edit the ingest block instead.",
        "#",
    ]
    for rec in _violating_effective(result.records):
        lines.append(f"# >>> {rec.record_id}")
        for tag, value in (("source", rec.source), ("current", rec.target)):
            for line in value.splitlines() or [""]:
                lines.append(f"# {tag}: {line}")
        lines.append("#")
    return "\n".join(lines) + "\n"
