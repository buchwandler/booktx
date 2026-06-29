"""Domain workflow functions for built-EPUB XHTML inspection (Phase 3 slice 3).

Read-only helpers that resolve the EPUB output directory and select XHTML files
(chapter-filtered). Not-found cases raise :class:`booktx.errors.BooktxError`.
File reading and rendering stay in the command layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from booktx.errors import BooktxError

if TYPE_CHECKING:
    from booktx.config import Project


def resolve_epub_output_dir(proj: Project) -> Path:
    """Return the built-EPUB output directory; raise if it does not exist."""
    output_dir = proj.output_dir
    if output_dir is None or not output_dir.is_dir():
        raise BooktxError(
            "no_epub_output",
            "no EPUB output directory; run `booktx build .` first. "
            f"Expected: translations/{proj.profile or '?'}/output/",
        )
    return output_dir


def select_xhtml_files(output_dir: Path, chapter: str | None = None) -> list[Path]:
    """Select XHTML files under ``output_dir``, optionally filtered by chapter.

    Mirrors the original command's two-stage check: first fail when the output
    dir has no XHTML at all, then fail when the chapter filter matches nothing.
    """
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


__all__ = ["resolve_epub_output_dir", "select_xhtml_files"]
