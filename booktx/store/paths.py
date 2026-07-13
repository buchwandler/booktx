"""Path helpers for the canonical translation store backends."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from booktx.config import Project

from booktx.record_refs import parse_record_ref

__all__ = [
    "chunk_id_filename",
    "current_shard_path",
    "manifest_path",
    "review_candidates_shard_path",
    "store_root",
    "transactions_dir",
    "translation_candidates_shard_path",
]


def store_root(project: Project) -> Path:
    from booktx.config import translation_store_v3_root

    return translation_store_v3_root(project)


def manifest_path(project: Project) -> Path:
    from booktx.config import translation_store_v3_manifest_path

    return translation_store_v3_manifest_path(project)


def transactions_dir(project: Project) -> Path:
    return store_root(project) / "transactions"


def chunk_id_filename(chunk_id: int | str) -> str:
    if isinstance(chunk_id, str):
        text = chunk_id.strip()
        if not text:
            raise ValueError("chunk id must not be empty")
        parsed = int(text)
    else:
        parsed = chunk_id
    if parsed <= 0:
        raise ValueError("chunk id must be a positive integer")
    return f"{parsed:04d}.json"


def _chunk_dir_path(project: Project, dirname: str, chunk_id: int | str) -> Path:
    return store_root(project) / dirname / chunk_id_filename(chunk_id)


def current_shard_path(project: Project, chunk_id: int | str) -> Path:
    return _chunk_dir_path(project, "current", chunk_id)


def translation_candidates_shard_path(project: Project, chunk_id: int | str) -> Path:
    return _chunk_dir_path(project, "translation-candidates", chunk_id)


def review_candidates_shard_path(project: Project, chunk_id: int | str) -> Path:
    return _chunk_dir_path(project, "review-candidates", chunk_id)


def chunk_id_for_record(record_id: str) -> str:
    return f"{parse_record_ref(record_id).chunk_id:04d}"
