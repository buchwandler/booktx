"""Shared paths for generated project outputs."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from booktx.config import find_source_file

if TYPE_CHECKING:
    from booktx.config import Project

__all__ = [
    "OutputPathError",
    "expected_epub_output_path",
    "output_path",
]


class OutputPathError(ValueError):
    """The project cannot resolve a configured generated-output directory."""


def output_path(project: Project, source: Path, *, suffix: str) -> Path:
    """Return the configured output path for ``source``."""
    if project.output_dir is None:
        raise OutputPathError("Output directory is not configured.")
    if project.config.output_filename:
        return project.output_dir / project.config.output_filename
    return project.output_dir / (
        f"{source.stem}.{project.config.target_language}{suffix}"
    )


def expected_epub_output_path(project: Project) -> Path:
    """Return the expected EPUB artifact path without mutating project state."""
    source = find_source_file(project, persist_discovery=False)
    return output_path(project, source, suffix=".epub")
