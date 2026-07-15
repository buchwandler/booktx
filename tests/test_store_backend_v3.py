from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path

from booktx.config import (
    current_source_sha256,
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
from booktx.store import (
    StoreFormat,
    detect_store_format,
    execute_store_migration,
    open_translation_store,
)
from booktx.store.paths import (
    current_shard_path,
    review_candidates_shard_path,
    translation_candidates_shard_path,
)
from booktx.translation_store import upsert_translation_version
from tests.store_backend_fixtures import create_rich_store_fixture


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


def _file_sha(path: Path) -> str | None:
    if not path.is_file():
        return None
    return sha256(path.read_bytes()).hexdigest()


def test_new_profiles_default_to_v2_until_v3_is_opted_in(tmp_path: Path):
    proj = _project_with_chunk(tmp_path)
    assert detect_store_format(proj) == StoreFormat.V2
    assert translation_store_path(proj).is_file()
    assert not translation_store_v3_manifest_path(proj).is_file()

    store = TranslationStoreV2(
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
    )

    write_json_model_atomic(translation_store_path(proj), store)
    execute_store_migration(proj, target_format=StoreFormat.V3, dry_run=False)

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
    translation_store_path(proj).unlink()
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


def test_edit_records_updates_only_affected_chunk_shards(tmp_path: Path):
    fixture = create_rich_store_fixture(
        tmp_path / "bounded", store_format=StoreFormat.V3
    )
    repo = open_translation_store(fixture.project, default_format=StoreFormat.V3)
    record_id = fixture.record_ids["mantis"]
    affected_chunk = record_id.split("-", 1)[0]
    manifest_before = json.loads(
        translation_store_v3_manifest_path(fixture.project).read_text("utf-8")
    )
    chunk_ids = list(manifest_before["chunk_ids"])

    def _chunk_hashes(chunk_id: str) -> dict[str, str | None]:
        return {
            "current": _file_sha(current_shard_path(fixture.project, chunk_id)),
            "translation": _file_sha(
                translation_candidates_shard_path(fixture.project, chunk_id)
            ),
            "review": _file_sha(
                review_candidates_shard_path(fixture.project, chunk_id)
            ),
        }

    before = {chunk_id: _chunk_hashes(chunk_id) for chunk_id in chunk_ids}

    def _mutate(store: TranslationStoreV2) -> None:
        upsert_translation_version(
            store.records[record_id],
            "1.3",
            "Die Mantis-Kommandantin meldete sich.",
            updated_at="2026-06-23T12:00:00Z",
            activate=True,
        )

    repo.edit_records([record_id], _mutate, summary="bounded translation update")
    after = {chunk_id: _chunk_hashes(chunk_id) for chunk_id in chunk_ids}
    manifest_after = json.loads(
        translation_store_v3_manifest_path(fixture.project).read_text("utf-8")
    )

    assert after[affected_chunk]["current"] != before[affected_chunk]["current"]
    assert after[affected_chunk]["translation"] != before[affected_chunk]["translation"]
    assert after[affected_chunk]["review"] == before[affected_chunk]["review"]

    for chunk_id in chunk_ids:
        if chunk_id == affected_chunk:
            continue
        assert after[chunk_id] == before[chunk_id]

    assert manifest_after["chunk_ids"] == manifest_before["chunk_ids"]
    assert manifest_after["source_sha256"] == manifest_before["source_sha256"]
    assert manifest_after["updated_at"] == manifest_before["updated_at"]


def test_production_code_uses_full_store_writer_only_in_allowed_modules():
    root = Path(__file__).resolve().parents[1]
    package_root = root / "booktx"
    allowed = {
        "booktx/config.py",
        "booktx/store/migration.py",
        "booktx/store/v1_v2.py",
        "booktx/store/v3.py",
        "booktx/workflows/translate.py",
    }
    offenders: list[str] = []
    pattern = re.compile(r"\.write_materialized_v2\(")

    for path in sorted(package_root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel in allowed:
            continue
        for line_number, line in enumerate(
            path.read_text("utf-8").splitlines(), start=1
        ):
            if pattern.search(line):
                offenders.append(f"{rel}:{line_number}")

    assert offenders == []
