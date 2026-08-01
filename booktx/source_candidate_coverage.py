"""Shared, conservative coverage classifications for source candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from booktx.config import Project
from booktx.context import TranslationContext
from booktx.source_analysis import SourceCandidate
from booktx.source_analysis_context import SourceAnalysisDecisions

CoverageState = Literal[
    "uncovered",
    "exact_context_glossary",
    "context_source_variant",
    "exact_termbase",
    "termbase_source_variant",
    "protected_name",
    "imported_candidate_policy",
    "family_policy",
    "metadata_artifact",
    "parser_artifact",
]


@dataclass(frozen=True)
class CandidateCoverage:
    state: CoverageState
    confidence: Literal["exact", "high", "suggested"]
    evidence: tuple[str, ...]
    automatic_disposition: Literal["none", "suppress", "reviewed", "ignored"]


def _term_sets(context: TranslationContext) -> tuple[set[str], set[str]]:
    exact: set[str] = set()
    variants: set[str] = set()
    for entry in context.glossary:
        if entry.status == "approved" or entry.source_analysis_candidate_id:
            exact.add(entry.source.casefold())
            variants.update(value.casefold() for value in entry.source_variants)
    return exact, variants


def _termbase_terms(project: Project) -> tuple[set[str], set[str]]:
    try:
        from booktx.workflows.termbase import termbase_status_workflow

        payload = termbase_status_workflow(
            project.root, profile=project.profile, scope="effective", language=None
        )
    except Exception:
        return set(), set()
    exact: set[str] = set()
    variants: set[str] = set()
    for entry in payload.get("entries", []):
        if entry.get("source"):
            exact.add(str(entry["source"]).casefold())
        variants.update(str(value).casefold() for value in entry.get("source_variants", []))
    return exact, variants


def classify_candidate(
    candidate: SourceCandidate,
    context: TranslationContext,
    project: Project,
    decisions: SourceAnalysisDecisions,
) -> CandidateCoverage:
    normalized = candidate.normalized.casefold()
    exact, variants = _term_sets(context)
    if normalized in exact or candidate.text.casefold() in exact:
        return CandidateCoverage("exact_context_glossary", "exact", ("approved context glossary",), "suppress")
    if normalized in variants or candidate.text.casefold() in variants:
        return CandidateCoverage("context_source_variant", "exact", ("approved context source variant",), "suppress")
    tb_exact, tb_variants = _termbase_terms(project)
    if normalized in tb_exact or candidate.text.casefold() in tb_exact:
        return CandidateCoverage("exact_termbase", "exact", ("effective termbase source",), "suppress")
    if normalized in tb_variants or candidate.text.casefold() in tb_variants:
        return CandidateCoverage("termbase_source_variant", "exact", ("effective termbase source variant",), "suppress")
    if candidate.already_protected:
        return CandidateCoverage("protected_name", "exact", ("protected source name",), "suppress")
    if any(d.candidate_id == candidate.id for d in decisions.promotions):
        return CandidateCoverage("imported_candidate_policy", "exact", ("candidate already promoted",), "suppress")
    if any(d.candidate_id == candidate.id for d in decisions.dispositions):
        disposition = next(d.disposition for d in decisions.dispositions if d.candidate_id == candidate.id)
        return CandidateCoverage(
            "imported_candidate_policy", "exact", (f"existing disposition: {disposition}",),
            "ignored" if disposition == "ignored" else "reviewed",
        )
    reason_codes = {value.casefold() for value in candidate.reason_codes}
    detectors = {value.casefold() for value in candidate.detectors}
    if candidate.suppression_reason or {"metadata", "front_matter", "copyright"} & reason_codes:
        return CandidateCoverage("metadata_artifact", "high", (candidate.suppression_reason or candidate.reason,), "ignored")
    if {"parser", "noun_chunk", "sentence_initial"} & (reason_codes | detectors):
        return CandidateCoverage("parser_artifact", "suggested", tuple(candidate.reason_codes or [candidate.reason]), "none")
    if normalized.endswith("-kinden") and any(term.endswith("-kinden") for term in exact | variants):
        return CandidateCoverage("family_policy", "suggested", ("approved X-kinden family policy",), "reviewed")
    return CandidateCoverage("uncovered", "suggested", (candidate.reason or "no approved coverage",), "none")


__all__ = ["CandidateCoverage", "CoverageState", "classify_candidate"]
