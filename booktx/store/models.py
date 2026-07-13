"""Store backend models and typed repository contracts."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from booktx.models import (
    StoredTranslationRecordV2,
    TranslationCandidate,
    TranslationReviewCandidate,
    TranslationStoreV2,
)

__all__ = [
    "StoreFormat",
    "StoreCommitResult",
    "StoreMigrationPlan",
    "StoreMigrationResult",
    "StoreMutationBatch",
    "TranslationStoreRepository",
    "V3CurrentRecord",
    "V3CurrentShard",
    "V3Manifest",
    "V3TranslationRecord",
    "V3TranslationShard",
    "V3ReviewRecord",
    "V3ReviewShard",
    "StoreTransactionJournal",
    "StoreTransactionWrite",
]

T = TypeVar("T")


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


@dataclass(slots=True)
class StoreMigrationResult:
    """Completed migration result."""

    plan: StoreMigrationPlan
    records: int
    chunk_ids: list[str]
    backup_path: Path | None = None
    report_path: Path | None = None
    changed: bool = False


class V3Manifest(BaseModel):
    """Top-level v3 store manifest."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[3] = 3
    format: Literal["v3"] = "v3"
    source_sha256: str = ""
    chunk_ids: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


class V3CurrentRecord(BaseModel):
    """Current per-record state stored in the current shard."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: int
    part_id: int
    source_sha256: str = ""
    active_version: str | None = None
    active_review: str | None = None


class V3CurrentShard(BaseModel):
    """Current state for one chunk."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[3] = 3
    chunk_id: str
    records: dict[str, V3CurrentRecord] = Field(default_factory=dict)


class V3TranslationRecord(BaseModel):
    """Translation candidates for one record."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: int
    part_id: int
    source_sha256: str = ""
    versions: list[TranslationCandidate] = Field(default_factory=list)


class V3TranslationShard(BaseModel):
    """Translation candidates for one chunk."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[3] = 3
    chunk_id: str
    records: dict[str, V3TranslationRecord] = Field(default_factory=dict)


class V3ReviewRecord(BaseModel):
    """Review candidates for one record."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: int
    part_id: int
    source_sha256: str = ""
    reviews: list[TranslationReviewCandidate] = Field(default_factory=list)


class V3ReviewShard(BaseModel):
    """Review candidates for one chunk."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[3] = 3
    chunk_id: str
    records: dict[str, V3ReviewRecord] = Field(default_factory=dict)


class StoreTransactionWrite(BaseModel):
    """One staged write in a v3 transaction journal."""

    model_config = ConfigDict(extra="forbid")

    relative_path: str
    group: Literal["translation", "review", "current", "manifest", "other"]


class StoreTransactionJournal(BaseModel):
    """Durable description of a staged v3 transaction."""

    model_config = ConfigDict(extra="forbid")

    transaction_id: str
    created_at: str
    writes: list[StoreTransactionWrite] = Field(default_factory=list)
    deletes: list[str] = Field(default_factory=list)


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
