"""Shared bounded-run policy and text-budget helpers for judge tasks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from booktx.progress import count_words

DEFAULT_JUDGE_BATCH_RECORDS = 40
DEFAULT_JUDGE_BATCH_SENTENCES = 60
DEFAULT_JUDGE_BATCH_WORDS = 1800
DEFAULT_JUDGE_BATCH_RENDERED_LINES = 700


@dataclass(frozen=True, slots=True)
class JudgeBatchPolicy:
    """Composable limits applied while creating one judge task."""

    max_records: int | None = DEFAULT_JUDGE_BATCH_RECORDS
    max_sentences: int | None = DEFAULT_JUDGE_BATCH_SENTENCES
    max_words: int = DEFAULT_JUDGE_BATCH_WORDS
    max_rendered_lines: int | None = DEFAULT_JUDGE_BATCH_RENDERED_LINES

    @classmethod
    def from_todo(cls, todo: Any) -> JudgeBatchPolicy:
        return cls(
            max_records=getattr(todo, "batch_records", None),
            max_sentences=getattr(todo, "batch_sentences", None),
            max_words=int(getattr(todo, "batch_words", None) or todo.max_words),
            max_rendered_lines=getattr(todo, "batch_rendered_lines", None),
        )


def _visible_text(text: str) -> str:
    """Remove inline tags while preserving their surrounding word boundaries."""
    return re.sub(r"<[^>]*>", " ", text)


def count_sentences(text: str) -> int:
    """Count prose sentences without splitting records or XHTML.

    German closing quotes and ellipses are treated as part of the terminal
    punctuation. Common abbreviations are masked before counting so ``z. B.``
    and ``d. h.`` do not become two sentences.
    """
    visible = _visible_text(text).strip()
    if not visible:
        return 0
    masked = re.sub(r"\b(?:z\.\s*B\.|d\.\s*h\.|u\.\s*a\.|bzw\.|Dr\.)", "ABBR", visible)
    terminals = re.findall(r"[.!?]+(?=(?:[»”\"')\]]*\s|$))", masked)
    return max(1, len(terminals))


def count_record_sentences(record: Any) -> int:
    """Return the largest target sentence count among a record's candidates."""
    targets = [candidate.target for candidate in getattr(record, "candidates", [])]
    return max((count_sentences(target) for target in targets), default=0)


def count_candidates_sentences(candidates: list[Any]) -> int:
    return max(
        (count_sentences(candidate.target) for candidate in candidates),
        default=0,
    )


def count_record_words(record: Any) -> int:
    return count_words(getattr(record, "source", ""))
