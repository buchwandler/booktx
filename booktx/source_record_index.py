"""Source-only record/chapter/order indexes for bounded workflow reads."""

from __future__ import annotations

from dataclasses import dataclass

from booktx.chapters import ChapterMap, ensure_chapter_map
from booktx.config import Project
from booktx.models import Chunk
from booktx.progress import SourceRecordView, load_source_chunks, load_source_records

__all__ = ["SourceRecordIndex", "build_source_record_index"]


@dataclass(slots=True)
class SourceRecordIndex:
    """Source-derived record/chapter ordering without store access."""

    chapter_map: ChapterMap
    source_chunks: dict[str, Chunk]
    ordered_records: list[SourceRecordView]
    ordered_record_ids: list[str]
    source_by_id: dict[str, SourceRecordView]
    record_ids_by_chapter: dict[str, list[str]]
    record_to_chapter: dict[str, str]


def build_source_record_index(
    project: Project,
    *,
    source_chunks: dict[str, Chunk] | None = None,
    source_records: list[SourceRecordView] | None = None,
    chapter_map: ChapterMap | None = None,
) -> SourceRecordIndex:
    """Build the source-only record/chapter ordering index for ``project``."""

    if source_chunks is None:
        source_chunks = {chunk.chunk_id: chunk for chunk in load_source_chunks(project)}
    if source_records is None:
        source_records = load_source_records(project)
    if chapter_map is None:
        chapter_map = ensure_chapter_map(project)

    source_by_id = {record.record_id: record for record in source_records}
    ordered_record_ids = [record.record_id for record in source_records]
    record_index_by_id = {
        record_id: idx for idx, record_id in enumerate(ordered_record_ids)
    }
    record_ids_by_chapter: dict[str, list[str]] = {}
    record_to_chapter: dict[str, str] = {}

    for chapter in chapter_map.chapters:
        start = record_index_by_id.get(chapter.start_record_id)
        end = record_index_by_id.get(chapter.end_record_id)
        if start is None or end is None or end < start:
            chapter_record_ids: list[str] = []
        else:
            chapter_record_ids = ordered_record_ids[start : end + 1]
        record_ids_by_chapter[chapter.chapter_id] = chapter_record_ids
        for record_id in chapter_record_ids:
            record_to_chapter[record_id] = chapter.chapter_id

    return SourceRecordIndex(
        chapter_map=chapter_map,
        source_chunks=source_chunks,
        ordered_records=source_records,
        ordered_record_ids=ordered_record_ids,
        source_by_id=source_by_id,
        record_ids_by_chapter=record_ids_by_chapter,
        record_to_chapter=record_to_chapter,
    )
