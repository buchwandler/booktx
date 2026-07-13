from __future__ import annotations

import json
from pathlib import Path

from booktx.config import (
    init_project,
    load_project,
    translation_store_path,
    translation_store_v3_manifest_path,
)
from booktx.io_utils import write_json_model_atomic
from booktx.models import (
    Chunk,
    Record,
    StoredTranslationRecordV2,
    TranslationCandidate,
    TranslationStoreV2,
)
from booktx.store import StoreFormat, detect_store_format, open_translation_store
from booktx.store.paths import current_shard_path


def _project_with_chunk(tmp_path: Path):
    proj = init_project(tmp_path / "book", target_language="de")
    (proj.source_dir / "story.md").write_text("# Demo\n\nHello.\n", encoding="utf-8")
    proj = load_project(proj.root, profile="de_default")
    proj.chunks_dir.mkdir(parents=True, exist_ok=True)
    write_json_model_atomic(
        proj.chunks_dir / "0001.json",
        Chunk(
            chunk_id="0001",
            source_language="en",
            records=[Record(id="0001-000001", source="Hello.")],
        ),
    )
    return proj


def test_new_profiles_default_to_v3_and_roundtrip_records(tmp_path: Path):
    proj = _project_with_chunk(tmp_path)
    store = TranslationStoreV2(
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
    )

    open_translation_store(proj, default_format=StoreFormat.V3).write_materialized_v2(
        store
    )

    assert detect_store_format(proj) == StoreFormat.V3
    assert translation_store_v3_manifest_path(proj).is_file()
    assert not translation_store_path(proj).is_file()

    repo = open_translation_store(proj, default_format=StoreFormat.V3)
    record = repo.get_record("0001-000001")
    assert record is not None
    assert record.source == "Hello."
    assert record.versions[0].target == "Hallo."
    assert current_shard_path(proj, "0001").is_file()


def test_v3_manifest_does_not_change_for_ordinary_record_updates(tmp_path: Path):
    proj = _project_with_chunk(tmp_path)
    repo = open_translation_store(proj, default_format=StoreFormat.V3)
    initial = TranslationStoreV2(
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
    )
    repo.write_materialized_v2(initial)
    before = json.loads(translation_store_v3_manifest_path(proj).read_text("utf-8"))

    updated = initial.model_copy(deep=True)
    updated.records["0001-000001"].versions[0].target = "Guten Tag."
    updated.records["0001-000001"].versions[0].updated_at = "2026-06-23T12:00:00Z"
    repo.write_materialized_v2(updated)
    after = json.loads(translation_store_v3_manifest_path(proj).read_text("utf-8"))

    assert after["chunk_ids"] == before["chunk_ids"]
    assert after["source_sha256"] == before["source_sha256"]
    assert after["updated_at"] == before["updated_at"]
