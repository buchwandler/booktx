"""Shard-based v3 translation store backend."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from hashlib import sha256
from pathlib import Path
from typing import TypeVar

from booktx.config import Project, _err
from booktx.io_utils import utc_timestamp
from booktx.models import (
    StoredTranslationRecordV2,
    TranslationCandidate,
    TranslationReviewCandidate,
    TranslationStoreV2,
)
from booktx.progress import load_source_records
from booktx.record_refs import parse_record_ref

from .models import (
    StoreCommitResult,
    StoreFormat,
    V3CurrentRecord,
    V3CurrentShard,
    V3Manifest,
    V3ReviewCandidate,
    V3ReviewRecord,
    V3ReviewShard,
    V3TranslationCandidate,
    V3TranslationRecord,
    V3TranslationShard,
)
from .paths import (
    chunk_id_for_record,
    current_shard_path,
    manifest_path,
    review_candidates_shard_path,
    store_root,
    transactions_dir,
    translation_candidates_shard_path,
)
from .transactions import commit_v3_transaction, recover_v3_transactions

__all__ = ["V3TranslationStoreRepository"]

T = TypeVar("T")


def _model_json_text(model: object) -> str:
    return str(model.model_dump_json(indent=2)) + "\n"  # type: ignore[attr-defined]


def _v3_translation_candidates(
    candidates: list[TranslationCandidate],
) -> list[V3TranslationCandidate]:
    return [
        V3TranslationCandidate.model_validate(
            candidate.model_dump(
                mode="python",
                exclude={"source_sha256", "target_sha256"},
            )
        )
        for candidate in candidates
    ]


def _v3_review_candidates(
    candidates: list[TranslationReviewCandidate],
) -> list[V3ReviewCandidate]:
    return [
        V3ReviewCandidate.model_validate(
            candidate.model_dump(
                mode="python",
                exclude={"source_sha256"},
            )
        )
        for candidate in candidates
    ]


def _file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return sha256(path.read_bytes()).hexdigest()


def _json_revision(path: Path) -> int | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    revision = payload.get("revision")
    return revision if isinstance(revision, int) else None


class V3TranslationStoreRepository:
    """Shard-based canonical store repository."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.format = StoreFormat.V3
        self._source_records: dict[str, object] | None = None

    def _recover_if_needed(self) -> None:
        root = store_root(self.project)
        if root.is_dir():
            recover_v3_transactions(transactions_dir(self.project), root)

    def _source_record_map(self) -> dict[str, object]:
        if self._source_records is None:
            self._source_records = {
                record.record_id: record for record in load_source_records(self.project)
            }
        return self._source_records

    def _load_manifest(self) -> V3Manifest:
        self._recover_if_needed()
        path = manifest_path(self.project)
        if not path.is_file():
            if store_root(self.project).exists():
                raise _err(
                    "invalid_translation_store",
                    f"v3 store manifest is missing at {path.as_posix()}",
                )
            timestamp = utc_timestamp()
            return V3Manifest(created_at=timestamp, updated_at=timestamp)
        try:
            return V3Manifest.model_validate_json(path.read_text("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise _err(
                "invalid_translation_store",
                f"v3 store manifest is invalid at {path.as_posix()}: {exc}",
            ) from exc

    def _load_current_shard(self, chunk_id: str) -> V3CurrentShard:
        path = current_shard_path(self.project, chunk_id)
        if not path.is_file():
            return V3CurrentShard(chunk_id=f"{int(chunk_id):04d}")
        return V3CurrentShard.model_validate_json(path.read_text("utf-8"))

    def _load_translation_shard(self, chunk_id: str) -> V3TranslationShard:
        path = translation_candidates_shard_path(self.project, chunk_id)
        if not path.is_file():
            return V3TranslationShard(chunk_id=f"{int(chunk_id):04d}")
        return V3TranslationShard.model_validate_json(path.read_text("utf-8"))

    def _load_review_shard(self, chunk_id: str) -> V3ReviewShard:
        path = review_candidates_shard_path(self.project, chunk_id)
        if not path.is_file():
            return V3ReviewShard(chunk_id=f"{int(chunk_id):04d}")
        return V3ReviewShard.model_validate_json(path.read_text("utf-8"))

    def _materialize_chunk(
        self, chunk_id: str
    ) -> list[tuple[str, StoredTranslationRecordV2]]:
        current = self._load_current_shard(chunk_id)
        translations = self._load_translation_shard(chunk_id)
        reviews = self._load_review_shard(chunk_id)
        source_by_id = self._source_record_map()
        record_ids = sorted(
            set(current.records) | set(translations.records) | set(reviews.records)
        )
        materialized: list[tuple[str, StoredTranslationRecordV2]] = []
        for record_id in record_ids:
            ref = parse_record_ref(record_id)
            current_record = current.records.get(record_id)
            translation_record = translations.records.get(record_id)
            review_record = reviews.records.get(record_id)
            source_view = source_by_id.get(record_id)
            source_sha256 = ""
            if current_record is not None and current_record.source_sha256:
                source_sha256 = current_record.source_sha256
            elif translation_record is not None and translation_record.source_sha256:
                source_sha256 = translation_record.source_sha256
            elif review_record is not None and review_record.source_sha256:
                source_sha256 = review_record.source_sha256
            elif source_view is not None:
                source_sha256 = source_view.source_sha256  # type: ignore[attr-defined]
            source = source_view.source if source_view is not None else ""  # type: ignore[attr-defined]
            materialized.append(
                (
                    record_id,
                    StoredTranslationRecordV2(
                        chunk_id=(
                            current_record.chunk_id
                            if current_record is not None
                            else ref.chunk_id
                        ),
                        part_id=(
                            current_record.part_id
                            if current_record is not None
                            else ref.part_id
                        ),
                        source_sha256=source_sha256,
                        source=source,
                        active_version=(
                            current_record.active_version
                            if current_record is not None
                            else None
                        ),
                        active_review=(
                            current_record.active_review
                            if current_record is not None
                            else None
                        ),
                        versions=(
                            [
                                TranslationCandidate.model_validate(
                                    candidate.model_dump(
                                        mode="python",
                                        exclude={"source_sha256", "target_sha256"},
                                    )
                                )
                                for candidate in translation_record.versions
                            ]
                            if translation_record is not None
                            else []
                        ),
                        reviews=(
                            [
                                TranslationReviewCandidate.model_validate(
                                    candidate.model_dump(
                                        mode="python",
                                        exclude={"source_sha256"},
                                    )
                                )
                                for candidate in review_record.reviews
                            ]
                            if review_record is not None
                            else []
                        ),
                    ),
                )
            )
        return materialized

    def materialize_v2(self) -> TranslationStoreV2:
        manifest = self._load_manifest()
        records: dict[str, StoredTranslationRecordV2] = {}
        for chunk_id in manifest.chunk_ids:
            for record_id, record in self._materialize_chunk(chunk_id):
                records[record_id] = record
        return TranslationStoreV2(source_sha256=manifest.source_sha256, records=records)

    def get_record(self, record_id: str) -> StoredTranslationRecordV2 | None:
        chunk_id = chunk_id_for_record(record_id)
        for current_id, record in self._materialize_chunk(chunk_id):
            if current_id == record_id:
                return record
        return None

    def iter_records(self) -> Iterator[tuple[str, StoredTranslationRecordV2]]:
        manifest = self._load_manifest()
        for chunk_id in manifest.chunk_ids:
            yield from self.iter_chunk_records(chunk_id)

    def iter_chunk_records(
        self, chunk_id: int | str
    ) -> Iterator[tuple[str, StoredTranslationRecordV2]]:
        yield from self._materialize_chunk(f"{int(chunk_id):04d}")

    def is_empty(self) -> bool:
        manifest = self._load_manifest()
        return not manifest.chunk_ids

    def _serialize_store(
        self, store: TranslationStoreV2
    ) -> tuple[
        V3Manifest,
        dict[str, V3CurrentShard],
        dict[str, V3TranslationShard],
        dict[str, V3ReviewShard],
    ]:
        existing_manifest = self._load_manifest()
        chunk_ids = sorted({record_id.split("-", 1)[0] for record_id in store.records})
        manifest_changed = (
            existing_manifest.chunk_ids != chunk_ids
            or existing_manifest.source_sha256 != store.source_sha256
            or not manifest_path(self.project).is_file()
        )
        preserved_timestamp = (
            existing_manifest.updated_at
            or existing_manifest.created_at
            or utc_timestamp()
        )
        manifest = V3Manifest(
            source_sha256=store.source_sha256,
            chunk_ids=chunk_ids,
            created_at=existing_manifest.created_at or utc_timestamp(),
            updated_at=utc_timestamp() if manifest_changed else preserved_timestamp,
        )
        current: dict[str, V3CurrentShard] = {}
        translations: dict[str, V3TranslationShard] = {}
        reviews: dict[str, V3ReviewShard] = {}
        for chunk_id in chunk_ids:
            current_records: dict[str, V3CurrentRecord] = {}
            translation_records: dict[str, V3TranslationRecord] = {}
            review_records: dict[str, V3ReviewRecord] = {}
            chunk_items = sorted(
                (
                    (record_id, record)
                    for record_id, record in store.records.items()
                    if record_id.startswith(f"{chunk_id}-")
                ),
                key=lambda item: item[0],
            )
            for record_id, record in chunk_items:
                current_records[record_id] = V3CurrentRecord(
                    chunk_id=record.chunk_id,
                    part_id=record.part_id,
                    source_sha256=record.source_sha256,
                    active_version=record.active_version,
                    active_review=record.active_review,
                )
                translation_records[record_id] = V3TranslationRecord(
                    chunk_id=record.chunk_id,
                    part_id=record.part_id,
                    source_sha256=record.source_sha256,
                    versions=_v3_translation_candidates(record.versions),
                )
                review_records[record_id] = V3ReviewRecord(
                    chunk_id=record.chunk_id,
                    part_id=record.part_id,
                    source_sha256=record.source_sha256,
                    reviews=_v3_review_candidates(record.reviews),
                )
            current[chunk_id] = V3CurrentShard(
                chunk_id=chunk_id, records=current_records
            )
            translations[chunk_id] = V3TranslationShard(
                chunk_id=chunk_id,
                records=translation_records,
            )
            reviews[chunk_id] = V3ReviewShard(chunk_id=chunk_id, records=review_records)
        return manifest, current, translations, reviews

    def _chunk_records(
        self, store: TranslationStoreV2, chunk_id: str
    ) -> dict[str, StoredTranslationRecordV2]:
        prefix = f"{chunk_id}-"
        return {
            record_id: record
            for record_id, record in sorted(store.records.items())
            if record_id.startswith(prefix)
        }

    def _serialize_chunk_records(
        self, chunk_id: str, records: dict[str, StoredTranslationRecordV2]
    ) -> tuple[
        V3CurrentShard | None,
        V3TranslationShard | None,
        V3ReviewShard | None,
    ]:
        if not records:
            return None, None, None
        current_records: dict[str, V3CurrentRecord] = {}
        translation_records: dict[str, V3TranslationRecord] = {}
        review_records: dict[str, V3ReviewRecord] = {}
        for record_id, record in sorted(records.items()):
            current_records[record_id] = V3CurrentRecord(
                chunk_id=record.chunk_id,
                part_id=record.part_id,
                source_sha256=record.source_sha256,
                active_version=record.active_version,
                active_review=record.active_review,
            )
            translation_records[record_id] = V3TranslationRecord(
                chunk_id=record.chunk_id,
                part_id=record.part_id,
                source_sha256=record.source_sha256,
                versions=_v3_translation_candidates(record.versions),
            )
            review_records[record_id] = V3ReviewRecord(
                chunk_id=record.chunk_id,
                part_id=record.part_id,
                source_sha256=record.source_sha256,
                reviews=_v3_review_candidates(record.reviews),
            )
        return (
            V3CurrentShard(chunk_id=chunk_id, records=current_records),
            V3TranslationShard(chunk_id=chunk_id, records=translation_records),
            V3ReviewShard(chunk_id=chunk_id, records=review_records),
        )

    def _commit_partial_store(
        self,
        *,
        existing_manifest: V3Manifest,
        before_store: TranslationStoreV2,
        after_store: TranslationStoreV2,
        chunk_ids: list[str],
    ) -> StoreCommitResult:
        root = store_root(self.project)
        root.mkdir(parents=True, exist_ok=True)
        relative_to_text: dict[str, str] = {}
        deletes: list[str] = []
        changed_chunk_ids: list[str] = []
        changed_record_ids: list[str] = []
        deleted_chunk_ids: list[str] = []
        expected_hashes: dict[str, str | None] = {}
        expected_revisions: dict[str, int | None] = {}
        next_chunk_ids = set(existing_manifest.chunk_ids)

        for chunk_id in chunk_ids:
            before_records = self._chunk_records(before_store, chunk_id)
            after_records = self._chunk_records(after_store, chunk_id)
            current_path = current_shard_path(self.project, chunk_id)
            translation_path = translation_candidates_shard_path(self.project, chunk_id)
            review_path = review_candidates_shard_path(self.project, chunk_id)
            shard_paths = (current_path, translation_path, review_path)
            for shard_path in shard_paths:
                relative_path = shard_path.relative_to(root).as_posix()
                expected_hashes[relative_path] = _file_sha256(shard_path)
                expected_revisions[relative_path] = _json_revision(shard_path)

            new_current, new_translations, new_reviews = self._serialize_chunk_records(
                chunk_id, after_records
            )
            current_text = (
                _model_json_text(new_current) if new_current is not None else None
            )
            translation_text = (
                _model_json_text(new_translations)
                if new_translations is not None
                else None
            )
            review_text = (
                _model_json_text(new_reviews) if new_reviews is not None else None
            )
            previous_current = (
                current_path.read_text("utf-8") if current_path.is_file() else None
            )
            previous_translation = (
                translation_path.read_text("utf-8")
                if translation_path.is_file()
                else None
            )
            previous_review = (
                review_path.read_text("utf-8") if review_path.is_file() else None
            )
            chunk_changed = False

            if current_text is None:
                if previous_current is not None:
                    deletes.append(current_path.relative_to(root).as_posix())
                    chunk_changed = True
            elif current_text != previous_current:
                relative_to_text[current_path.relative_to(root).as_posix()] = (
                    current_text
                )
                chunk_changed = True

            if translation_text is None:
                if previous_translation is not None:
                    deletes.append(translation_path.relative_to(root).as_posix())
                    chunk_changed = True
            elif translation_text != previous_translation:
                relative_to_text[translation_path.relative_to(root).as_posix()] = (
                    translation_text
                )
                chunk_changed = True

            if review_text is None:
                if previous_review is not None:
                    deletes.append(review_path.relative_to(root).as_posix())
                    chunk_changed = True
            elif review_text != previous_review:
                relative_to_text[review_path.relative_to(root).as_posix()] = review_text
                chunk_changed = True

            if chunk_changed and after_records:
                changed_chunk_ids.append(chunk_id)
            if chunk_changed and not after_records and chunk_id in next_chunk_ids:
                deleted_chunk_ids.append(chunk_id)

            if after_records:
                next_chunk_ids.add(chunk_id)
            else:
                next_chunk_ids.discard(chunk_id)

            for record_id in sorted(set(before_records) | set(after_records)):
                if before_records.get(record_id) != after_records.get(record_id):
                    changed_record_ids.append(record_id)

        manifest_file = manifest_path(self.project)
        manifest_relative_path = manifest_file.relative_to(root).as_posix()
        expected_hashes[manifest_relative_path] = _file_sha256(manifest_file)
        manifest_changed = (
            existing_manifest.chunk_ids != sorted(next_chunk_ids)
            or existing_manifest.source_sha256 != after_store.source_sha256
            or not manifest_file.is_file()
        )
        preserved_timestamp = (
            existing_manifest.updated_at
            or existing_manifest.created_at
            or utc_timestamp()
        )
        manifest = V3Manifest(
            source_sha256=after_store.source_sha256,
            chunk_ids=sorted(next_chunk_ids),
            created_at=existing_manifest.created_at or utc_timestamp(),
            updated_at=utc_timestamp() if manifest_changed else preserved_timestamp,
        )
        manifest_text = _model_json_text(manifest)
        previous_manifest_text = _model_json_text(existing_manifest)
        wrote_manifest = (
            manifest_text != previous_manifest_text or not manifest_file.is_file()
        )
        if wrote_manifest:
            relative_to_text[manifest_relative_path] = manifest_text

        if not relative_to_text and not deletes:
            return StoreCommitResult(
                format=StoreFormat.V3,
                changed_chunk_ids=sorted(changed_chunk_ids),
                deleted_chunk_ids=sorted(deleted_chunk_ids),
                changed_record_ids=changed_record_ids,
                wrote_manifest=False,
            )

        return commit_v3_transaction(
            transactions_root=transactions_dir(self.project),
            store_root=root,
            relative_to_text=relative_to_text,
            deletes=sorted(set(deletes)),
            changed_chunk_ids=sorted(changed_chunk_ids),
            deleted_chunk_ids=sorted(deleted_chunk_ids),
            changed_record_ids=changed_record_ids,
            wrote_manifest=wrote_manifest,
            expected_hashes=expected_hashes,
            expected_revisions=expected_revisions,
        )

    def write_materialized_v2(self, store: TranslationStoreV2) -> StoreCommitResult:
        store = TranslationStoreV2.model_validate(store.model_dump(mode="python"))
        existing = self.materialize_v2()
        manifest, current, translations, reviews = self._serialize_store(store)
        existing_manifest = self._load_manifest()
        root = store_root(self.project)
        root.mkdir(parents=True, exist_ok=True)

        new_chunk_ids = set(manifest.chunk_ids)
        old_chunk_ids = set(existing_manifest.chunk_ids)
        all_chunk_ids = sorted(new_chunk_ids | old_chunk_ids)
        relative_to_text: dict[str, str] = {}
        changed_chunk_ids: list[str] = []
        changed_record_ids: list[str] = []
        deleted_chunk_ids = sorted(old_chunk_ids - new_chunk_ids)

        for chunk_id in all_chunk_ids:
            new_current = current.get(chunk_id)
            new_translations = translations.get(chunk_id)
            new_reviews = reviews.get(chunk_id)
            current_path = current_shard_path(self.project, chunk_id)
            translation_path = translation_candidates_shard_path(self.project, chunk_id)
            review_path = review_candidates_shard_path(self.project, chunk_id)
            current_text = (
                _model_json_text(new_current) if new_current is not None else None
            )
            translation_text = (
                _model_json_text(new_translations)
                if new_translations is not None
                else None
            )
            review_text = (
                _model_json_text(new_reviews) if new_reviews is not None else None
            )
            previous_current = (
                current_path.read_text("utf-8") if current_path.is_file() else None
            )
            previous_translation = (
                translation_path.read_text("utf-8")
                if translation_path.is_file()
                else None
            )
            previous_review = (
                review_path.read_text("utf-8") if review_path.is_file() else None
            )
            chunk_changed = False
            if current_text is not None and current_text != previous_current:
                relative_to_text[current_path.relative_to(root).as_posix()] = (
                    current_text
                )
                chunk_changed = True
            if (
                translation_text is not None
                and translation_text != previous_translation
            ):
                relative_to_text[translation_path.relative_to(root).as_posix()] = (
                    translation_text
                )
                chunk_changed = True
            if review_text is not None and review_text != previous_review:
                relative_to_text[review_path.relative_to(root).as_posix()] = review_text
                chunk_changed = True
            if chunk_changed:
                changed_chunk_ids.append(chunk_id)

        deletes: list[str] = []
        for chunk_id in deleted_chunk_ids:
            deletes.extend(
                [
                    current_shard_path(self.project, chunk_id)
                    .relative_to(root)
                    .as_posix(),
                    translation_candidates_shard_path(self.project, chunk_id)
                    .relative_to(root)
                    .as_posix(),
                    review_candidates_shard_path(self.project, chunk_id)
                    .relative_to(root)
                    .as_posix(),
                ]
            )

        manifest_text = _model_json_text(manifest)
        previous_manifest_text = _model_json_text(existing_manifest)
        wrote_manifest = (
            manifest_text != previous_manifest_text
            or not manifest_path(self.project).is_file()
        )
        if wrote_manifest:
            relative_to_text[
                manifest_path(self.project).relative_to(root).as_posix()
            ] = manifest_text

        previous_ids = set(existing.records)
        current_ids = set(store.records)
        for record_id in sorted(previous_ids | current_ids):
            if existing.records.get(record_id) != store.records.get(record_id):
                changed_record_ids.append(record_id)

        return commit_v3_transaction(
            transactions_root=transactions_dir(self.project),
            store_root=root,
            relative_to_text=relative_to_text,
            deletes=deletes,
            changed_chunk_ids=changed_chunk_ids,
            deleted_chunk_ids=deleted_chunk_ids,
            changed_record_ids=changed_record_ids,
            wrote_manifest=wrote_manifest,
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
        del summary
        existing_manifest = self._load_manifest()
        chunk_ids = sorted({chunk_id_for_record(record_id) for record_id in record_ids})
        store = TranslationStoreV2(source_sha256=existing_manifest.source_sha256)
        for chunk_id in chunk_ids:
            for record_id, record in self._materialize_chunk(chunk_id):
                store.records[record_id] = record
        before_store = TranslationStoreV2.model_validate(
            store.model_dump(mode="python")
        )
        result = mutator(store)
        store.source_sha256 = (
            source_sha256
            if source_sha256 is not None
            else existing_manifest.source_sha256
        )
        self._commit_partial_store(
            existing_manifest=existing_manifest,
            before_store=before_store,
            after_store=store,
            chunk_ids=chunk_ids,
        )
        return result

    def clear_all(self, *, source_sha256: str = "") -> StoreCommitResult:
        existing_manifest = self._load_manifest()
        return self._commit_partial_store(
            existing_manifest=existing_manifest,
            before_store=TranslationStoreV2(
                source_sha256=existing_manifest.source_sha256
            ),
            after_store=TranslationStoreV2(source_sha256=source_sha256),
            chunk_ids=list(existing_manifest.chunk_ids),
        )

    def update_source_sha256(self, source_sha256: str) -> StoreCommitResult:
        existing_manifest = self._load_manifest()
        return self._commit_partial_store(
            existing_manifest=existing_manifest,
            before_store=TranslationStoreV2(
                source_sha256=existing_manifest.source_sha256
            ),
            after_store=TranslationStoreV2(source_sha256=source_sha256),
            chunk_ids=[],
        )
