"""Store backend models and typed repository contracts."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from booktx.models import (
    StoredTranslationRecordV2,
    TranslationCandidate,
    TranslationReviewCandidate,
    TranslationStoreV2,
)
from booktx.record_refs import parse_record_ref, parse_version_ref
from booktx.review_refs import parse_review_ref

from .paths import canonical_chunk_id, validate_relative_store_path

__all__ = [
    "CURRENT_SHARD_SCHEMA",
    "REVIEW_CANDIDATE_SHARD_SCHEMA",
    "StoreFormat",
    "StoreCommitResult",
    "StoreMigrationPlan",
    "StoreMigrationResult",
    "StoreMutationBatch",
    "STORE_MIGRATION_PLAN_SCHEMA",
    "STORE_V3_SCHEMA",
    "StoreTransactionJournal",
    "StoreTransactionWrite",
    "TRANSLATION_CANDIDATE_SHARD_SCHEMA",
    "TranslationStoreRepository",
    "V3CurrentRecord",
    "V3CurrentShard",
    "V3Manifest",
    "V3ManifestMigration",
    "V3ReviewCandidate",
    "V3ReviewRecord",
    "V3ReviewShard",
    "V3TranslationCandidate",
    "V3TranslationRecord",
    "V3TranslationShard",
    "validate_v3_shard_consistency",
]

T = TypeVar("T")

STORE_V3_SCHEMA = "booktx.translation-store.v3"
CURRENT_SHARD_SCHEMA = "booktx.translation-current-shard.v1"
TRANSLATION_CANDIDATE_SHARD_SCHEMA = "booktx.translation-candidate-shard.v1"
REVIEW_CANDIDATE_SHARD_SCHEMA = "booktx.review-candidate-shard.v1"
STORE_MIGRATION_PLAN_SCHEMA = "booktx.store-migration-plan.v1"


def _sha256_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _sorted_mapping(mapping: dict[str, T]) -> dict[str, T]:
    return dict(sorted(mapping.items(), key=lambda item: item[0]))


def _validate_record_key(record_id: str, *, chunk_id: int | str, part_id: int) -> None:
    ref = parse_record_ref(record_id)
    if canonical_chunk_id(ref.chunk_id) != canonical_chunk_id(chunk_id):
        raise ValueError(
            f"record id {record_id!r} does not belong to chunk "
            f"{canonical_chunk_id(chunk_id)!r}"
        )
    if ref.part_id != part_id:
        raise ValueError(
            f"record id {record_id!r} part {ref.part_id} "
            f"does not match part_id {part_id}"
        )


class StoreFormat(str, Enum):
    """Known canonical store formats."""

    MISSING = "missing"
    V1 = "v1"
    V2 = "v2"
    V3 = "v3"


@dataclass(slots=True)
class StoreMutationBatch:
    """Summary of a logical repository mutation."""

    summary: str = ""
    changed_record_ids: list[str] = field(default_factory=list)
    changed_chunk_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StoreCommitResult:
    """Result of persisting a canonical store mutation."""

    format: StoreFormat
    changed_chunk_ids: list[str] = field(default_factory=list)
    deleted_chunk_ids: list[str] = field(default_factory=list)
    changed_record_ids: list[str] = field(default_factory=list)
    transaction_id: str | None = None
    wrote_manifest: bool = False


@dataclass(slots=True)
class StoreMigrationPlan:
    """Planned migration or rollback action."""

    source_format: StoreFormat
    target_format: StoreFormat
    store_root: Path
    legacy_store_path: Path
    backup_path: Path | None = None
    report_path: Path | None = None
    dry_run: bool = True
    allow_source_drift: bool = False
    keep_legacy_copy: bool = False
    migration_id: str = ""
    stale_lock_policy: str = "reject"


@dataclass(slots=True)
class StoreMigrationResult:
    """Completed migration result."""

    plan: StoreMigrationPlan
    records: int
    chunk_ids: list[str]
    backup_path: Path | None = None
    report_path: Path | None = None
    changed: bool = False
    findings: list[dict[str, str | None]] = field(default_factory=list)
    source_drift_detected: bool = False
    parity_verified: bool = False
    backup_sha256: str | None = None
    report_sha256: str | None = None


class V3ManifestMigration(BaseModel):
    """Migration provenance recorded in the v3 manifest."""

    model_config = ConfigDict(extra="forbid")

    format: Literal[1, 2, 3]
    migration_id: str
    source_store_sha256: str


class V3Manifest(BaseModel):
    """Top-level v3 store manifest."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
    )

    schema_name: Literal["booktx.translation-store.v3"] = Field(
        default="booktx.translation-store.v3",
        alias="schema",
    )
    version: Literal[3] = 3
    format: Literal["v3"] = "v3"
    source_sha256: str = ""
    record_id_scheme: Literal["chunk-local:v1"] = "chunk-local:v1"
    shard_scheme: Literal["source-chunk:v1"] = "source-chunk:v1"
    chunk_ids: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    migrated_from: V3ManifestMigration | None = None

    @field_validator("chunk_ids")
    @classmethod
    def _chunk_ids_canonical(cls, chunk_ids: list[str]) -> list[str]:
        normalized = [canonical_chunk_id(chunk_id) for chunk_id in chunk_ids]
        if len(set(normalized)) != len(normalized):
            raise ValueError("chunk_ids must not contain duplicates")
        return sorted(normalized)


