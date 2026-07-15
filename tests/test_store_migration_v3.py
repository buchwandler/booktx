from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from booktx.config import (
    BooktxError,
    current_source_sha256,
    init_project,
    load_project,
    translation_store_path,
    translation_store_v3_root,
)
from booktx.io_utils import write_json_model_atomic
from booktx.models import (
    Chunk,
    Record,
    StoredTranslationRecordV2,
    TranslationCandidate,
    TranslationStoreV2,
)
from booktx.store import StoreFormat, detect_store_format
from booktx.store.doctor import inspect_store
from booktx.store.migration import execute_store_migration


def _legacy_v2_project(tmp_path: Path):
    proj = init_project(tmp_path / "book", target_language="de")
    (proj.source_dir / "story.md").write_text("# Demo\n\nHello.\n", encoding="utf-8")
    proj = load_project(proj.root, profile="de_default")
    v3_root = translation_store_v3_root(proj)
    if v3_root.exists():
        shutil.rmtree(v3_root)
    proj.chunks_dir.mkdir(parents=True, exist_ok=True)
    write_json_model_atomic(
        proj.chunks_dir / "0001.json",
        Chunk(
            chunk_id="0001",
            source_language="en",
            records=[Record(id="0001-000001", source="Hello.")],
        ),
    )
    write_json_model_atomic(
        translation_store_path(proj),
        TranslationStoreV2(
            source_sha256=current_source_sha256(proj),
            records={
                "0001-000001": StoredTranslationRecordV2(
                    chunk_id=1,
                    part_id=1,
                    source_sha256="source-sha",
                    source="Hello.",
                    active_version="1.1",
                    versions=[
                        TranslationCandidate(
                            version=1,
                            subversion=1,
                            version_ref="1.1",
                            target="Hallo.",
                            created_at="2026-06-22T12:00:00Z",
                            updated_at="2026-06-22T12:00:00Z",
                        )
                    ],
                )
            },
        ),
    )
    return proj


def test_migrate_v2_to_v3_and_back(tmp_path: Path):
    proj = _legacy_v2_project(tmp_path)
    assert detect_store_format(proj) == StoreFormat.V2

    dry_run = execute_store_migration(proj, target_format=StoreFormat.V3, dry_run=True)
    assert dry_run.records == 1
    assert dry_run.report_path is not None and dry_run.report_path.is_file()
    assert detect_store_format(proj) == StoreFormat.V2

    migrated = execute_store_migration(
        proj, target_format=StoreFormat.V3, dry_run=False
    )
    assert migrated.backup_path is not None and migrated.backup_path.is_file()
    assert detect_store_format(proj) == StoreFormat.V3
    assert not translation_store_path(proj).exists()

    rolled_back = execute_store_migration(
        proj, target_format=StoreFormat.V2, dry_run=False
    )
    assert rolled_back.backup_path is not None and rolled_back.backup_path.is_dir()
    assert detect_store_format(proj) == StoreFormat.V2
    assert translation_store_path(proj).is_file()


def test_migration_dry_run_reports_source_drift_without_mutating(tmp_path: Path):
    proj = _legacy_v2_project(tmp_path)
    store = TranslationStoreV2.model_validate_json(
        translation_store_path(proj).read_text("utf-8")
    )
    store.source_sha256 = "stale-source-sha"
    write_json_model_atomic(translation_store_path(proj), store)

    result = execute_store_migration(proj, target_format=StoreFormat.V3, dry_run=True)

    assert result.changed is False
    assert result.source_drift_detected is True
    assert any(finding["code"] == "source_drift" for finding in result.findings)
    assert detect_store_format(proj) == StoreFormat.V2


def test_migration_write_blocks_on_source_drift_without_override(tmp_path: Path):
    proj = _legacy_v2_project(tmp_path)
    store = TranslationStoreV2.model_validate_json(
        translation_store_path(proj).read_text("utf-8")
    )
    store.source_sha256 = "stale-source-sha"
    write_json_model_atomic(translation_store_path(proj), store)

    with pytest.raises(BooktxError, match="store migration blocked"):
        execute_store_migration(proj, target_format=StoreFormat.V3, dry_run=False)

    assert detect_store_format(proj) == StoreFormat.V2


def test_migration_write_can_keep_legacy_copy_when_migrating_to_v3(tmp_path: Path):
    proj = _legacy_v2_project(tmp_path)

    result = execute_store_migration(
        proj,
        target_format=StoreFormat.V3,
        dry_run=False,
        keep_legacy_copy=True,
    )

    assert result.changed is True
    assert detect_store_format(proj) == StoreFormat.V3
    assert translation_store_path(proj).is_file()


def test_store_doctor_reports_pending_transaction_for_v3(tmp_path: Path):
    proj = _legacy_v2_project(tmp_path)
    execute_store_migration(proj, target_format=StoreFormat.V3, dry_run=False)
    transactions_root = translation_store_v3_root(proj) / "transactions"
    tx_dir = transactions_root / "tx-pending"
    tx_dir.mkdir(parents=True, exist_ok=True)
    (tx_dir / "journal.json").write_text(
        json.dumps(
            {
                "transaction_id": "tx-pending",
                "created_at": "2026-07-15T00:00:00Z",
                "status": "prepared",
                "writes": [],
                "deletes": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    report = inspect_store(proj)

    assert report.format == StoreFormat.V3
    assert any(finding.code == "pending_transaction" for finding in report.findings)
