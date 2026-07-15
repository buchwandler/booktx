from __future__ import annotations

import pytest

from booktx.store.models import (
    CURRENT_SHARD_SCHEMA,
    STORE_V3_SCHEMA,
    StoreTransactionJournal,
    V3CurrentRecord,
    V3CurrentShard,
    V3Manifest,
    V3ManifestMigration,
    V3ReviewCandidate,
    V3ReviewRecord,
    V3ReviewShard,
    V3TranslationCandidate,
    V3TranslationRecord,
    V3TranslationShard,
    validate_v3_shard_consistency,
)
from booktx.store.paths import (
    chunk_id_filename,
    chunk_id_from_filename,
    validate_relative_store_path,
)
from booktx.translation_store import sha256_text

TS = "2026-06-22T12:00:00Z"


def _version(
    target: str,
    *,
    version_ref: str = "1.1",
    source_sha256: str = "src-sha",
    target_sha256: str | None = None,
) -> V3TranslationCandidate:
    version, subversion = (int(piece) for piece in version_ref.split("."))
    return V3TranslationCandidate(
        version=version,
        subversion=subversion,
        version_ref=version_ref,
        source_sha256=source_sha256,
        target=target,
        target_sha256=target_sha256 or sha256_text(target),
        created_at=TS,
        updated_at=TS,
    )


def _review(
    target: str,
    *,
    review_ref: str = "R1.1",
    base_kind: str = "translation",
    base_ref: str = "1.1",
    base_target: str,
    status: str = "accepted",
    source_sha256: str = "src-sha",
) -> V3ReviewCandidate:
    pass_number = int(review_ref.split("R", 1)[1].split(".", 1)[0])
    run_number = int(review_ref.split(".", 1)[1])
    return V3ReviewCandidate(
        pass_number=pass_number,
        run_number=run_number,
        review_ref=review_ref,
        source_sha256=source_sha256,
        base_kind=base_kind,  # type: ignore[arg-type]
        base_ref=base_ref,
        base_target_sha256=sha256_text(base_target),
        target=target,
        target_sha256=sha256_text(target),
        status=status,  # type: ignore[arg-type]
        created_at=TS,
        updated_at=TS,
    )


def test_v3_manifest_roundtrips():
    manifest = V3Manifest(
        source_sha256="abc123",
        chunk_ids=["0002", "0001"],
        created_at=TS,
        updated_at=TS,
        migrated_from=V3ManifestMigration(
            format=2,
            migration_id="btm-123",
            source_store_sha256="legacy-sha",
        ),
    )
    round_trip = V3Manifest.model_validate_json(manifest.model_dump_json())
    assert round_trip == manifest
    assert round_trip.model_dump(by_alias=True)["schema"] == STORE_V3_SCHEMA
    assert round_trip.record_id_scheme == "chunk-local:v1"
    assert round_trip.shard_scheme == "source-chunk:v1"
    assert round_trip.chunk_ids == ["0001", "0002"]


def test_v3_manifest_rejects_unknown_schema():
    with pytest.raises(ValueError):
        V3Manifest.model_validate(
            {
                "schema": "booktx.translation-store.v2",
                "version": 3,
            }
        )


def test_v3_current_shard_rejects_unknown_fields():
    with pytest.raises(ValueError):
        V3CurrentShard.model_validate(
            {
                "schema": CURRENT_SHARD_SCHEMA,
                "chunk_id": "0001",
                "records": {},
                "unexpected": True,
            }
        )


def test_path_helpers_enforce_canonical_chunk_filenames_and_relative_paths():
    assert chunk_id_filename("1") == "0001.json"
    assert chunk_id_from_filename("0001.json") == "0001"
    assert validate_relative_store_path("current/0001.json") == "current/0001.json"

    with pytest.raises(ValueError):
        chunk_id_from_filename("1.json")
    with pytest.raises(ValueError):
        chunk_id_from_filename("current/0001.json")
    with pytest.raises(ValueError):
        validate_relative_store_path("../manifest.json")