class V3CurrentRecord(BaseModel):
    """Current per-record state stored in the current shard."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: int
    part_id: int
    source_sha256: str = ""
    active_version: str | None = None
    active_review: str | None = None

    @field_validator("active_version")
    @classmethod
    def _active_version_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return parse_version_ref(value).version_ref

    @field_validator("active_review")
    @classmethod
    def _active_review_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return parse_review_ref(value).review_ref


class V3CurrentShard(BaseModel):
    """Current state for one chunk."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
    )

    schema_name: Literal["booktx.translation-current-shard.v1"] = Field(
        default="booktx.translation-current-shard.v1",
        alias="schema",
    )
    version: Literal[3] = 3
    chunk_id: str
    revision: int = 0
    records: dict[str, V3CurrentRecord] = Field(default_factory=dict)

    @field_validator("chunk_id")
    @classmethod
    def _chunk_id_canonical(cls, chunk_id: str) -> str:
        return canonical_chunk_id(chunk_id)

    @model_validator(mode="after")
    def _record_keys_match(self) -> V3CurrentShard:
        if self.revision < 0:
            raise ValueError("revision must be nonnegative")
        self.records = _sorted_mapping(self.records)
        for record_id, record in self.records.items():
            if canonical_chunk_id(record.chunk_id) != self.chunk_id:
                raise ValueError(
                    f"current record {record_id!r} chunk_id "
                    "does not match shard chunk_id"
                )
            _validate_record_key(
                record_id,
                chunk_id=self.chunk_id,
                part_id=record.part_id,
            )
        return self


class V3TranslationCandidate(TranslationCandidate):
    """Translation candidate envelope stored on disk."""

    source_sha256: str = ""
    target_sha256: str = ""

    @model_validator(mode="after")
    def _hash_matches_target(self) -> V3TranslationCandidate:
        expected = _sha256_text(self.target)
        if self.target_sha256 and self.target_sha256 != expected:
            raise ValueError("target_sha256 does not match target text")
        self.target_sha256 = expected
        return self


class V3TranslationRecord(BaseModel):
    """Translation candidates for one record."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: int
    part_id: int
    source_sha256: str = ""
    versions: list[V3TranslationCandidate] = Field(default_factory=list)

    @field_validator("versions", mode="before")
    @classmethod
    def _coerce_versions(cls, versions: list[object] | None) -> list[object]:
        if versions is None:
            return []
        coerced: list[object] = []
        for candidate in versions:
            if isinstance(candidate, TranslationCandidate):
                payload = candidate.model_dump(mode="python")
                payload.setdefault("source_sha256", "")
                payload.setdefault("target_sha256", "")
                coerced.append(payload)
            else:
                coerced.append(candidate)
        return coerced

    @model_validator(mode="after")
    def _normalize_versions(self) -> V3TranslationRecord:
        seen: set[str] = set()
        ordered = sorted(
            self.versions,
            key=lambda candidate: (
                candidate.version,
                candidate.subversion,
                candidate.version_ref,
            ),
        )
        for candidate in ordered:
            if candidate.version_ref in seen:
                raise ValueError(
                    f"duplicate version_ref {candidate.version_ref!r} in v3 record"
                )
            seen.add(candidate.version_ref)
            if not candidate.source_sha256:
                candidate.source_sha256 = self.source_sha256
            elif self.source_sha256 and candidate.source_sha256 != self.source_sha256:
                raise ValueError(
                    "candidate source_sha256 does not match record source_sha256"
                )
        self.versions = ordered
        return self


class V3TranslationShard(BaseModel):
    """Translation candidates for one chunk."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
    )

    schema_name: Literal["booktx.translation-candidate-shard.v1"] = Field(
        default="booktx.translation-candidate-shard.v1",
        alias="schema",
    )
    version: Literal[3] = 3
    chunk_id: str
    revision: int = 0
    records: dict[str, V3TranslationRecord] = Field(default_factory=dict)

    @field_validator("chunk_id")
    @classmethod
    def _chunk_id_canonical(cls, chunk_id: str) -> str:
        return canonical_chunk_id(chunk_id)

    @model_validator(mode="after")
    def _record_keys_match(self) -> V3TranslationShard:
        if self.revision < 0:
            raise ValueError("revision must be nonnegative")
        self.records = _sorted_mapping(self.records)
        for record_id, record in self.records.items():
            if canonical_chunk_id(record.chunk_id) != self.chunk_id:
                raise ValueError(
                    f"translation record {record_id!r} chunk_id "
                    "does not match shard chunk_id"
                )
            _validate_record_key(
                record_id,
                chunk_id=self.chunk_id,
                part_id=record.part_id,
            )
        return self


