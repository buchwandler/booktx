"""Legacy v1/v2 translation-store adapter."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from typing import TypeVar

from booktx.config import Project, translation_store_path
from booktx.io_utils import write_json_model_atomic
from booktx.models import (
    StoredTranslationRecordV2,
    TranslationStore,
    TranslationStoreV2,
)
from booktx.progress import load_source_records
from booktx.translation_store import legacy_store_to_v2

from .models import StoreCommitResult, StoreFormat

__all__ = ["V1V2TranslationStoreRepository"]

T = TypeVar("T")


def _load_v2_store(project: Project) -> TranslationStoreV2:
    path = translation_store_path(project)
    if not path.is_file():
        return TranslationStoreV2()
    raw = json.loads(path.read_text("utf-8"))
    if isinstance(raw, dict) and raw.get("version") == 2:
        return TranslationStoreV2.model_validate(raw)
    legacy = TranslationStore.model_validate(raw)
    source_records = {
        record.record_id: record for record in load_source_records(project)
    }
    return legacy_store_to_v2(legacy, source_records=source_records)


class V1V2TranslationStoreRepository:
    """Adapter for the legacy flat file and current v2 file formats."""

    def __init__(
        self, project: Project, *, format: StoreFormat = StoreFormat.V2
    ) -> None:
        self.project = project
        self.format = format

    def materialize_v2(self) -> TranslationStoreV2:
        return _load_v2_store(self.project)

    def write_materialized_v2(self, store: TranslationStoreV2) -> StoreCommitResult:
        path = translation_store_path(self.project)
        previous = self.materialize_v2() if path.is_file() else TranslationStoreV2()
        write_json_model_atomic(path, store)
        previous_ids = set(previous.records)
        current_ids = set(store.records)
        changed_ids = sorted(
            record_id
            for record_id in current_ids | previous_ids
            if previous.records.get(record_id) != store.records.get(record_id)
        )
        changed_chunks = sorted(
            {record_id.split("-", 1)[0] for record_id in changed_ids}
        )
        return StoreCommitResult(
            format=StoreFormat.V2,
            changed_chunk_ids=changed_chunks,
            changed_record_ids=changed_ids,
            deleted_chunk_ids=sorted(
                {record_id.split("-", 1)[0] for record_id in previous_ids - current_ids}
            ),
        )

    def edit_v2(
        self, mutator: Callable[[TranslationStoreV2], T], *, summary: str = ""
    ) -> T:
        del summary
        store = self.materialize_v2()
        result = mutator(store)
        self.write_materialized_v2(store)
        return result

    def edit_records(
        self,
        record_ids: Iterable[str],
        mutator: Callable[[TranslationStoreV2], T],
        *,
        summary: str = "",
        source_sha256: str | None = None,
    ) -> T:
        del record_ids, summary
        store = self.materialize_v2()
        result = mutator(store)
        if source_sha256 is not None:
            store.source_sha256 = source_sha256
        self.write_materialized_v2(store)
        return result

    def get_record(self, record_id: str) -> StoredTranslationRecordV2 | None:
        return self.materialize_v2().records.get(record_id)

    def iter_records(self) -> Iterator[tuple[str, StoredTranslationRecordV2]]:
        yield from sorted(self.materialize_v2().records.items())

    def iter_chunk_records(
        self, chunk_id: int | str
    ) -> Iterator[tuple[str, StoredTranslationRecordV2]]:
        prefix = f"{int(chunk_id):04d}-"
        for record_id, record in self.iter_records():
            if record_id.startswith(prefix):
                yield record_id, record

    def is_empty(self) -> bool:
        return not self.materialize_v2().records

    def clear_all(self, *, source_sha256: str = "") -> StoreCommitResult:
        return self.write_materialized_v2(
            TranslationStoreV2(source_sha256=source_sha256)
        )

    def update_source_sha256(self, source_sha256: str) -> StoreCommitResult:
        store = self.materialize_v2()
        store.source_sha256 = source_sha256
        return self.write_materialized_v2(store)
