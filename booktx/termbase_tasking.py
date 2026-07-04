"""Applicable-termbase snapshot helpers for tasks and submission guards."""

from __future__ import annotations

from collections.abc import Mapping

from booktx.config import Project
from booktx.models import (
    ApplicableTermbaseEntrySnapshot,
    ApplicableTermbaseExampleSnapshot,
    ApplicableTermbaseUsageRuleSnapshot,
)
from booktx.termbase import (
    EffectiveTranslationTermbase,
    TermbaseEntry,
    TermbaseUsageRule,
    effective_approved_entries,
    resolve_effective_termbase,
)
from booktx.termbase_match import evaluate_entry_policy, termbase_source_matches
from booktx.validate import Finding
from booktx.versioning import canonical_json_sha256

__all__ = [
    "applicable_termbase_sha256_for_record_sources",
    "collect_applicable_termbase_for_record_sources",
    "validate_termbase_record_pair",
]


def _relevant_entries(
    project: Project, effective: EffectiveTranslationTermbase
) -> list[TermbaseEntry]:
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
    entry: TermbaseEntry, *, source_match: str, source_span: tuple[int, int]
) -> ApplicableTermbaseEntrySnapshot:
    return ApplicableTermbaseEntrySnapshot(
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
            ApplicableTermbaseExampleSnapshot(
                source=example.source,
                good_target=example.good_target,
                bad_target=example.bad_target,
                note=example.note,
            )
            for example in entry.examples
        ],
        usage_rules=[
            ApplicableTermbaseUsageRuleSnapshot(
                rule_id=rule.id,
                context_id=rule.context_id,
                source_cue=rule.source_cue,
                source_regex=rule.source_regex,
                required_target_literals=list(rule.required_target_literals),
                required_target_regexes=list(rule.required_target_regexes),
                allowed_target_literals=list(rule.allowed_target_literals),
                allowed_target_regexes=list(rule.allowed_target_regexes),
                forbidden_target_literals=list(rule.forbidden_target_literals),
                forbidden_target_regexes=list(rule.forbidden_target_regexes),
                severity=rule.severity,
                prompt=rule.prompt,
                fallback=rule.fallback,
            )
            for rule in entry.usage_rules
        ],
    )


def _choose_per_entry(
    snapshots: list[ApplicableTermbaseEntrySnapshot],
) -> list[ApplicableTermbaseEntrySnapshot]:
    by_entry: dict[str, ApplicableTermbaseEntrySnapshot] = {}
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
    record_snapshots: Mapping[str, list[ApplicableTermbaseEntrySnapshot]],
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
                    "usage_rules": [
                        {
                            "rule_id": rule.rule_id,
                            "context_id": rule.context_id,
                            "source_cue": rule.source_cue,
                            "source_regex": rule.source_regex,
                            "required_target_literals": list(
                                rule.required_target_literals
                            ),
                            "required_target_regexes": list(
                                rule.required_target_regexes
                            ),
                            "allowed_target_literals": list(
                                rule.allowed_target_literals
                            ),
                            "allowed_target_regexes": list(rule.allowed_target_regexes),
                            "forbidden_target_literals": list(
                                rule.forbidden_target_literals
                            ),
                            "forbidden_target_regexes": list(
                                rule.forbidden_target_regexes
                            ),
                            "severity": rule.severity,
                            "prompt": rule.prompt,
                            "fallback": rule.fallback,
                        }
                        for rule in snapshot.usage_rules
                    ],
                }
            )
    return payload


def collect_applicable_termbase_for_record_sources(
    project: Project,
    record_sources: Mapping[str, str],
) -> tuple[dict[str, list[ApplicableTermbaseEntrySnapshot]], str]:
    effective, _ = resolve_effective_termbase(project)
    entries = _relevant_entries(project, effective)
    entry_by_id = {entry.id: entry for entry in entries}
    record_snapshots: dict[str, list[ApplicableTermbaseEntrySnapshot]] = {}
    for record_id, source_text in record_sources.items():
        snapshots: list[ApplicableTermbaseEntrySnapshot] = []
        for match in termbase_source_matches(source_text, entries):
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


def applicable_termbase_sha256_for_record_sources(
    project: Project,
    record_sources: Mapping[str, str],
) -> str:
    return collect_applicable_termbase_for_record_sources(project, record_sources)[1]


def _termbase_entry_from_snapshot(
    snapshot: ApplicableTermbaseEntrySnapshot,
) -> TermbaseEntry:
    """Reconstruct an evaluable TermbaseEntry from an applicable-termbase snapshot.

    The snapshot already carries canonical, validated fields, so reconstruction
    is safe and lets the shared ``evaluate_entry_policy`` machinery classify the
    target without duplicating the forbidden/preferred logic.
    """
    return TermbaseEntry(
        id=snapshot.entry_id,
        kind=snapshot.kind,
        source=snapshot.source,
        source_variants=list(snapshot.source_variants),
        source_regex=snapshot.source_regex,
        source_language=snapshot.source_language,
        case_sensitive=snapshot.case_sensitive,
        target_preferred=list(snapshot.target_preferred),
        target_allowed=list(snapshot.target_allowed),
        target_forbidden=list(snapshot.target_forbidden),
        target_regex_forbidden=list(snapshot.target_regex_forbidden),
        preferred_policy=snapshot.preferred_policy,
        target_language="de",
        severity=snapshot.severity,
        sense=snapshot.sense,
        rationale=snapshot.rationale,
        usage_rules=[
            TermbaseUsageRule(
                id=rule.rule_id,
                context_id=rule.context_id,
                source_cue=rule.source_cue,
                source_regex=rule.source_regex,
                required_target_literals=list(rule.required_target_literals),
                required_target_regexes=list(rule.required_target_regexes),
                allowed_target_literals=list(rule.allowed_target_literals),
                allowed_target_regexes=list(rule.allowed_target_regexes),
                forbidden_target_literals=list(rule.forbidden_target_literals),
                forbidden_target_regexes=list(rule.forbidden_target_regexes),
                severity=rule.severity,
                prompt=rule.prompt,
                fallback=rule.fallback,
            )
            for rule in snapshot.usage_rules
        ],
    )


def validate_termbase_record_pair(
    *,
    source_text: str,
    target_text: str,
    snapshots: list[ApplicableTermbaseEntrySnapshot],
    chunk_id: str,
    record_id: str,
) -> list[Finding]:
    """Evaluate applicable termbase snapshots against one target text.

    Returns one Finding per non-clean evaluation. Findings use the rules
    ``termbase.forbidden_target`` and ``termbase.preferred_missing`` at the
    snapshot/rule severity. Error-severity findings block judge copy/edited
    output; advisory (warn/info) findings render as guidance only.
    """
    findings: list[Finding] = []
    for snapshot in snapshots:
        entry = _termbase_entry_from_snapshot(snapshot)
        evaluations = evaluate_entry_policy(
            target_text,
            entry,
            source_match=snapshot.matched_source_cue,
            source_span=snapshot.matched_source_span,
            occurrence_index=0,
        )
        for evaluation in evaluations:
            if evaluation.status == "clean":
                continue
            rule = (
                "termbase.forbidden_target"
                if evaluation.status == "forbidden_target"
                else "termbase.preferred_missing"
            )
            findings.append(
                Finding(
                    chunk_id=chunk_id,
                    severity=evaluation.severity,
                    rule=rule,
                    message=evaluation.reason,
                    record_id=record_id,
                )
            )
    return findings
