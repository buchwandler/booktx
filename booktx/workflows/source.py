"""Domain workflow functions for source-record inspection (Phase 3 slice 2).

Read-only workflows that load and assemble source records, chapters, and the
status snapshot. The thin Typer commands in :mod:`booktx.commands.source`
delegate here. Not-found cases raise :class:`booktx.errors.BooktxError` so the
command layer can map them to a non-zero exit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from booktx.chapters import detect_chapters, load_chapter_map
from booktx.config import Project, load_manifest
from booktx.errors import BooktxError
from booktx.progress import SourceRecordView, load_source_records
from booktx.record_refs import parse_record_ref
from booktx.status import StatusBundle


@dataclass(frozen=True)
class ChapterSourceRecords:
    """One chapter's source records, ready for CLI rendering."""

    chapter_id: str
    title: str
    records: list[dict[str, str]]


def build_source_status_payload(proj: Project) -> dict[str, Any]:
    """Assemble the safe summary of extracted source state."""
    manifest = load_manifest(proj)
    source_records = load_source_records(proj)
    chapter_map = load_chapter_map(proj) or detect_chapters(proj)
    return {
        "source": "available" if proj.chunks() else "missing",
        "format": proj.config.format,
        "source_language": proj.config.source_language,
        "records": len(source_records),
        "chunks": len(proj.chunks()),
        "chapters": len(chapter_map.chapters),
        "source_sha256": manifest.source.sha256 if manifest is not None else "",
    }


def find_source_record(proj: Project, record_ref: str) -> SourceRecordView:
    """Resolve one source record by id or record ref; raise if unknown."""
    canonical_id = parse_record_ref(record_ref).canonical_id
    source_by_id = {record.record_id: record for record in load_source_records(proj)}
    record = source_by_id.get(canonical_id)
    if record is None:
        raise BooktxError(
            "unknown_source_record", f"unknown source record id: {canonical_id}"
        )
    return record


def collect_chapter_records(
    bundle: StatusBundle, chapter_id: str
) -> ChapterSourceRecords:
    """Collect all source records for one chapter from a status snapshot."""
    record_ids = bundle.index.record_ids_by_chapter.get(chapter_id)
    chapter = bundle.index.chapters_by_id.get(chapter_id)
    if not record_ids or chapter is None:
        raise BooktxError("unknown_chapter", f"unknown chapter id: {chapter_id}")
    records = [
        {"id": record_id, "source": bundle.index.source_by_id[record_id].source}
        for record_id in record_ids
    ]
    return ChapterSourceRecords(
        chapter_id=chapter.chapter_id, title=chapter.title, records=records
    )


__all__ = [
    "ChapterSourceRecords",
    "build_source_status_payload",
    "collect_chapter_records",
    "find_source_record",
]
