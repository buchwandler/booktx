"""Shared source and target matching for the translation preference dictionary."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from booktx.lexicon import LexiconEntry

__all__ = [
    "LexiconSourceSpan",
    "entry_allowed_hits",
    "entry_preferred_absence",
    "entry_preferred_hits",
    "entry_source_matches",
    "entry_target_forbidden_hits",
    "iter_boundary_matches",
    "lexicon_source_matches",
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
class LexiconSourceSpan:
    entry_id: str
    source_match: str
    source_span: tuple[int, int]
    cue_order: int
    shadowed: bool = False


def _candidate_spans(text: str, entry: LexiconEntry) -> list[LexiconSourceSpan]:
    candidates: dict[tuple[int, int], LexiconSourceSpan] = {}
    cue_order = 0
    cues = [entry.source, *entry.source_variants]
    for cue in cues:
        for match in iter_boundary_matches(
            text, cue, case_sensitive=entry.case_sensitive
        ):
            key = (match.start(), match.end())
            span = LexiconSourceSpan(
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
            span = LexiconSourceSpan(
                entry_id=entry.id,
                source_match=match.group(0),
                source_span=(match.start(), match.end()),
                cue_order=cue_order,
            )
            existing = candidates.get(key)
            if existing is None or span.cue_order < existing.cue_order:
                candidates[key] = span
    return list(candidates.values())


def entry_source_matches(text: str, entry: LexiconEntry) -> list[LexiconSourceSpan]:
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


def lexicon_source_matches(
    text: str, entries: list[LexiconEntry]
) -> list[LexiconSourceSpan]:
    """Return source matches with deterministic longest-span shadowing."""
    candidates: list[LexiconSourceSpan] = []
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
    accepted: list[LexiconSourceSpan] = []
    result: list[LexiconSourceSpan] = []
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


def entry_target_forbidden_hits(target_text: str, entry: LexiconEntry) -> list[str]:
    """Return literal and regex forbidden-target hits in declared order."""
    hits: list[str] = []
    for term in entry.target_forbidden:
        if iter_boundary_matches(
            target_text, term, case_sensitive=entry.case_sensitive
        ):
            hits.append(term)
    flags = re.UNICODE if entry.case_sensitive else re.UNICODE | re.IGNORECASE
    for pattern in entry.target_regex_forbidden:
        for match in re.finditer(pattern, target_text, flags):
            hits.append(match.group(0))
    return _dedupe_hits(hits)


def entry_preferred_hits(target_text: str, entry: LexiconEntry) -> list[str]:
    """Return preferred target expressions present in the target text."""
    return _dedupe_hits(
        [
            term
            for term in entry.target_preferred
            if iter_boundary_matches(
                target_text, term, case_sensitive=entry.case_sensitive
            )
        ]
    )


def entry_allowed_hits(target_text: str, entry: LexiconEntry) -> list[str]:
    """Return allowed target expressions present in the target text."""
    return _dedupe_hits(
        [
            term
            for term in entry.target_allowed
            if iter_boundary_matches(
                target_text, term, case_sensitive=entry.case_sensitive
            )
        ]
    )


def entry_preferred_absence(
    target_text: str, entry: LexiconEntry
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
