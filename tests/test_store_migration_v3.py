from __future__ import annotations

import shutil
from pathlib import Path

from booktx.config import (
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
            source_sha256="src-sha",
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
