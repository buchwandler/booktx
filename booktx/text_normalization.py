"""Safe normalization helpers for user-submitted translation text."""

from __future__ import annotations

GERMAN_OPEN_QUOTE = "\u201e"  # „
GERMAN_CLOSE_QUOTE = "\u201c"  # “
ASCII_DOUBLE_QUOTE = '"'


def normalize_german_closing_quotes(text: str) -> str:
    """Normalize accidental ASCII closers in German-quoted prose.

    Some editing surfaces preserve the German opening quote ``„`` but downgrade
    the matching German closing quote ``“`` to ASCII ``"``. This creates noisy
    ``outer_quotation_marks_preserved`` failures even though the translator's
    intent is unambiguous.

    Only rewrite ASCII double quotes when the target already contains at least
    one German low-9 opening quote. Replacements are skipped inside XHTML tags
    so attributes are never corrupted.
    """
    if GERMAN_OPEN_QUOTE not in text or ASCII_DOUBLE_QUOTE not in text:
        return text

    out: list[str] = []
    in_tag = False
    for ch in text:
        if ch == "<":
            in_tag = True
            out.append(ch)
            continue
        if ch == ">" and in_tag:
            in_tag = False
            out.append(ch)
            continue
        if ch == ASCII_DOUBLE_QUOTE and not in_tag:
            out.append(GERMAN_CLOSE_QUOTE)
        else:
            out.append(ch)
    return "".join(out)


def normalize_submitted_target(text: str) -> str:
    """Normalize one submitted target before validation and persistence."""
    return normalize_german_closing_quotes(text)
