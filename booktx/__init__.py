"""booktx: deterministic translation-preparation CLI for Markdown and EPUB.

booktx does NOT translate text. It extracts translatable sentence records into
JSON chunks, validates chunks that a coding agent has translated, and rebuilds
the final translated document. See :mod:`booktx.cli` for the command surface.
"""

try:
    from booktx._version import __version__  # type: ignore[assignment]
except Exception:  # pragma: no cover - source tree without generated version file
    __version__ = "0+unknown"

__all__ = ["__version__"]
