"""Shared progress and record-loading helpers for the command workflow."""

from __future__ import annotations

import re
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field

from booktx.config import Project
from booktx.models import Chunk, Placeholder

__all__ = [
    "WORD_RE",
    "SourceRecordView",
    "RecordProgress",
    "ChunkProgress",
    "ChapterProgress",
    "count_words",
    "source_record_sha256",
    "load_source_chunks",
    "load_source_records",
]


WORD_RE = re.compile(
    r"[^\W\d_]+(?:['’\-][^\W\d_]+)*|\d+(?:[.,]\d+)?",
    re.UNICODE,
)


class SourceRecordView(BaseModel):
    """Flattened source-record view used by progress and submission logic."""

    model_config = ConfigDict(extra="forbid")

    record_id: str
    chunk_id: str
    source: str
    protected_terms: list[str] = Field(default_factory=list)
    placeholders: list[Placeholder] = Field(default_factory=list)
    source_words: int = 0
    source_sha256: str = ""
    span_index: int | None = None
    span_record_index: int | None = None
    block_id: str | None = None


class RecordProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str
    chunk_id: str
    chapter_id: str | None = None
    source: str
    source_words: int
    target_words: int = 0
    translated: bool = False
    valid: bool = False
    invalid_reason: str | None = None


class ChunkProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    records_total: int
    records_translated: int
    records_remaining: int
    source_words_total: int
    source_words_translated: int
    source_words_remaining: int
    status: str


class ChapterProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: str
    title: str
    chunk_ids: list[str] = Field(default_factory=list)
    pending_chunk_ids: list[str] = Field(default_factory=list)
    start_record_id: str
    end_record_id: str
    records_total: int
    records_translated: int
    records_remaining: int
    source_words_total: int
    source_words_translated: int
    source_words_remaining: int
    status: str


def count_words(text: str) -> int:
    """Return a deterministic local word count for ``text``."""
    return len(WORD_RE.findall(text))


def source_record_sha256(text: str) -> str:
    """Return the SHA256 of one source-record text."""
    return sha256(text.encode("utf-8")).hexdigest()


def load_source_chunks(project: Project) -> list[Chunk]:
    """Load source chunks in chunk-id order."""
    chunks = [
        Chunk.model_validate_json(path.read_text("utf-8")) for path in project.chunks()
    ]
    chunks.sort(key=lambda chunk: chunk.chunk_id)
    return chunks


def load_source_records(project: Project) -> list[SourceRecordView]:
    """Load all source records as flattened views."""
    out: list[SourceRecordView] = []
    for chunk in load_source_chunks(project):
        for record in chunk.records:
            out.append(
                SourceRecordView(
                    record_id=record.id,
                    chunk_id=chunk.chunk_id,
                    source=record.source,
                    protected_terms=list(record.protected_terms),
                    placeholders=list(record.placeholders),
                    source_words=count_words(record.source),
                    source_sha256=source_record_sha256(record.source),
                    span_index=getattr(record, "span_index", None),
                    span_record_index=getattr(record, "span_record_index", None),
                    block_id=getattr(record, "block_id", None),
                )
            )
    return out
