"""Backend-neutral store integrity helpers."""

from __future__ import annotations

from dataclasses import dataclass, field

from booktx.config import Project

from .detect import detect_store_format, open_translation_store
from .models import StoreFormat

__all__ = ["StoreDoctorReport", "inspect_store"]


@dataclass(slots=True)
class StoreDoctorReport:
    """Lightweight store health report."""

    format: StoreFormat
    record_count: int
    chunk_ids: list[str] = field(default_factory=list)


def inspect_store(project: Project) -> StoreDoctorReport:
    """Inspect the current store backend without changing it."""

    store_format = detect_store_format(project)
    repo = open_translation_store(project, default_format=StoreFormat.V2)
    records = list(repo.iter_records())
    chunk_ids = sorted({record_id.split("-", 1)[0] for record_id, _record in records})
    return StoreDoctorReport(
        format=store_format,
        record_count=len(records),
        chunk_ids=chunk_ids,
    )
