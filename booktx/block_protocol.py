"""Shared helpers for the durable translation block protocol."""

from __future__ import annotations

__all__ = [
    "SOURCE_ONLY_DIRECTIVE_PREFIXES",
    "source_only_directive_prefix",
]


SOURCE_ONLY_DIRECTIVE_PREFIXES = (
    "# glossary:",
    "# style:",
    "# termbase:",
)


def source_only_directive_prefix(line: str) -> str | None:
    """Return the reserved source-only directive prefix present in ``line``."""
    stripped = line.lstrip()
    for prefix in SOURCE_ONLY_DIRECTIVE_PREFIXES:
        if stripped.startswith(prefix):
            return prefix
    return None
