"""Shared glossary diagnostic helpers for rich ``glossary_target_missing`` output.

Both :mod:`booktx.validate` and :mod:`booktx.judge_acceptance` call these
helpers so glossary findings carry the same actionable context regardless of
whether the failure originates from ``translate insert``, ``judge insert``,
``validate``, or ``lint-block``.
"""

from __future__ import annotations

import re

from booktx.context import GlossaryEntry
from booktx.glossary_match import TermSpan, source_glossary_matches

__all__ = [
    "source_phrase_window",
    "detect_phrase_collision",
    "format_glossary_missing_message",
]


# Categories that the phrase-collision heuristic considers "short-term" when
# the matched source term is short and appears to be used as a modifier.
_PHRASE_COLLISION_CATEGORIES = frozenset({"insect", "kinden", "people", "term", ""})

# Words in glossary notes that suggest the entry is about a standalone sense
# rather than a compound modifier.
_STANDALONE_NOTE_MARKERS = frozenset(
    {"standalone", "compound", "unaffected", "compounds"}
)
_SOURCE_TOKEN_RE = re.compile(r"[^\W\d_]+(?:[-'][^\W\d_]+)*", re.UNICODE)
_LEFT_MODIFIER_STOPWORDS = frozenset({"A", "An", "The"})


def _capitalized_left_phrase(matched: TermSpan, source_text: str) -> str | None:
    """Extend a match over adjacent capitalized modifiers on its left."""
    prefix = source_text[: matched.start]
    cursor = len(prefix)
    modifiers: list[str] = []
    while cursor:
        token_match = None
        for candidate in _SOURCE_TOKEN_RE.finditer(prefix, 0, cursor):
            token_match = candidate
        if token_match is None or prefix[token_match.end() : cursor].strip():
            break
        components = re.split(r"[-']", token_match.group())
        if token_match.group() in _LEFT_MODIFIER_STOPWORDS or not all(
            component and component[0].isupper() for component in components
        ):
            break
        modifiers.append(token_match.group())
        cursor = token_match.start()
    if not modifiers:
        return None
    modifiers.reverse()
    return " ".join([*modifiers, matched.matched_term])


def source_phrase_window(
    source_text: str, start: int, end: int, *, context_chars: int = 60
) -> str:
    """Return a snippet of *source_text* around the matched span.

    The snippet includes up to ``context_chars`` visible characters before and
    after the match.  If the window reaches the start/end of the string the
    corresponding ellipsis is omitted.
    """
    window_start = max(0, start - context_chars)
    window_end = min(len(source_text), end + context_chars)
    prefix = "..." if window_start > 0 else ""
    suffix = "..." if window_end < len(source_text) else ""
    return prefix + source_text[window_start:window_end] + suffix


def detect_phrase_collision(
    matched: TermSpan,
    source_text: str,
    entry: GlossaryEntry,
    glossary: list[GlossaryEntry],
) -> str | None:
    """Detect a possible source-phrase collision and return a hint, or ``None``.

    A collision is likely when:

    1. The matched source term is short (≤ 2 whitespace-delimited tokens).
    2. A plausible capitalized modifier precedes the match, or another source
       token follows immediately after it.
    3. No *longer* glossary entry shadowed this short span.
    4. The entry category is one of the "short-term" categories.
    5. Either the entry notes mention standalone/compound/unaffected words,
       or the matched token starts with an uppercase letter inside prose
       (i.e. is not the first token of the sentence).
    """
    matched_tokens = matched.matched_term.split()
    if len(matched_tokens) > 2:
        return None

    # Check no longer entry shadowed this span.
    all_spans = source_glossary_matches(source_text, glossary)
    for other in all_spans:
        if (
            other.entry_index != matched.entry_index
            and not other.shadowed
            and other.start <= matched.start
            and other.end >= matched.end
            and (other.end - other.start) > (matched.end - matched.start)
        ):
            # A longer non-shadowed entry already covers this span.
            return None

    if entry.category not in _PHRASE_COLLISION_CATEGORIES:
        return None

    # Check notes or uppercase-in-prose.
    notes_lower = entry.notes.lower()
    has_standalone_marker = any(
        marker in notes_lower for marker in _STANDALONE_NOTE_MARKERS
    )
    starts_upper = matched.matched_term[0].isupper() if matched.matched_term else False
    # The matched token is inside prose (not at position 0) and starts uppercase.
    is_upper_in_prose = starts_upper and matched.start > 0

    if not has_standalone_marker and not is_upper_in_prose:
        return None

    left_phrase = _capitalized_left_phrase(matched, source_text)
    if left_phrase is not None:
        return (
            f"possible phrase collision: `{matched.matched_term}` is contained "
            f"in `{left_phrase}`. A longer glossary entry for `{left_phrase}` "
            f"would shadow the shorter `{entry.source}` rule."
        )

    # Fall back to a following token when there is no plausible left modifier.
    rest = source_text[matched.end :]
    if not rest or not rest[0].isspace():
        return None
    stripped = rest.lstrip()
    if not stripped or not stripped[0].isalnum():
        return None

    # Build the phrase excerpt for the hint.
    # Find the next token boundaries.
    after_start = matched.end + len(rest) - len(stripped)
    next_token_end = after_start
    for i, ch in enumerate(stripped):
        if ch.isspace():
            next_token_end = after_start + i
            break
    else:
        next_token_end = len(source_text)
    phrase_context = source_text[max(0, matched.start) : next_token_end]

    return (
        f"possible phrase collision: `{matched.matched_term}` is used as a "
        f"modifier in `{phrase_context}`. "
        f"A longer glossary entry for `{phrase_context}` would shadow the "
        f"shorter `{entry.source}` rule."
    )


def format_glossary_missing_message(
    *,
    entry: GlossaryEntry,
    approved: list[str],
    matched: TermSpan,
    phrase_excerpt: str,
    source: str,
    target: str,
    glossary: list[GlossaryEntry] | None = None,
) -> str:
    """Format a rich ``glossary_target_missing`` message with actionable context.

    Returns a multi-line message that includes the matched source span, approved
    targets, glossary notes, an optional phrase-collision hint, and the full
    source/target record pair.
    """
    parts: list[str] = []
    parts.append(
        f"source term `{entry.source}` matched `{matched.matched_term}` "
        f"in source phrase `{phrase_excerpt}`."
    )
    parts.append(f"approved target missing: {' / '.join(approved)}")
    if entry.notes:
        parts.append(f"glossary note: {entry.notes}")

    hint = detect_phrase_collision(matched, source, entry, glossary or [])
    if hint:
        parts.append(f"hint: {hint}")

    parts.append(f"source: {source}")
    parts.append(f"target: {target}")
    return "\n".join(parts)