class V3ReviewCandidate(TranslationReviewCandidate):
    """Review candidate envelope stored on disk."""

    source_sha256: str = ""

    @model_validator(mode="after")
    def _hash_matches_target(self) -> V3ReviewCandidate:
        expected = _sha256_text(self.target)
        if self.target_sha256 != expected:
            raise ValueError("target_sha256 does not match target text")
        return self


class V3ReviewRecord(BaseModel):
    """Review candidates for one record."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: int
    part_id: int
    source_sha256: str = ""
    reviews: list[V3ReviewCandidate] = Field(default_factory=list)

    @field_validator("reviews", mode="before")
    @classmethod
    def _coerce_reviews(cls, reviews: list[object] | None) -> list[object]:
        if reviews is None:
            return []
        coerced: list[object] = []
        for candidate in reviews:
            if isinstance(candidate, TranslationReviewCandidate):
                payload = candidate.model_dump(mode="python")
                payload.setdefault("source_sha256", "")
                coerced.append(payload)
            else:
                coerced.append(candidate)
        return coerced

    @model_validator(mode="after")
    def _normalize_reviews(self) -> V3ReviewRecord:
        seen: set[str] = set()
        ordered = sorted(
            self.reviews,
            key=lambda candidate: (
                candidate.pass_number,
                candidate.run_number,
                candidate.review_ref,
            ),
        )
        for candidate in ordered:
            if candidate.review_ref in seen:
                raise ValueError(
                    f"duplicate review_ref {candidate.review_ref!r} in v3 record"
                )
            seen.add(candidate.review_ref)
            if not candidate.source_sha256:
                candidate.source_sha256 = self.source_sha256
            elif self.source_sha256 and candidate.source_sha256 != self.source_sha256:
                raise ValueError(
                    "candidate source_sha256 does not match record source_sha256"
                )
        self.reviews = ordered
        return self


class V3ReviewShard(BaseModel):
    """Review candidates for one chunk."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
    )

    schema_name: Literal["booktx.review-candidate-shard.v1"] = Field(
        default="booktx.review-candidate-shard.v1",
        alias="schema",
    )
    version: Literal[3] = 3
    chunk_id: str
    revision: int = 0
    records: dict[str, V3ReviewRecord] = Field(default_factory=dict)

    @field_validator("chunk_id")
    @classmethod
    def _chunk_id_canonical(cls, chunk_id: str) -> str:
        return canonical_chunk_id(chunk_id)

    @model_validator(mode="after")
    def _record_keys_match(self) -> V3ReviewShard:
        if self.revision < 0:
            raise ValueError("revision must be nonnegative")
        self.records = _sorted_mapping(self.records)
        for record_id, record in self.records.items():
            if canonical_chunk_id(record.chunk_id) != self.chunk_id:
                raise ValueError(
                    f"review record {record_id!r} chunk_id "
                    "does not match shard chunk_id"
                )
            _validate_record_key(
                record_id,
                chunk_id=self.chunk_id,
                part_id=record.part_id,
            )
        return self


class StoreTransactionWrite(BaseModel):
    """One staged write in a v3 transaction journal."""

    model_config = ConfigDict(extra="forbid")

    relative_path: str
    group: Literal["translation", "review", "current", "manifest", "other"]
    expected_sha256: str | None = None
    staged_sha256: str | None = None

    @field_validator("relative_path")
    @classmethod
    def _relative_path_safe(cls, relative_path: str) -> str:
        return validate_relative_store_path(relative_path)


class StoreTransactionJournal(BaseModel):
    """Durable description of a staged v3 transaction."""

    model_config = ConfigDict(extra="forbid")

    transaction_id: str
    created_at: str
    status: Literal["prepared", "committed"] = "prepared"
    writes: list[StoreTransactionWrite] = Field(default_factory=list)
    deletes: list[str] = Field(default_factory=list)

    @field_validator("deletes")
    @classmethod
    def _delete_paths_safe(cls, deletes: list[str]) -> list[str]:
        return sorted(validate_relative_store_path(path) for path in deletes)


