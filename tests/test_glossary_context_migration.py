from __future__ import annotations

from booktx.context import GlossaryEntry, TranslationContext
from booktx.glossary_context_migration import (
    apply_context_glossary_migration,
    context_glossary_to_termbase_entry,
    plan_context_glossary_migration,
    termbase_entry_to_context_glossary,
)
from booktx.termbase import TranslationTermbase


def test_context_glossary_round_trips_through_termbase_entry() -> None:
    legacy = GlossaryEntry(
        source="Ant-kinden",
        source_variants=["Ant kinden"],
        target="Ameisenkinden",
        target_variants=["Ameisen-Kinden"],
        forbidden_targets=["Ameisenvolk"],
        category="people",
        notes="Use the established series term.",
        case_sensitive=True,
        require_target=True,
        enforce="error",
    )

    entry = context_glossary_to_termbase_entry(
        legacy,
        source_language="en",
        target_language="de",
        target_locale="de-DE",
    )

    assert entry.kind == "flat_term"
    assert entry.source == "Ant-kinden"
    assert entry.target_preferred == ["Ameisenkinden", "Ameisen-Kinden"]
    assert entry.target_forbidden == ["Ameisenvolk"]
    assert entry.preferred_policy == "required"
    assert termbase_entry_to_context_glossary(entry).model_dump(
        mode="json"
    ) == legacy.model_dump(mode="json")


def test_plan_context_glossary_migration_dry_run_clears_planned_context() -> None:
    context = TranslationContext(
        source_language="en",
        target_language="de",
        glossary=[
            GlossaryEntry(source="empire", target="Imperium", require_target=True)
        ],
    )
    termbase = TranslationTermbase(
        language_key="de",
        source_language="en",
        target_language="de",
        entries=[],
    )

    result = plan_context_glossary_migration(context, termbase)

    assert result.changed is True
    assert result.parity_verified is True
    assert result.entries_added == 1
    assert result.entries_cleared == 1
    assert context.glossary
    assert termbase.entries == []
    assert result.context.glossary == []
    assert result.termbase.entries[0].source == "empire"


def test_apply_context_glossary_migration_returns_verified_result() -> None:
    context = TranslationContext(
        source_language="en",
        target_language="de",
        glossary=[GlossaryEntry(source="Wasp", target="Wespe", require_target=True)],
    )
    termbase = TranslationTermbase(
        language_key="de",
        source_language="en",
        target_language="de",
        entries=[],
    )

    result = apply_context_glossary_migration(context, termbase)

    assert result.parity_verified is True
    assert result.context.glossary == []
    assert [entry.source for entry in result.termbase.entries] == ["Wasp"]
