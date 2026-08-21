"""Shard-based v3 translation store backend."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
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
    MaterializedStoreRecord,
    MaterializedStoreSnapshot,
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
    validate_v3_shard_consistency,
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
from .transactions import _json_revision, commit_v3_transaction, recover_v3_transactions

__all__ = ["V3TranslationStoreRepository"]

T = TypeVar("T")

_READ_RETRIES = 5
_READ_RETRY_DELAY_SECONDS = 0.005


def _evolve_manifest(
    existing: V3Manifest,
    *,
    source_sha256: str,
    chunk_ids: list[str],
    changed: bool,
) -> V3Manifest:
    """Update mutable manifest fields without dropping historical metadata."""

    payload = existing.model_dump(mode="python")
    payload.update(
        {
            "source_sha256": source_sha256,
            "chunk_ids": chunk_ids,
            "created_at": existing.created_at or utc_timestamp(),
            "updated_at": utc_timestamp()
            if changed
            else (existing.updated_at or existing.created_at or utc_timestamp()),
        }
    )
    return V3Manifest.model_validate(payload)


@dataclass(frozen=True, slots=True)
class V3ChunkSnapshot:
    """One internally consistent read of the three shard envelopes."""

    current: V3CurrentShard
    translations: V3TranslationShard
    reviews: V3ReviewShard
    revision: int


@dataclass(frozen=True, slots=True)
class SerializedChunk:
    current: V3CurrentShard | None
    translations: V3TranslationShard | None
    reviews: V3ReviewShard | None


@dataclass(frozen=True, slots=True)
class ChunkWritePlan:
    writes: dict[str, str]
    deletes: list[str]
    expected_hashes: dict[str, str | None]
    expected_revisions: dict[str, int | None]
    changed: bool


@dataclass(frozen=True, slots=True)
class V3WritePlan:
    relative_to_text: dict[str, str]
    deletes: list[str]
    expected_hashes: dict[str, str | None]
    expected_revisions: dict[str, int | None]
    changed_chunk_ids: list[str]
    deleted_chunk_ids: list[str]
    changed_record_ids: list[str]
    wrote_manifest: bool
    summary: str = ""


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
        except Exception as exc:
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

    def _read_consistent_chunk(self, chunk_id: str) -> V3ChunkSnapshot:
        """Read a chunk only when all shard files share one stable revision."""

        normalized_chunk_id = f"{int(chunk_id):04d}"
        root = store_root(self.project)
        shard_paths = (
            current_shard_path(self.project, normalized_chunk_id),
            translation_candidates_shard_path(self.project, normalized_chunk_id),
            review_candidates_shard_path(self.project, normalized_chunk_id),
        )
        last_error = "unknown concurrent update"
        for _attempt in range(_READ_RETRIES):
            # A reader never starts a snapshot while a writer is publishing.
            # A pending transaction without a live lock is safe to recover
            # before taking the revision boundary.
            if (root / ".write-lock").exists():
                time.sleep(_READ_RETRY_DELAY_SECONDS)
                continue
            self._recover_if_needed()
            if (root / ".write-lock").exists():
                time.sleep(_READ_RETRY_DELAY_SECONDS)
                continue

            present = [path.is_file() for path in shard_paths]
            if any(present) and not all(present):
                raise _err(
                    "invalid_translation_store",
                    f"v3 chunk {normalized_chunk_id} has an incomplete shard set",
                )

            before = tuple(_json_revision(path) for path in shard_paths)
            if len(set(before)) > 1:
                last_error = (
                    f"v3 chunk {normalized_chunk_id} has mixed revisions {before!r}"
                )
                time.sleep(_READ_RETRY_DELAY_SECONDS)
                continue
            current = self._load_current_shard(normalized_chunk_id)
            translations = self._load_translation_shard(normalized_chunk_id)
            reviews = self._load_review_shard(normalized_chunk_id)
            after = tuple(_json_revision(path) for path in shard_paths)
            if before != after or (root / ".write-lock").exists():
                last_error = f"v3 chunk {normalized_chunk_id} changed during the read"
                time.sleep(_READ_RETRY_DELAY_SECONDS)
                continue
            try:
                validate_v3_shard_consistency(
                    current=current,
                    translations=translations,
                    reviews=reviews,
                    # Rejected or stale active selections are validation
                    # findings, not unreadable shard topology. The validator
                    # still performs the strict selection check via doctor.
                    validate_active_selection=False,
                )
            except ValueError as exc:
                raise _err(
                    "invalid_translation_store",
                    f"v3 chunk {normalized_chunk_id} failed consistency checks: {exc}",
                ) from exc
            revision = before[0] if before[0] is not None else 0
            return V3ChunkSnapshot(
                current=current,
                translations=translations,
                reviews=reviews,
                revision=revision,
            )
        raise _err(
            "store_concurrent_update",
            f"could not obtain a consistent v3 chunk snapshot after {_READ_RETRIES} "
            f"attempts: {last_error}",
        )

    def _materialize_chunk(
        self, chunk_id: str
    ) -> list[tuple[str, StoredTranslationRecordV2]]:
        snapshot = self._read_consistent_chunk(chunk_id)
        current = snapshot.current
        translations = snapshot.translations
        reviews = snapshot.reviews
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

    def materialize_v2(self) -> MaterializedStoreSnapshot:
        manifest = self._load_manifest()
        records: dict[str, MaterializedStoreRecord] = {}
        for chunk_id in manifest.chunk_ids:
            for record_id, record in self._materialize_chunk(chunk_id):
                records[record_id] = record
        return TranslationStoreV2(source_sha256=manifest.source_sha256, records=records)

    def get_record(self, record_id: str) -> MaterializedStoreRecord | None:
        chunk_id = chunk_id_for_record(record_id)
        for current_id, record in self._materialize_chunk(chunk_id):
            if current_id == record_id:
                return record
        return None

    def iter_records(self) -> Iterator[tuple[str, MaterializedStoreRecord]]:
        manifest = self._load_manifest()
        for chunk_id in manifest.chunk_ids:
            yield from self.iter_chunk_records(chunk_id)

    def iter_chunk_records(
        self, chunk_id: int | str
    ) -> Iterator[tuple[str, MaterializedStoreRecord]]:
        yield from self._materialize_chunk(f"{int(chunk_id):04d}")

    def is_empty(self) -> bool:
        manifest = self._load_manifest()
        return not manifest.chunk_ids

    def _serialize_store(
        self, store: MaterializedStoreSnapshot
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
        manifest = _evolve_manifest(
            existing_manifest,
            source_sha256=store.source_sha256,
            chunk_ids=chunk_ids,
            changed=manifest_changed,
        )
        current: dict[str, V3CurrentShard] = {}
        translations: dict[str, V3TranslationShard] = {}
        reviews: dict[str, V3ReviewShard] = {}
        for chunk_id in chunk_ids:
            serialized = self._serialize_chunk(
                chunk_id=chunk_id,
                records=self._chunk_records(store, chunk_id),
                revision=self._next_chunk_revision(chunk_id),
            )
            if serialized is None:
                continue
            assert serialized.current is not None
            assert serialized.translations is not None
            assert serialized.reviews is not None
            current[chunk_id] = serialized.current
            translations[chunk_id] = serialized.translations
            reviews[chunk_id] = serialized.reviews
        return manifest, current, translations, reviews

    def _next_chunk_revision(self, chunk_id: str) -> int:
        revisions = [
            _json_revision(current_shard_path(self.project, chunk_id)),
            _json_revision(translation_candidates_shard_path(self.project, chunk_id)),
            _json_revision(review_candidates_shard_path(self.project, chunk_id)),
        ]
        return max((revision or 0) for revision in revisions) + 1

    def _chunk_records(
        self, store: MaterializedStoreSnapshot, chunk_id: str
    ) -> dict[str, MaterializedStoreRecord]:
        prefix = f"{chunk_id}-"
        return {
            record_id: record
            for record_id, record in sorted(store.records.items())
            if record_id.startswith(prefix)
        }

    def _serialize_chunk(
        self,
        *,
        chunk_id: str,
        records: dict[str, MaterializedStoreRecord],
        revision: int,
    ) -> SerializedChunk | None:
        if not records:
            return None
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
        return SerializedChunk(
            current=V3CurrentShard(
                chunk_id=chunk_id, revision=revision, records=current_records
            ),
            translations=V3TranslationShard(
                chunk_id=chunk_id,
                revision=revision,
                records=translation_records,
            ),
            reviews=V3ReviewShard(
                chunk_id=chunk_id,
                revision=revision,
                records=review_records,
            ),
        )

    def _plan_chunk_write(
        self,
        *,
        root: Path,
        chunk_id: str,
        before_records: dict[str, MaterializedStoreRecord],
        after_records: dict[str, MaterializedStoreRecord],
        capture_expected_state: bool,
    ) -> ChunkWritePlan:
        current_path = current_shard_path(self.project, chunk_id)
        translation_path = translation_candidates_shard_path(self.project, chunk_id)
        review_path = review_candidates_shard_path(self.project, chunk_id)
        shard_paths = (
            ("current", current_path),
            ("translation", translation_path),
            ("review", review_path),
        )
        expected_hashes: dict[str, str | None] = {}
        expected_revisions: dict[str, int | None] = {}
        if capture_expected_state:
            for _kind, shard_path in shard_paths:
                relative_path = shard_path.relative_to(root).as_posix()
                expected_hashes[relative_path] = _file_sha256(shard_path)
                expected_revisions[relative_path] = _json_revision(shard_path)

        serialized = self._serialize_chunk(
            chunk_id=chunk_id,
            records=after_records,
            revision=self._next_chunk_revision(chunk_id),
        )
        texts = {
            "current": (
                _model_json_text(serialized.current)
                if serialized is not None and serialized.current is not None
                else None
            ),
            "translation": (
                _model_json_text(serialized.translations)
                if serialized is not None and serialized.translations is not None
                else None
            ),
            "review": (
                _model_json_text(serialized.reviews)
                if serialized is not None and serialized.reviews is not None
                else None
            ),
        }
        previous_texts = {
            kind: path.read_text("utf-8") if path.is_file() else None
            for kind, path in shard_paths
        }
        writes: dict[str, str] = {}
        deletes: list[str] = []
        changed = False
        for kind, shard_path in shard_paths:
            relative_path = shard_path.relative_to(root).as_posix()
            text = texts[kind]
            previous = previous_texts[kind]
            if text is None:
                if previous is not None:
                    deletes.append(relative_path)
                    changed = True
            elif text != previous:
                writes[relative_path] = text
                changed = True

        return ChunkWritePlan(
            writes=writes,
            deletes=deletes,
            expected_hashes=expected_hashes,
            expected_revisions=expected_revisions,
            changed=changed,
        )

    def _build_write_plan(
        self,
        *,
        existing_manifest: V3Manifest,
        before_store: MaterializedStoreSnapshot,
        after_store: MaterializedStoreSnapshot,
        chunk_ids: list[str],
        capture_expected_state: bool,
        summary: str = "",
    ) -> V3WritePlan:
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
            chunk_plan = self._plan_chunk_write(
                root=root,
                chunk_id=chunk_id,
                before_records=before_records,
                after_records=after_records,
                capture_expected_state=capture_expected_state,
            )
            relative_to_text.update(chunk_plan.writes)
            deletes.extend(chunk_plan.deletes)
            expected_hashes.update(chunk_plan.expected_hashes)
            expected_revisions.update(chunk_plan.expected_revisions)

            if chunk_plan.changed and after_records:
                changed_chunk_ids.append(chunk_id)
            if chunk_plan.changed and not after_records and chunk_id in next_chunk_ids:
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
        if capture_expected_state:
            expected_hashes[manifest_relative_path] = _file_sha256(manifest_file)
            expected_revisions[manifest_relative_path] = _json_revision(manifest_file)
        manifest_changed = (
            existing_manifest.chunk_ids != sorted(next_chunk_ids)
            or existing_manifest.source_sha256 != after_store.source_sha256
            or not manifest_file.is_file()
        )
        manifest = _evolve_manifest(
            existing_manifest,
            source_sha256=after_store.source_sha256,
            chunk_ids=sorted(next_chunk_ids),
            changed=manifest_changed,
        )
        manifest_text = _model_json_text(manifest)
        previous_manifest_text = _model_json_text(existing_manifest)
        wrote_manifest = (
            manifest_text != previous_manifest_text or not manifest_file.is_file()
        )
        if wrote_manifest:
            relative_to_text[manifest_relative_path] = manifest_text

        return V3WritePlan(
            relative_to_text=relative_to_text,
            deletes=sorted(set(deletes)),
            expected_hashes=expected_hashes,
            expected_revisions=expected_revisions,
            changed_chunk_ids=sorted(changed_chunk_ids),
            deleted_chunk_ids=sorted(deleted_chunk_ids),
            changed_record_ids=changed_record_ids,
            wrote_manifest=wrote_manifest,
            summary=summary,
        )

    def _commit_write_plan(self, plan: V3WritePlan) -> StoreCommitResult:
        if not plan.relative_to_text and not plan.deletes:
            return StoreCommitResult(
                format=StoreFormat.V3,
                changed_chunk_ids=plan.changed_chunk_ids,
                deleted_chunk_ids=plan.deleted_chunk_ids,
                changed_record_ids=plan.changed_record_ids,
                wrote_manifest=False,
            )
        return commit_v3_transaction(
            transactions_root=transactions_dir(self.project),
            store_root=store_root(self.project),
            relative_to_text=plan.relative_to_text,
            deletes=plan.deletes,
            changed_chunk_ids=plan.changed_chunk_ids,
            deleted_chunk_ids=plan.deleted_chunk_ids,
            changed_record_ids=plan.changed_record_ids,
            wrote_manifest=plan.wrote_manifest,
            summary=plan.summary,
            expected_hashes=plan.expected_hashes,
            expected_revisions=plan.expected_revisions,
        )

    def _commit_partial_store(
        self,
        *,
        existing_manifest: V3Manifest,
        before_store: MaterializedStoreSnapshot,
        after_store: MaterializedStoreSnapshot,
        chunk_ids: list[str],
        summary: str = "",
    ) -> StoreCommitResult:
        plan = self._build_write_plan(
            existing_manifest=existing_manifest,
            before_store=before_store,
            after_store=after_store,
            chunk_ids=chunk_ids,
            capture_expected_state=True,
            summary=summary,
        )
        return self._commit_write_plan(plan)

    def write_materialized_v2(
        self, store: MaterializedStoreSnapshot, *, summary: str = ""
    ) -> StoreCommitResult:
        store = TranslationStoreV2.model_validate(store.model_dump(mode="python"))
        existing = self.materialize_v2()
        existing_manifest = self._load_manifest()
        chunk_ids = sorted(
            set(existing_manifest.chunk_ids)
            | {record_id.split("-", 1)[0] for record_id in store.records}
        )
        plan = self._build_write_plan(
            existing_manifest=existing_manifest,
            before_store=existing,
            after_store=store,
            chunk_ids=chunk_ids,
            capture_expected_state=True,
            summary=summary,
        )
        return self._commit_write_plan(plan)

    def _validate_edit_records_scope(
        self,
        *,
        before_store: MaterializedStoreSnapshot,
        after_store: MaterializedStoreSnapshot,
        chunk_ids: list[str],
    ) -> None:
        requested_chunk_ids = set(chunk_ids)
        changed_record_ids = [
            record_id
            for record_id in sorted(
                set(before_store.records) | set(after_store.records)
            )
            if before_store.records.get(record_id) != after_store.records.get(record_id)
        ]
        changed_chunk_ids = {
            chunk_id_for_record(record_id) for record_id in changed_record_ids
        }
        for record_id in changed_record_ids:
            record = after_store.records.get(record_id)
            if record is None:
                continue
            canonical_chunk_id = chunk_id_for_record(record_id)
            if canonical_chunk_id != f"{int(record.chunk_id):04d}":
                raise _err(
                    "translation_store_scope_violation",
                    "record "
                    f"{record_id} does not match declared chunk {record.chunk_id}",
                )
        if not changed_chunk_ids.issubset(requested_chunk_ids):
            raise _err(
                "translation_store_scope_violation",
                "partial store edit changed records outside the requested chunk scope",
            )

    def edit_records(
        self,
        record_ids: Iterable[str],
        mutator: Callable[[TranslationStoreV2], T],
        *,
        summary: str = "",
        source_sha256: str | None = None,
    ) -> T:
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
        self._validate_edit_records_scope(
            before_store=before_store,
            after_store=store,
            chunk_ids=chunk_ids,
        )
        self._commit_partial_store(
            existing_manifest=existing_manifest,
            before_store=before_store,
            after_store=store,
            chunk_ids=chunk_ids,
            summary=summary,
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
