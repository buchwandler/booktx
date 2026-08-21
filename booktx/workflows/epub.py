"""Read-only workflow helpers for inspecting built EPUB content."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from booktx.chapters import load_chapter_map
from booktx.config import load_manifest
from booktx.epub_manifest import load_epub_template_from_manifest
from booktx.epub_verify import EpubArchive
from booktx.errors import BooktxError
from booktx.output_paths import OutputPathError, expected_epub_output_path

if TYPE_CHECKING:
    from booktx.config import Project

__all__ = [
    "EpubDocument",
    "resolve_epub_output_dir",
    "select_epub_documents",
    "select_xhtml_files",
]


@dataclass(frozen=True, slots=True)
class EpubDocument:
    """One XHTML document from an EPUB archive or compatibility directory."""

    name: str
    archive: EpubArchive | None = None
    path: Path | None = None

    def read_text(self) -> str:
        if self.archive is not None:
            return self.archive.read_text(self.name)
        if self.path is None:
            raise BooktxError("no_epub_xhtml", f"no readable XHTML source: {self.name}")
        return self.path.read_text("utf-8", errors="replace")


def resolve_epub_output_dir(proj: Project) -> Path:
    """Return the generated-output directory; raise if it does not exist."""
    output_dir = proj.output_dir
    if output_dir is None or not output_dir.is_dir():
        raise BooktxError(
            "no_epub_output",
            "no EPUB output directory; run `booktx build .` first. "
            f"Expected: translations/{proj.profile or '?'}/output/",
        )
    return output_dir


def select_xhtml_files(output_dir: Path, chapter: str | None = None) -> list[Path]:
    """Select loose XHTML files for legacy exploded-output compatibility."""
    xhtml_files = sorted(output_dir.glob("**/*.xhtml"))
    if not xhtml_files:
        raise BooktxError("no_epub_xhtml", f"no XHTML files found in {output_dir}")
    if chapter is not None:
        xhtml_files = [
            f
            for f in xhtml_files
            if f"chapter_{chapter}" in f.name or f"ch_{chapter}" in f.name
        ]
        if not xhtml_files:
            raise BooktxError(
                "no_epub_xhtml", f"no XHTML files found for chapter {chapter}"
            )
    return xhtml_files


def _chapter_id(project: Project, requested: str) -> str:
    """Resolve numeric chapter spellings such as ``1`` and ``0001``."""
    chapter_map = load_chapter_map(project)
    ids = (
        {chapter.chapter_id for chapter in chapter_map.chapters}
        if chapter_map
        else set()
    )
    if requested in ids:
        return requested
    if re.fullmatch(r"\d+", requested):
        canonical = f"{int(requested):04d}"
        if canonical in ids:
            return canonical
    return requested


def _chapter_hrefs(project: Project, requested: str) -> set[str]:
    """Return source EPUB document hrefs belonging to one chapter."""
    chapter_id = _chapter_id(project, requested)
    manifest = load_manifest(project)
    if manifest is None:
        raise BooktxError(
            "epub_chapter_metadata_missing",
            f"cannot resolve chapter {requested}: the EPUB manifest is missing",
        )
    template = load_epub_template_from_manifest(manifest)
    refs = template.spans
    hrefs = {
        ref.document_href
        for ref in refs
        if ref.chapter_id == chapter_id and ref.document_href
    }
    chapter_map = load_chapter_map(project)
    chapter = next(
        (
            item
            for item in (chapter_map.chapters if chapter_map else [])
            if item.chapter_id == chapter_id
        ),
        None,
    )
    if chapter is not None:
        from booktx.progress import load_source_records

        records = load_source_records(project)
        record_ids = [record.record_id for record in records]
        try:
            start = record_ids.index(chapter.start_record_id)
            end = record_ids.index(chapter.end_record_id)
        except ValueError:
            start = end = -1
        span_indexes = {
            record.span_index
            for record in records[start : end + 1]
            if start >= 0 and record.span_index is not None
        }
        hrefs.update(
            ref.document_href
            for ref in refs
            if ref.span_index in span_indexes and ref.document_href
        )
    if not hrefs:
        raise BooktxError(
            "no_epub_xhtml", f"no XHTML files found for chapter {requested}"
        )
    return hrefs


def select_epub_documents(
    proj: Project, chapter: str | None = None
) -> list[EpubDocument]:
    """Select XHTML documents from the built EPUB, without extracting it."""
    output_dir = resolve_epub_output_dir(proj)
    try:
        output_path = expected_epub_output_path(proj)
    except (OutputPathError, FileNotFoundError) as exc:
        raise BooktxError("no_epub_output", str(exc)) from exc

    if output_path.is_file():
        archive = EpubArchive.open(output_path)
        entries = archive.xhtml_entries()
        if not entries:
            raise BooktxError("no_epub_xhtml", f"no XHTML files found in {output_path}")
        if chapter is not None:
            hrefs = _chapter_hrefs(proj, chapter)
            entries = tuple(
                entry
                for entry in entries
                if any(archive.resolve_entry(href) == entry for href in hrefs)
            )
            if not entries:
                raise BooktxError(
                    "no_epub_xhtml", f"no XHTML files found for chapter {chapter}"
                )
        return [EpubDocument(name=entry, archive=archive) for entry in entries]

    return [
        EpubDocument(name=path.name, path=path)
        for path in select_xhtml_files(output_dir, chapter)
    ]
