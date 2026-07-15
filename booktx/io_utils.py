"""Atomic write and timestamp helpers.

Centralizes the atomic-write pattern (write to a sibling temp file, then
``replace``) that was previously duplicated as the private
``_write_json_atomic`` in :mod:`booktx.config` and reimplemented inline as
direct ``Path.write_text`` calls in several modules.

All booktx persistence (translation store, context, chapter map, manifest,
reports, chunks, task source files, and ingest templates) should route
through :func:`write_text_atomic` or :func:`write_json_model_atomic` so an
interrupted write never leaves a half-empty file in ``.booktx/``.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel


def utc_timestamp() -> str:
    """Return the current UTC time as a second-precision ISO-8601 ``Z`` string.

    Equivalent to the inline expression that was duplicated in the translate
    acceptance path. Microseconds are dropped so timestamps are stable and
    human-comparable.
    """
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def write_text_atomic(path: Path, text: str) -> None:
    """Write UTF-8 ``text`` atomically into ``path``.

    The file is written to a hidden sibling temp file in the same directory and
    then ``replace``\\ d into place, so readers either see the previous file or
    the complete new file, never a partial write. Parent directories are
    created on demand. The temp file is cleaned up if the write fails.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as fh:
            fh.write(text)
            tmp_path = Path(fh.name)
        tmp_path.replace(path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def write_json_model_atomic(
    path: Path, model: BaseModel, *, indent: int = 2, trailing_newline: bool = True
) -> None:
    """Serialize a Pydantic ``model`` to JSON and write it atomically into ``path``.

    Mirrors the previous ``model_dump_json(indent=2) + "\\n"`` convention used
    by the store, chapter map, and chunk writers. ``by_alias`` and other dump
    options must be applied by the caller via ``model.model_dump_json`` when
    needed; this helper is for the common default case.
    """
    text = model.model_dump_json(indent=indent)
    if trailing_newline:
        text += "\n"
    write_text_atomic(path, text)


def write_json_text_atomic(path: Path, text: str) -> None:
    """Write already-serialized JSON text atomically into ``path``.

    Used when the caller has already produced JSON via ``json.dumps`` or
    ``model_dump_json(by_alias=True)`` and just needs the atomic write plus a
    trailing newline. The trailing newline matches the historical convention.
    """
    if not text.endswith("\n"):
        text += "\n"
    write_text_atomic(path, text)