def _to_domain_candidate(candidate: V3TranslationCandidate) -> TranslationCandidate:
    payload = candidate.model_dump(
        mode="python",
        exclude={"source_sha256", "target_sha256"},
    )
    return TranslationCandidate.model_validate(payload)


def _to_domain_review(candidate: V3ReviewCandidate) -> TranslationReviewCandidate:
    payload = candidate.model_dump(mode="python", exclude={"source_sha256"})
    return TranslationReviewCandidate.model_validate(payload)


def validate_v3_shard_consistency(
    *,
    current: V3CurrentShard | None,
    translations: V3TranslationShard | None,
    reviews: V3ReviewShard | None,
) -> None:
    """Validate cross-shard pointer, graph, and selection invariants."""

    shard_chunk_ids = {
        shard.chunk_id
        for shard in (current, translations, reviews)
        if shard is not None
    }
    if len(shard_chunk_ids) > 1:
        raise ValueError("v3 shards must describe the same chunk")

    record_ids = sorted(
        set(current.records if current is not None else {})
        | set(translations.records if translations is not None else {})
        | set(reviews.records if reviews is not None else {})
    )
    for record_id in record_ids:
        ref = parse_record_ref(record_id)
        current_record = current.records.get(record_id) if current is not None else None
        translation_record = (
            translations.records.get(record_id) if translations is not None else None
        )
        review_record = reviews.records.get(record_id) if reviews is not None else None

        source_hashes = {
            hash_value
            for hash_value in [
                current_record.source_sha256 if current_record is not None else "",
                (
                    translation_record.source_sha256
                    if translation_record is not None
                    else ""
                ),
                review_record.source_sha256 if review_record is not None else "",
            ]
            if hash_value
        }
        if len(source_hashes) > 1:
            raise ValueError(
                f"record {record_id!r} has conflicting source_sha256 values"
            )

        materialized = StoredTranslationRecordV2(
            chunk_id=ref.chunk_id,
            part_id=ref.part_id,
            source_sha256=next(iter(source_hashes), ""),
            source="",
            active_version=(
                current_record.active_version if current_record is not None else None
            ),
            active_review=(
                current_record.active_review if current_record is not None else None
            ),
            versions=[
                _to_domain_candidate(candidate)
                for candidate in (
                    translation_record.versions
                    if translation_record is not None
                    else []
                )
            ],
            reviews=[
                _to_domain_review(candidate)
                for candidate in (
                    review_record.reviews if review_record is not None else []
                )
            ],
        )
        if materialized.active_review is not None:
            from booktx.translation_store import (
                EffectiveCandidateError,
                effective_candidate_selection,
            )

            selection = effective_candidate_selection(
                materialized,
                strict_active_review=True,
            )
            if isinstance(selection, EffectiveCandidateError):
                raise ValueError(selection.message)


class TranslationStoreRepository(Protocol):
    """Backend-neutral repository contract."""

    format: StoreFormat

    def materialize_v2(self) -> TranslationStoreV2:
        """Return the canonical store as the legacy-compatible v2 model."""

    def write_materialized_v2(self, store: TranslationStoreV2) -> StoreCommitResult:
        """Persist a materialized v2 store into this backend."""

    def edit_v2(
        self, mutator: Callable[[TranslationStoreV2], T], *, summary: str = ""
    ) -> T:
        """Load, mutate, and persist the store atomically for this backend."""

    def edit_records(
        self,
        record_ids: Iterable[str],
        mutator: Callable[[TranslationStoreV2], T],
        *,
        summary: str = "",
        source_sha256: str | None = None,
    ) -> T:
        """Load, mutate, and persist only the affected records or chunks."""

    def get_record(self, record_id: str) -> StoredTranslationRecordV2 | None:
        """Return one record if present."""

    def iter_records(self) -> Iterator[tuple[str, StoredTranslationRecordV2]]:
        """Iterate all records in canonical-id order."""

    def iter_chunk_records(
        self, chunk_id: int | str
    ) -> Iterator[tuple[str, StoredTranslationRecordV2]]:
        """Iterate all records for one chunk."""

    def is_empty(self) -> bool:
        """Return whether the store contains any records."""

    def clear_all(self, *, source_sha256: str = "") -> StoreCommitResult:
        """Reset the backend to an empty canonical store."""

    def update_source_sha256(self, source_sha256: str) -> StoreCommitResult:
        """Persist a new store-level source identity without changing records."""
