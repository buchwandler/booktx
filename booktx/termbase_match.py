"""Shared source matching and target policy evaluation for the canonical termbase."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from booktx.termbase import TermbaseEntry, TermbaseUsageRule, deterministic_context_id

__all__ = [
    "TermbaseRuleEvaluation",
    "TermbaseSourceSpan",
    "entry_allowed_hits",
    "entry_preferred_absence",
    "entry_preferred_hits",
    "entry_source_matches",
    "entry_target_forbidden_hits",
    "evaluate_entry_policy",
    "iter_boundary_matches",
    "matching_usage_rules",
    "termbase_source_matches",
]


def _edge_prefix(term: str) -> str:
    return r"(?<!\w)" if term[0].isalnum() or term[0] == "_" else ""


def _edge_suffix(term: str) -> str:
    return r"(?!\w)" if term[-1].isalnum() or term[-1] == "_" else ""


def iter_boundary_matches(
    text: str, term: str, *, case_sensitive: bool
) -> list[re.Match[str]]:
    """Return boundary-aware literal matches for ``term`` in ``text``."""
    cleaned = term.strip()
    if not cleaned:
        return []
    flags = re.UNICODE if case_sensitive else re.UNICODE | re.IGNORECASE
    pattern = f"{_edge_prefix(cleaned)}{re.escape(cleaned)}{_edge_suffix(cleaned)}"
    return list(re.finditer(pattern, text, flags))


@dataclass(frozen=True, slots=True)
class TermbaseSourceSpan:
    entry_id: str
    source_match: str
    source_span: tuple[int, int]
    cue_order: int
    shadowed: bool = False


@dataclass(frozen=True, slots=True)
class TermbaseRuleEvaluation:
    entry_id: str
    rule_id: str
    context_id: str
    source_match: str
    source_span: tuple[int, int]
    status: str
    severity: str
    reason: str
    required_target_found: list[str]
    allowed_target_found: list[str]
    forbidden_target_found: list[str]
    prompt: str
    fallback: bool


@dataclass(frozen=True, slots=True)
class _TargetHit:
    text: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class _ClassifiedTargetHit:
    category: str
    text: str
    start: int
    end: int


def _candidate_spans(text: str, entry: TermbaseEntry) -> list[TermbaseSourceSpan]:
    candidates: dict[tuple[int, int], TermbaseSourceSpan] = {}
    cue_order = 0
    cues = [entry.source, *entry.source_variants]
    for cue in cues:
        for match in iter_boundary_matches(
            text, cue, case_sensitive=entry.case_sensitive
        ):
            key = (match.start(), match.end())
            span = TermbaseSourceSpan(
                entry_id=entry.id,
                source_match=match.group(0),
                source_span=(match.start(), match.end()),
                cue_order=cue_order,
            )
            existing = candidates.get(key)
            if existing is None or span.cue_order < existing.cue_order:
                candidates[key] = span
        cue_order += 1
    if entry.source_regex is not None:
        flags = re.UNICODE if entry.case_sensitive else re.UNICODE | re.IGNORECASE
        for match in re.finditer(entry.source_regex, text, flags):
            key = (match.start(), match.end())
            span = TermbaseSourceSpan(
                entry_id=entry.id,
                source_match=match.group(0),
                source_span=(match.start(), match.end()),
                cue_order=cue_order,
            )
            existing = candidates.get(key)
            if existing is None or span.cue_order < existing.cue_order:
                candidates[key] = span
    return list(candidates.values())


def entry_source_matches(text: str, entry: TermbaseEntry) -> list[TermbaseSourceSpan]:
    """Return one entry's source matches with same-entry span deduplication."""
    return sorted(
        _candidate_spans(text, entry),
        key=lambda span: (
            span.source_span[0],
            span.source_span[1],
            span.entry_id,
            span.cue_order,
        ),
    )


def termbase_source_matches(
    text: str, entries: list[TermbaseEntry]
) -> list[TermbaseSourceSpan]:
    """Return source matches with deterministic longest-span shadowing."""
    candidates: list[TermbaseSourceSpan] = []
    for entry in entries:
        candidates.extend(entry_source_matches(text, entry))
    ordered = sorted(
        candidates,
        key=lambda span: (
            span.source_span[0],
            -(span.source_span[1] - span.source_span[0]),
            span.entry_id,
            span.cue_order,
        ),
    )
    accepted: list[TermbaseSourceSpan] = []
    result: list[TermbaseSourceSpan] = []
    for span in ordered:
        contained = any(
            span.source_span[0] >= chosen.source_span[0]
            and span.source_span[1] <= chosen.source_span[1]
            for chosen in accepted
        )
        if contained:
            result.append(replace(span, shadowed=True))
            continue
        accepted.append(span)
        result.append(span)
    return sorted(
        result,
        key=lambda span: (
            span.source_span[0],
            span.source_span[1],
            span.entry_id,
            span.cue_order,
        ),
    )


