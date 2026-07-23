"""Shared glossary snapshot helpers for durable task artifacts."""

from __future__ import annotations

from booktx.context import GlossaryEntry
from booktx.glossary_match import entry_is_binding, source_glossary_matches
from booktx.models import ApplicableGlossaryEntrySnapshot

__all__ = ["applicable_glossary_snapshots"]


def applicable_glossary_snapshots(
    source: str, glossary: list[GlossaryEntry]
) -> list[ApplicableGlossaryEntrySnapshot]:
    """Return binding glossary entries applicable to ``source``."""
    matched_by_entry: dict[int, str] = {}
    for span in source_glossary_matches(source, glossary):
        if span.shadowed:
            continue
        matched_by_entry.setdefault(span.entry_index, span.matched_term)
    snapshots: list[ApplicableGlossaryEntrySnapshot] = []
    for idx, entry in enumerate(glossary):
        matched = matched_by_entry.get(idx)
        if matched is None or not entry_is_binding(entry):
            continue
        snapshots.append(
            ApplicableGlossaryEntrySnapshot(
                source=entry.source,
                source_variants=list(entry.source_variants),
                matched_source_cue=matched,
                target=entry.target,
                target_variants=list(entry.target_variants),
                usage_notes=dict(entry.usage_notes),
                concept_kind=entry.concept_kind,
                require_concept=entry.require_concept,
                require_target=entry.require_target,
                forbidden_targets=list(entry.forbidden_targets),
                enforce=entry.enforce,
                case_sensitive=entry.case_sensitive,
                notes=entry.notes,
            )
        )
    return snapshots
