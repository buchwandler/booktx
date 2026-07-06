"""Deterministic QA scan for translation review.

Checks effective targets against glossary entries, forbidden terms, regex
patterns, and source-language leftovers. Returns record-level findings that
agents can consume without scripting against ``translation-store.json``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from booktx.config import Project, load_translation_store
from booktx.glossary_audit import (
    GlossaryEntryEvaluation,
    evaluate_glossary_entries,
)
from booktx.glossary_match import (
    entry_is_binding,
)
from booktx.translation_store import effective_target_candidate

if TYPE_CHECKING:
    from booktx.context import GlossaryEntry
    from booktx.status import StatusBundle

__all__ = [
    "QaScanFinding",
    "QaScanResult",
    "qa_scan",
    "build_language_leftover_words",
]

# Simple English stopwords/heuristics for --language-leftovers detection.
# This is not a linguistic analysis; it catches common English words that
# survive in German target text.
_COMMON_ENGLISH_LEFTOVERS: set[str] = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "they",
    "have",
    "were",
    "been",
    "would",
    "could",
    "should",
    "about",
    "which",
    "their",
    "there",
    "because",
    "though",
    "while",
    "between",
    "through",
    "without",
    "before",
    "after",
    "into",
    "onto",
    "upon",
    "within",
    "along",
    "among",
    "himself",
    "herself",
    "itself",
    "being",
    "having",
    "doing",
    "going",
    "was",
    "are",
    "is",
    "does",
    "did",
    "done",
    "also",
    "very",
    "just",
    "only",
    "much",
    "such",
    "these",
    "those",
    "some",
    "any",
    "each",
    "every",
    "both",
    "few",
    "many",
    "most",
    "other",
    "more",
    "own",
    "same",
    "than",
    "then",
    "too",
    "well",
}


def build_language_leftover_words(custom_words: list[str] | None = None) -> set[str]:
    """Return the set of words treated as language leftovers.

    Always includes the built-in common English leftovers; merges optional
    custom words from a project config.
    """
    words = set(_COMMON_ENGLISH_LEFTOVERS)
    if custom_words:
        words.update(custom_words)
    return {w.lower() for w in words}


@dataclass(slots=True)
class QaScanFinding:
    """One record-level QA finding."""

    id: str
    chapter_id: str
    rule: str  # forbidden_target, glossary_mismatch, advisory_glossary,
    # pattern_match, language_leftover
    term: str = ""
    source: str = ""
    target: str = ""
    severity: str = "error"

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "chapter_id": self.chapter_id,
            "rule": self.rule,
            "term": self.term,
            "source": self.source,
            "target": self.target,
            "severity": self.severity,
        }


@dataclass(slots=True)
class QaScanResult:
    """Aggregate QA scan results."""

    findings: list[QaScanFinding] = field(default_factory=list)
    records_scanned: int = 0
    findings_count: int = 0


def qa_scan(
    project: Project,
    bundle: StatusBundle,
    *,
    chapter_id: str | None = None,
    target_only: bool = False,
    forbidden: bool = False,
    glossary: bool = False,
    target_contains: str | None = None,
    pattern: str | None = None,
    language_leftovers: str | None = None,
    include_advisory: bool = False,
) -> QaScanResult:
    """Scan effective targets for QA findings.

    Uses the effective target (active review or active translation), never
    iterates raw ``versions[]``/``reviews[]``.
    """
    store = load_translation_store(project)
    store_records = store.records
    source_by_id = bundle.index.source_by_id

    chapters_to_scan = (
        [chapter_id]
        if chapter_id is not None
        else list(bundle.index.record_ids_by_chapter)
    )

    # Load glossary entries for forbidden/glossary checks.
    glossary_entries: list[GlossaryEntry] = []
    if (forbidden or glossary) and project.profile_config is not None:
        from booktx.context import load_context

        ctx = load_context(project)
        if ctx is not None:
            glossary_entries = list(ctx.glossary)

    compiled_pattern: re.Pattern[str] | None = None
    if pattern is not None:
        try:
            compiled_pattern = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"invalid regex pattern {pattern!r}: {exc}") from exc

    leftover_words: set[str] | None = None
    if language_leftovers is not None:
        leftover_words = build_language_leftover_words()

    findings: list[QaScanFinding] = []
    records_scanned = 0

    for cid in chapters_to_scan:
        for record_id in bundle.index.record_ids_by_chapter.get(cid, []):
            stored = store_records.get(record_id)
            if stored is None:
                continue
            eff = effective_target_candidate(stored)
            if eff is None:
                continue
            source = source_by_id.get(record_id)
            source_text = source.source if source is not None else ""
            records_scanned += 1
            findings.extend(
                _collect_record_findings(
                    record_id,
                    cid,
                    eff.target,
                    source_text,
                    target_only=target_only,
                    forbidden=forbidden,
                    glossary=glossary,
                    glossary_entries=glossary_entries,
                    target_contains=target_contains,
                    compiled_pattern=compiled_pattern,
                    pattern=pattern,
                    leftover_words=leftover_words,
                    include_advisory=include_advisory,
                )
            )

    return QaScanResult(
        findings=findings,
        records_scanned=records_scanned,
        findings_count=len(findings),
    )


def _finding(
    record_id: str,
    chapter_id: str,
    source_text: str,
    target: str,
    target_only: bool,
    *,
    rule: str,
    term: str,
    severity: str = "error",
) -> QaScanFinding:
    """Build a finding using the shared source/target convention."""
    return QaScanFinding(
        id=record_id,
        chapter_id=chapter_id,
        rule=rule,
        term=term,
        source="" if target_only else source_text,
        target=target,
        severity=severity,
    )


def _check_glossary_targets(
    findings: list[QaScanFinding],
    record_id: str,
    chapter_id: str,
    source_text: str,
    target: str,
    target_only: bool,
    glossary_entries: list[GlossaryEntry],
    include_advisory: bool,
    evaluations_by_entry: dict[int, list[GlossaryEntryEvaluation]],
) -> None:
    """Check approved glossary entries against target for missing/advisory terms."""
    for idx, entry in enumerate(glossary_entries):
        if entry.status != "approved" or entry.target is None:
            continue
        if entry.enforce == "off":
            continue
        evaluations = evaluations_by_entry.get(idx, [])
        if not evaluations:
            continue
        if entry.require_target:
            if any(
                not (
                    evaluation.evaluation.required_target_found
                    or evaluation.evaluation.allowed_target_found
                )
                for evaluation in evaluations
            ):
                findings.append(
                    _finding(
                        record_id,
                        chapter_id,
                        source_text,
                        target,
                        target_only,
                        rule="glossary_mismatch",
                        term=f"{entry.source} -> {entry.target}",
                    )
                )
        elif include_advisory and not entry_is_binding(entry):
            if any(
                not (
                    evaluation.evaluation.required_target_found
                    or evaluation.evaluation.allowed_target_found
                )
                for evaluation in evaluations
            ):
                findings.append(
                    _finding(
                        record_id,
                        chapter_id,
                        source_text,
                        target,
                        target_only,
                        rule="advisory_glossary",
                        term=f"{entry.source} -> {entry.target}",
                        severity="info",
                    )
                )


def _collect_record_findings(
    record_id: str,
    chapter_id: str,
    target: str,
    source_text: str,
    *,
    target_only: bool,
    forbidden: bool,
    glossary: bool,
    glossary_entries: list[GlossaryEntry],
    target_contains: str | None,
    compiled_pattern: re.Pattern[str] | None,
    pattern: str | None,
    leftover_words: set[str] | None,
    include_advisory: bool = False,
) -> list[QaScanFinding]:
    """Run all enabled checks for one record and return the findings."""
    findings: list[QaScanFinding] = []

    evaluations_by_entry = evaluate_glossary_entries(
        glossary=glossary_entries, source_text=source_text, target=target
    )
    if forbidden:
        for idx, entry in enumerate(glossary_entries):
            if entry.enforce == "off" or not entry.forbidden_targets:
                continue
            evaluations = evaluations_by_entry.get(idx, [])
            if not evaluations:
                continue
            for evaluation in evaluations:
                for ft in evaluation.evaluation.forbidden_target_found:
                    findings.append(
                        _finding(
                            record_id,
                            chapter_id,
                            source_text,
                            target,
                            target_only,
                            rule="forbidden_target",
                            term=ft,
                            severity="error",
                        )
                    )

    if glossary:
        _check_glossary_targets(
            findings,
            record_id,
            chapter_id,
            source_text,
            target,
            target_only,
            glossary_entries,
            include_advisory,
            evaluations_by_entry,
        )

    if target_contains is not None and target_contains.lower() in target.lower():
        findings.append(
            _finding(
                record_id,
                chapter_id,
                source_text,
                target,
                target_only,
                rule="target_contains",
                term=target_contains,
            )
        )

    if compiled_pattern is not None:
        search_text = target if target_only else f"{source_text} {target}"
        if compiled_pattern.search(search_text):
            findings.append(
                _finding(
                    record_id,
                    chapter_id,
                    source_text,
                    target,
                    target_only,
                    rule="pattern_match",
                    term=pattern or "",
                )
            )

    if leftover_words is not None:
        target_words = set(re.findall(r"\b\w+\b", target.lower()))
        for word in sorted(target_words & leftover_words):
            findings.append(
                _finding(
                    record_id,
                    chapter_id,
                    source_text,
                    target,
                    target_only,
                    rule="language_leftover",
                    term=word,
                )
            )

    return findings