def _dedupe_hits(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _dedupe_target_hits(values: list[_TargetHit]) -> list[_TargetHit]:
    seen: set[tuple[str, int, int]] = set()
    result: list[_TargetHit] = []
    for value in values:
        key = (value.text, value.start, value.end)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _literal_hits(text: str, values: list[str], *, case_sensitive: bool) -> list[str]:
    hits: list[str] = []
    for value in values:
        if iter_boundary_matches(text, value, case_sensitive=case_sensitive):
            hits.append(value)
    return _dedupe_hits(hits)


def _regex_hits(text: str, values: list[str], *, case_sensitive: bool) -> list[str]:
    flags = re.UNICODE if case_sensitive else re.UNICODE | re.IGNORECASE
    hits: list[str] = []
    for pattern in values:
        for match in re.finditer(pattern, text, flags):
            hits.append(match.group(0))
    return _dedupe_hits(hits)


def _literal_target_hits(
    text: str, values: list[str], *, case_sensitive: bool
) -> list[_TargetHit]:
    hits: list[_TargetHit] = []
    for value in values:
        for match in iter_boundary_matches(text, value, case_sensitive=case_sensitive):
            hits.append(_TargetHit(match.group(0), match.start(), match.end()))
    return _dedupe_target_hits(
        sorted(hits, key=lambda item: (item.start, -(item.end - item.start), item.text))
    )


def _regex_target_hits(
    text: str, values: list[str], *, case_sensitive: bool
) -> list[_TargetHit]:
    flags = re.UNICODE if case_sensitive else re.UNICODE | re.IGNORECASE
    hits: list[_TargetHit] = []
    for pattern in values:
        for match in re.finditer(pattern, text, flags):
            hits.append(_TargetHit(match.group(0), match.start(), match.end()))
    return _dedupe_target_hits(
        sorted(hits, key=lambda item: (item.start, -(item.end - item.start), item.text))
    )


def _occurrence_hits(
    text: str,
    literal_values: list[str],
    regex_values: list[str],
    *,
    case_sensitive: bool,
    occurrence_index: int | None,
) -> list[str]:
    hits = _dedupe_target_hits(
        _literal_target_hits(text, literal_values, case_sensitive=case_sensitive)
        + _regex_target_hits(text, regex_values, case_sensitive=case_sensitive)
    )
    hits = sorted(hits, key=lambda item: (item.start, item.end, item.text))
    if occurrence_index is None:
        return [hit.text for hit in hits]
    if occurrence_index >= len(hits):
        return []
    return [hits[occurrence_index].text]


def _classified_occurrence_hits(
    target_text: str,
    *,
    required_literals: list[str],
    required_regexes: list[str],
    allowed_literals: list[str],
    allowed_regexes: list[str],
    forbidden_literals: list[str],
    forbidden_regexes: list[str],
    case_sensitive: bool,
    occurrence_index: int | None,
) -> tuple[list[str], list[str], list[str]]:
    hits: list[_ClassifiedTargetHit] = []
    for hit in _literal_target_hits(
        target_text, required_literals, case_sensitive=case_sensitive
    ) + _regex_target_hits(
        target_text, required_regexes, case_sensitive=case_sensitive
    ):
        hits.append(
            _ClassifiedTargetHit(
                category="required", text=hit.text, start=hit.start, end=hit.end
            )
        )
    for hit in _literal_target_hits(
        target_text, allowed_literals, case_sensitive=case_sensitive
    ) + _regex_target_hits(target_text, allowed_regexes, case_sensitive=case_sensitive):
        hits.append(
            _ClassifiedTargetHit(
                category="allowed", text=hit.text, start=hit.start, end=hit.end
            )
        )
    for hit in _literal_target_hits(
        target_text, forbidden_literals, case_sensitive=case_sensitive
    ) + _regex_target_hits(
        target_text, forbidden_regexes, case_sensitive=case_sensitive
    ):
        hits.append(
            _ClassifiedTargetHit(
                category="forbidden", text=hit.text, start=hit.start, end=hit.end
            )
        )
    ordered = sorted(
        hits,
        key=lambda item: (
            item.start,
            -(item.end - item.start),
            item.category,
            item.text,
        ),
    )
    if occurrence_index is None:
        selected = ordered
    else:
        occurrence_keys: list[tuple[int, int, str]] = []
        for hit in ordered:
            key = (hit.start, hit.end, hit.text)
            if key not in occurrence_keys:
                occurrence_keys.append(key)
        if occurrence_index >= len(occurrence_keys):
            selected = []
        else:
            chosen = occurrence_keys[occurrence_index]
            selected = [
                hit for hit in ordered if (hit.start, hit.end, hit.text) == chosen
            ]
    required = [hit.text for hit in selected if hit.category == "required"]
    allowed = [hit.text for hit in selected if hit.category == "allowed"]
    forbidden = [hit.text for hit in selected if hit.category == "forbidden"]
    return required, allowed, forbidden


def entry_target_forbidden_hits(target_text: str, entry: TermbaseEntry) -> list[str]:
    """Return literal and regex forbidden-target hits in declared order."""
    return _dedupe_hits(
        _literal_hits(
            target_text, entry.target_forbidden, case_sensitive=entry.case_sensitive
        )
        + _regex_hits(
            target_text,
            entry.target_regex_forbidden,
            case_sensitive=entry.case_sensitive,
        )
    )


def entry_preferred_hits(target_text: str, entry: TermbaseEntry) -> list[str]:
    """Return preferred target expressions present in the target text."""
    return _literal_hits(
        target_text, entry.target_preferred, case_sensitive=entry.case_sensitive
    )


def entry_allowed_hits(target_text: str, entry: TermbaseEntry) -> list[str]:
    """Return allowed target expressions present in the target text."""
    return _literal_hits(
        target_text, entry.target_allowed, case_sensitive=entry.case_sensitive
    )


def entry_preferred_absence(
    target_text: str, entry: TermbaseEntry
) -> tuple[list[str], bool]:
    """Return preferred hits and whether absence should be reported."""
    preferred_hits = entry_preferred_hits(target_text, entry)
    if entry.preferred_policy == "off":
        return preferred_hits, False
    if preferred_hits:
        return preferred_hits, False
    if not entry.target_preferred:
        return preferred_hits, False
    if entry_allowed_hits(target_text, entry):
        return preferred_hits, False
    return preferred_hits, True


def _rule_applies(
    source_match: str, entry: TermbaseEntry, rule: TermbaseUsageRule
) -> bool:
    if rule.fallback:
        return False
    if rule.source_cue is not None:
        if iter_boundary_matches(
            source_match, rule.source_cue, case_sensitive=entry.case_sensitive
        ):
            return True
    if rule.source_regex is not None:
        flags = re.UNICODE if entry.case_sensitive else re.UNICODE | re.IGNORECASE
        return bool(re.search(rule.source_regex, source_match, flags))
    return False


def matching_usage_rules(
    source_match: str, entry: TermbaseEntry
) -> list[TermbaseUsageRule]:
    """Return the applicable contextual usage rules in declaration order."""
    if entry.kind != "contextual_term":
        return []
    specific: list[TermbaseUsageRule] = []
    fallback: TermbaseUsageRule | None = None
    for rule in entry.usage_rules:
        if rule.fallback:
            fallback = rule
            continue
        if _rule_applies(source_match, entry, rule):
            specific.append(rule)
    if specific:
        return specific
    return [fallback] if fallback is not None else []


def _classify_flat_entry(
    target_text: str,
    entry: TermbaseEntry,
    *,
    source_match: str,
    source_span: tuple[int, int],
    occurrence_index: int | None,
) -> TermbaseRuleEvaluation:
    preferred_hits, allowed_hits, forbidden_hits = _classified_occurrence_hits(
        target_text,
        required_literals=entry.target_preferred,
        required_regexes=[],
        allowed_literals=entry.target_allowed,
        allowed_regexes=[],
        forbidden_literals=entry.target_forbidden,
        forbidden_regexes=entry.target_regex_forbidden,
        case_sensitive=entry.case_sensitive,
        occurrence_index=occurrence_index,
    )
    preferred_missing = False
    if entry.preferred_policy != "off" and entry.target_preferred:
        preferred_missing = not (preferred_hits or allowed_hits)
    if forbidden_hits:
        return TermbaseRuleEvaluation(
            entry_id=entry.id,
            rule_id="flat-term",
            context_id=deterministic_context_id(entry.id, "flat-term"),
            source_match=source_match,
            source_span=source_span,
            status="forbidden_target",
            severity=entry.severity,
            reason="effective target contains a forbidden termbase expression",
            required_target_found=preferred_hits,
            allowed_target_found=allowed_hits,
            forbidden_target_found=forbidden_hits,
            prompt=entry.rationale,
            fallback=False,
        )
    if preferred_missing:
        severity = "warn" if entry.preferred_policy == "advisory" else entry.severity
        return TermbaseRuleEvaluation(
            entry_id=entry.id,
            rule_id="flat-term",
            context_id=deterministic_context_id(entry.id, "flat-term"),
            source_match=source_match,
            source_span=source_span,
            status="preferred_missing",
            severity=severity,
            reason="effective target lacks a preferred or allowed termbase expression",
            required_target_found=preferred_hits,
            allowed_target_found=allowed_hits,
            forbidden_target_found=[],
            prompt=entry.rationale,
            fallback=False,
        )
    return TermbaseRuleEvaluation(
        entry_id=entry.id,
        rule_id="flat-term",
        context_id=deterministic_context_id(entry.id, "flat-term"),
        source_match=source_match,
        source_span=source_span,
        status="clean",
        severity="info",
        reason="effective target satisfies the applicable termbase policy",
        required_target_found=preferred_hits,
        allowed_target_found=allowed_hits,
        forbidden_target_found=[],
        prompt=entry.rationale,
        fallback=False,
    )


def _classify_usage_rule(
    target_text: str,
    entry: TermbaseEntry,
    rule: TermbaseUsageRule,
    *,
    source_match: str,
    source_span: tuple[int, int],
    occurrence_index: int | None,
) -> TermbaseRuleEvaluation:
    required_hits, allowed_hits, forbidden_hits = _classified_occurrence_hits(
        target_text,
        required_literals=rule.required_target_literals,
        required_regexes=rule.required_target_regexes,
        allowed_literals=rule.allowed_target_literals,
        allowed_regexes=rule.allowed_target_regexes,
        forbidden_literals=rule.forbidden_target_literals,
        forbidden_regexes=rule.forbidden_target_regexes,
        case_sensitive=entry.case_sensitive,
        occurrence_index=occurrence_index,
    )
    missing_required = bool(
        rule.required_target_literals or rule.required_target_regexes
    ) and not (required_hits or allowed_hits)
    if forbidden_hits:
        return TermbaseRuleEvaluation(
            entry_id=entry.id,
            rule_id=rule.id,
            context_id=rule.context_id,
            source_match=source_match,
            source_span=source_span,
            status="forbidden_target",
            severity=rule.severity,
            reason=(
                "effective target contains a forbidden termbase usage-rule expression"
            ),
            required_target_found=required_hits,
            allowed_target_found=allowed_hits,
            forbidden_target_found=forbidden_hits,
            prompt=rule.prompt,
            fallback=rule.fallback,
        )
    if missing_required:
        return TermbaseRuleEvaluation(
            entry_id=entry.id,
            rule_id=rule.id,
            context_id=rule.context_id,
            source_match=source_match,
            source_span=source_span,
            status="preferred_missing",
            severity=rule.severity,
            reason=(
                "effective target lacks a required or allowed "
                "termbase usage-rule expression"
            ),
            required_target_found=required_hits,
            allowed_target_found=allowed_hits,
            forbidden_target_found=[],
            prompt=rule.prompt,
            fallback=rule.fallback,
        )
    return TermbaseRuleEvaluation(
        entry_id=entry.id,
        rule_id=rule.id,
        context_id=rule.context_id,
        source_match=source_match,
        source_span=source_span,
        status="clean",
        severity="info",
        reason="effective target satisfies the applicable termbase usage rule",
        required_target_found=required_hits,
        allowed_target_found=allowed_hits,
        forbidden_target_found=[],
        prompt=rule.prompt,
        fallback=rule.fallback,
    )


def evaluate_entry_policy(
    target_text: str,
    entry: TermbaseEntry,
    *,
    source_match: str,
    source_span: tuple[int, int],
    occurrence_index: int | None = 0,
) -> list[TermbaseRuleEvaluation]:
    """Evaluate one matched entry occurrence against the target text."""
    if entry.kind != "contextual_term":
        return [
            _classify_flat_entry(
                target_text,
                entry,
                source_match=source_match,
                source_span=source_span,
                occurrence_index=occurrence_index,
            )
        ]
    rules = matching_usage_rules(source_match, entry)
    if not rules:
        return [
            TermbaseRuleEvaluation(
                entry_id=entry.id,
                rule_id="no-rule",
                context_id=deterministic_context_id(entry.id, source_match, "no-rule"),
                source_match=source_match,
                source_span=source_span,
                status="clean",
                severity="info",
                reason="no contextual usage rule applies to this source occurrence",
                required_target_found=[],
                allowed_target_found=[],
                forbidden_target_found=[],
                prompt="",
                fallback=False,
            )
        ]
    return [
        _classify_usage_rule(
            target_text,
            entry,
            rule,
            source_match=source_match,
            source_span=source_span,
            occurrence_index=occurrence_index,
        )
        for rule in rules
    ]
