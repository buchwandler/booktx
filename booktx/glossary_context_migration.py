"""Legacy context glossary migration helpers for the canonical termbase."""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict, Field

from booktx.context import GlossaryEntry, TranslationContext
from booktx.termbase import TermbaseEntry, TranslationTermbase

__all__ = [
    "GlossaryContextMigrationResult",
    "context_glossary_to_termbase_entry",
    "termbase_entry_to_context_glossary",
    "plan_context_glossary_migration",
    "apply_context_glossary_migration",
]


class GlossaryContextMigrationResult(BaseModel):
    """Dry-run result for moving legacy context glossary entries to termbase."""

    model_config = ConfigDict(extra="forbid")

    changed: bool
    entries_added: int = 0
    entries_cleared: int = 0
    parity_verified: bool = False
    findings: list[str] = Field(default_factory=list)
    termbase: TranslationTermbase
    context: TranslationContext


def _entry_id_for_glossary(entry: GlossaryEntry) -> str:
    slug = "-".join(entry.source.lower().split()) or "term"
    slug = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in slug).strip("-")
    digest = hashlib.sha256(entry.source.encode("utf-8")).hexdigest()[:8]
    return f"legacy-glossary-{slug or 'term'}-{digest}"


def context_glossary_to_termbase_entry(
    entry: GlossaryEntry,
    *,
    source_language: str,
    target_language: str,
    target_locale: str = "",
) -> TermbaseEntry:
    """Convert one legacy context glossary entry to a flat termbase entry."""

    preferred = [entry.target] if entry.target else []
    preferred.extend(entry.target_variants)
    return TermbaseEntry(
        id=_entry_id_for_glossary(entry),
        status="disabled" if entry.enforce == "off" else "approved",
        kind="flat_term",
        source=entry.source,
        source_variants=entry.source_variants,
        source_language=source_language,
        case_sensitive=entry.case_sensitive,
        target_preferred=preferred,
        target_forbidden=entry.forbidden_targets,
        preferred_policy="required" if entry.require_target else "off",
        target_language=target_language,
        target_locale=target_locale,
        sense=entry.category,
        rationale=entry.notes,
        severity="warn" if entry.enforce == "warn" else "error",
    )


def termbase_entry_to_context_glossary(
    entry: TermbaseEntry,
) -> GlossaryEntry:
    """Render flat termbase entry as legacy context glossary for parity checks."""

    target = entry.target_preferred[0] if entry.target_preferred else None
    target_variants = entry.target_preferred[1:]
    return GlossaryEntry(
        source=entry.source,
        source_variants=list(entry.source_variants),
        target=target,
        target_variants=target_variants,
        forbidden_targets=list(entry.target_forbidden),
        category=entry.sense,
        status="open",
        notes=entry.rationale,
        case_sensitive=entry.case_sensitive,
        require_target=entry.preferred_policy == "required",
        enforce="off"
        if entry.status == "disabled"
        else ("warn" if entry.severity == "info" else entry.severity),
    )


def _legacy_parity(a: GlossaryEntry, b: GlossaryEntry) -> bool:
    return a.model_dump(mode="json") == b.model_dump(mode="json")


def plan_context_glossary_migration(
    context: TranslationContext, termbase: TranslationTermbase
) -> GlossaryContextMigrationResult:
    """Plan migration without mutating context or termbase.

    Legacy context glossary remains readable at lowest precedence until callers
    apply this plan after parity has been verified.
    """

    planned_context = context.model_copy(deep=True)
    planned_termbase = termbase.model_copy(deep=True)
    findings: list[str] = []
    existing_ids = {entry.id for entry in planned_termbase.entries}
    added = 0
    parity_verified = True

    for legacy in context.glossary:
        converted = context_glossary_to_termbase_entry(
            legacy,
            source_language=context.source_language,
            target_language=termbase.target_language or context.target_language,
            target_locale=termbase.target_locale,
        )
        if converted.id not in existing_ids:
            planned_termbase.entries.append(converted)
            existing_ids.add(converted.id)
            added += 1
            findings.append(f"add termbase entry for legacy glossary: {legacy.source}")
        round_tripped = termbase_entry_to_context_glossary(converted)
        if not _legacy_parity(legacy, round_tripped):
            parity_verified = False
            findings.append(f"parity mismatch for legacy glossary: {legacy.source}")

    cleared = 0
    if context.glossary and parity_verified:
        cleared = len(planned_context.glossary)
        planned_context.glossary = []
        findings.append("clear legacy context glossary after parity verification")

    return GlossaryContextMigrationResult(
        changed=bool(added or cleared),
        entries_added=added,
        entries_cleared=cleared,
        parity_verified=parity_verified,
        findings=findings,
        termbase=planned_termbase,
        context=planned_context,
    )


def apply_context_glossary_migration(
    context: TranslationContext, termbase: TranslationTermbase
) -> GlossaryContextMigrationResult:
    """Return the verified migration result or raise if semantic parity fails."""

    result = plan_context_glossary_migration(context, termbase)
    if not result.parity_verified:
        raise ValueError("legacy context glossary migration parity failed")
    return result