def test_current_shard_rejects_record_key_mismatch():
    with pytest.raises(ValueError):
        V3CurrentShard(
            chunk_id="0001",
            records={
                "0002-000001": V3CurrentRecord(
                    chunk_id=1,
                    part_id=1,
                    source_sha256="src-sha",
                    active_version="1.1",
                )
            },
        )


def test_translation_shard_normalizes_hashes_order_and_rejects_duplicate_refs():
    shard = V3TranslationShard(
        chunk_id="0001",
        revision=2,
        records={
            "0001-000001": V3TranslationRecord(
                chunk_id=1,
                part_id=1,
                source_sha256="src-sha",
                versions=[
                    _version("second", version_ref="1.2", target_sha256=""),
                    _version("first", version_ref="1.1"),
                ],
            )
        },
    )
    versions = shard.records["0001-000001"].versions
    assert [candidate.version_ref for candidate in versions] == ["1.1", "1.2"]
    assert versions[1].target_sha256 == sha256_text("second")

    with pytest.raises(ValueError):
        V3TranslationShard(
            chunk_id="0001",
            records={
                "0001-000001": V3TranslationRecord(
                    chunk_id=1,
                    part_id=1,
                    source_sha256="src-sha",
                    versions=[
                        _version("a", version_ref="1.1"),
                        _version("b", version_ref="1.1"),
                    ],
                )
            },
        )


def test_review_shard_rejects_target_hash_mismatch_and_duplicate_refs():
    with pytest.raises(ValueError):
        V3ReviewCandidate(
            pass_number=1,
            run_number=1,
            review_ref="R1.1",
            source_sha256="src-sha",
            base_kind="translation",
            base_ref="1.1",
            base_target_sha256=sha256_text("base"),
            target="polished",
            target_sha256="wrong",
            created_at=TS,
            updated_at=TS,
        )

    with pytest.raises(ValueError):
        V3ReviewShard(
            chunk_id="0001",
            records={
                "0001-000001": V3ReviewRecord(
                    chunk_id=1,
                    part_id=1,
                    source_sha256="src-sha",
                    reviews=[
                        _review("a", review_ref="R1.1", base_target="base"),
                        _review("b", review_ref="R1.1", base_target="base"),
                    ],
                )
            },
        )


def test_transaction_journal_rejects_traversal_paths():
    with pytest.raises(ValueError):
        StoreTransactionJournal(
            transaction_id="tx-123",
            created_at=TS,
            writes=[],
            deletes=["../manifest.json"],
        )


def test_validate_v3_shard_consistency_accepts_valid_same_pass_rerun_chain():
    current = V3CurrentShard(
        chunk_id="0001",
        records={
            "0001-000001": V3CurrentRecord(
                chunk_id=1,
                part_id=1,
                source_sha256="src-sha",
                active_version="1.1",
                active_review="R1.2",
            )
        },
    )
    translations = V3TranslationShard(
        chunk_id="0001",
        records={
            "0001-000001": V3TranslationRecord(
                chunk_id=1,
                part_id=1,
                source_sha256="src-sha",
                versions=[_version("first-pass", version_ref="1.1")],
            )
        },
    )
    reviews = V3ReviewShard(
        chunk_id="0001",
        records={
            "0001-000001": V3ReviewRecord(
                chunk_id=1,
                part_id=1,
                source_sha256="src-sha",
                reviews=[
                    _review("pass1", review_ref="R1.1", base_target="first-pass"),
                    _review(
                        "rerun",
                        review_ref="R1.2",
                        base_kind="review",
                        base_ref="R1.1",
                        base_target="pass1",
                    ),
                ],
            )
        },
    )
    validate_v3_shard_consistency(
        current=current,
        translations=translations,
        reviews=reviews,
    )


