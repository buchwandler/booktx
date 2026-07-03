"""Applicable-lexicon snapshot helpers for tasks and submission guards."""

from __future__ import annotations

from collections.abc import Mapping

from booktx.config import Project
from booktx.lexicon import (
    EffectiveTranslationLexicon,
    LexiconEntry,
    effective_approved_entries,
    resolve_effective_lexicon,
)
from booktx.lexicon_match import lexicon_source_matches
from booktx.models import (
    ApplicableLexiconEntrySnapshot,
    ApplicableLexiconExampleSnapshot,
)
from booktx.versioning import canonical_json_sha256

__all__ = [
    "applicable_lexicon_sha256_for_record_sources",
    "collect_applicable_lexicon_for_record_sources",
]


def _relevant_entries(
    project: Project, effective: EffectiveTranslationLexicon
) -> list[LexiconEntry]:
    target_locale = project.config.target_locale or project.config.target_language
    return [
        entry
        for entry in effective_approved_entries(
            effective, source_language=project.config.source_language
        )
        if entry.target_language == project.config.target_language
        and entry.target_locale in {"", target_locale}
    ]


def _snapshot(
    entry: LexiconEntry, *, source_match: str, source_span: tuple[int, int]
) -> ApplicableLexiconEntrySnapshot:
    return ApplicableLexiconEntrySnapshot(
        entry_id=entry.id,
        kind=entry.kind,
        source=entry.source,
        source_variants=list(entry.source_variants),
        source_regex=entry.source_regex,
        source_language=entry.source_language,
        case_sensitive=entry.case_sensitive,
        matched_source_cue=source_match,
        matched_source_span=source_span,
        target_preferred=list(entry.target_preferred),
        target_allowed=list(entry.target_allowed),
        target_forbidden=list(entry.target_forbidden),
        target_regex_forbidden=list(entry.target_regex_forbidden),
        preferred_policy=entry.preferred_policy,
        severity=entry.severity,
        sense=entry.sense,
        rationale=entry.rationale,
        examples=[
            ApplicableLexiconExampleSnapshot(
                source=example.source,
                good_target=example.good_target,
                bad_target=example.bad_target,
                note=example.note,
            )
            for example in entry.examples
        ],
    )


def _choose_per_entry(
    snapshots: list[ApplicableLexiconEntrySnapshot],
) -> list[ApplicableLexiconEntrySnapshot]:
    by_entry: dict[str, ApplicableLexiconEntrySnapshot] = {}
    for snapshot in snapshots:
        current = by_entry.get(snapshot.entry_id)
        if current is None:
            by_entry[snapshot.entry_id] = snapshot
            continue
        current_len = current.matched_source_span[1] - current.matched_source_span[0]
        new_len = snapshot.matched_source_span[1] - snapshot.matched_source_span[0]
        if new_len > current_len or (
            new_len == current_len
            and snapshot.matched_source_span < current.matched_source_span
        ):
            by_entry[snapshot.entry_id] = snapshot
    return sorted(
        by_entry.values(),
        key=lambda item: (
            item.matched_source_span[0],
            -(item.matched_source_span[1] - item.matched_source_span[0]),
            item.entry_id,
        ),
    )


def _sha_payload(
    record_snapshots: Mapping[str, list[ApplicableLexiconEntrySnapshot]],
) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for record_id in sorted(record_snapshots):
        for snapshot in sorted(
            record_snapshots[record_id],
            key=lambda item: (
                item.matched_source_span[0],
                item.matched_source_span[1],
                item.entry_id,
            ),
        ):
            payload.append(
                {
                    "record_id": record_id,
                    "entry_id": snapshot.entry_id,
                    "kind": snapshot.kind,
                    "source": snapshot.source,
                    "source_variants": list(snapshot.source_variants),
                    "source_regex": snapshot.source_regex,
                    "source_language": snapshot.source_language,
                    "case_sensitive": snapshot.case_sensitive,
                    "matched_source_cue": snapshot.matched_source_cue,
                    "matched_source_span": list(snapshot.matched_source_span),
                    "target_preferred": list(snapshot.target_preferred),
                    "target_allowed": list(snapshot.target_allowed),
                    "target_forbidden": list(snapshot.target_forbidden),
                    "target_regex_forbidden": list(snapshot.target_regex_forbidden),
                    "preferred_policy": snapshot.preferred_policy,
                    "severity": snapshot.severity,
                    "sense": snapshot.sense,
                    "rationale": snapshot.rationale,
                    "examples": [
                        {
                            "source": example.source,
                            "good_target": example.good_target,
                            "bad_target": example.bad_target,
                            "note": example.note,
                        }
                        for example in snapshot.examples
                    ],
                }
            )
    return payload


def collect_applicable_lexicon_for_record_sources(
    project: Project,
    record_sources: Mapping[str, str],
) -> tuple[dict[str, list[ApplicableLexiconEntrySnapshot]], str]:
    effective, _ = resolve_effective_lexicon(project)
    entries = _relevant_entries(project, effective)
    entry_by_id = {entry.id: entry for entry in entries}
    record_snapshots: dict[str, list[ApplicableLexiconEntrySnapshot]] = {}
    for record_id, source_text in record_sources.items():
        snapshots: list[ApplicableLexiconEntrySnapshot] = []
        for match in lexicon_source_matches(source_text, entries):
            if match.shadowed:
                continue
            snapshots.append(
                _snapshot(
                    entry_by_id[match.entry_id],
                    source_match=match.source_match,
                    source_span=match.source_span,
                )
            )
        record_snapshots[record_id] = _choose_per_entry(snapshots)
    return record_snapshots, canonical_json_sha256(_sha_payload(record_snapshots))


def applicable_lexicon_sha256_for_record_sources(
    project: Project,
    record_sources: Mapping[str, str],
) -> str:
    return collect_applicable_lexicon_for_record_sources(project, record_sources)[1]