def test_validate_v3_shard_consistency_rejects_missing_active_pointer():
    with pytest.raises(ValueError, match="active_version"):
        validate_v3_shard_consistency(
            current=V3CurrentShard(
                chunk_id="0001",
                records={
                    "0001-000001": V3CurrentRecord(
                        chunk_id=1,
                        part_id=1,
                        source_sha256="src-sha",
                        active_version="1.2",
                    )
                },
            ),
            translations=V3TranslationShard(
                chunk_id="0001",
                records={
                    "0001-000001": V3TranslationRecord(
                        chunk_id=1,
                        part_id=1,
                        source_sha256="src-sha",
                        versions=[_version("first-pass", version_ref="1.1")],
                    )
                },
            ),
            reviews=None,
        )


def test_validate_v3_shard_consistency_rejects_rejected_active_review():
    with pytest.raises(ValueError, match="not accepted"):
        validate_v3_shard_consistency(
            current=V3CurrentShard(
                chunk_id="0001",
                records={
                    "0001-000001": V3CurrentRecord(
                        chunk_id=1,
                        part_id=1,
                        source_sha256="src-sha",
                        active_version="1.1",
                        active_review="R1.1",
                    )
                },
            ),
            translations=V3TranslationShard(
                chunk_id="0001",
                records={
                    "0001-000001": V3TranslationRecord(
                        chunk_id=1,
                        part_id=1,
                        source_sha256="src-sha",
                        versions=[_version("first-pass", version_ref="1.1")],
                    )
                },
            ),
            reviews=V3ReviewShard(
                chunk_id="0001",
                records={
                    "0001-000001": V3ReviewRecord(
                        chunk_id=1,
                        part_id=1,
                        source_sha256="src-sha",
                        reviews=[
                            _review(
                                "polished",
                                review_ref="R1.1",
                                base_target="first-pass",
                                status="rejected",
                            )
                        ],
                    )
                },
            ),
        )


def test_validate_v3_shard_consistency_rejects_review_cycle_and_bad_pass_order():
    base_current = V3CurrentShard(
        chunk_id="0001",
        records={
            "0001-000001": V3CurrentRecord(
                chunk_id=1,
                part_id=1,
                source_sha256="src-sha",
                active_version="1.1",
                active_review="R2.1",
            )
        },
    )
    base_translations = V3TranslationShard(
        chunk_id="0001",
        records={
            "0001-000001": V3TranslationRecord(
                chunk_id=1,
                part_id=1,
                source_sha256="src-sha",
                versions=[_version("first-pass", version_ref="1.1")],
            )
        },
    )

    with pytest.raises(ValueError, match="cycle detected"):
        validate_v3_shard_consistency(
            current=base_current,
            translations=base_translations,
            reviews=V3ReviewShard(
                chunk_id="0001",
                records={
                    "0001-000001": V3ReviewRecord(
                        chunk_id=1,
                        part_id=1,
                        source_sha256="src-sha",
                        reviews=[
                            _review(
                                "r1",
                                review_ref="R1.1",
                                base_kind="review",
                                base_ref="R2.1",
                                base_target="r2",
                            ),
                            _review(
                                "r2",
                                review_ref="R2.1",
                                base_kind="review",
                                base_ref="R1.1",
                                base_target="r1",
                            ),
                        ],
                    )
                },
            ),
        )

    with pytest.raises(ValueError, match="must be greater than base"):
        validate_v3_shard_consistency(
            current=V3CurrentShard(
                chunk_id="0001",
                records={
                    "0001-000001": V3CurrentRecord(
                        chunk_id=1,
                        part_id=1,
                        source_sha256="src-sha",
                        active_version="1.1",
                        active_review="R1.1",
                    )
                },
            ),
            translations=base_translations,
            reviews=V3ReviewShard(
                chunk_id="0001",
                records={
                    "0001-000001": V3ReviewRecord(
                        chunk_id=1,
                        part_id=1,
                        source_sha256="src-sha",
                        reviews=[
                            _review(
                                "r2",
                                review_ref="R1.2",
                                base_target="first-pass",
                            ),
                            _review(
                                "r1",
                                review_ref="R1.1",
                                base_kind="review",
                                base_ref="R1.2",
                                base_target="r2",
                            ),
                        ],
                    )
                },
            ),
        )
