from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

import booktx.cli_support as cli_support
import booktx.workflows.translate as translate_workflows
from booktx.cli_support import _store_record_payload
from booktx.config import (
    current_source_sha256,
    init_project,
    load_project,
    translation_store_path,
    translation_store_v3_manifest_path,
)
from booktx.errors import BooktxError
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
    open_translation_store,
)
from booktx.store.paths import (
    current_shard_path,
    review_candidates_shard_path,
    translation_candidates_shard_path,
)
from booktx.translation_store import upsert_translation_version
from booktx.workflows.translate import (
    translation_list_workflow,
    translation_search_cmd_workflow,
)
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


class _NoMaterializeRepo:
    def __init__(self, repo: Any) -> None:
        self._repo = repo
        self.format = repo.format
        self.get_calls: list[str] = []
        self.iter_chunk_calls: list[str] = []
        self.iter_records_calls = 0
        self.materialize_calls = 0

    def materialize_v2(self) -> Any:
        self.materialize_calls += 1
        raise AssertionError("materialize_v2 must not be called")

    def get_record(self, record_id: str) -> Any:
        self.get_calls.append(record_id)
        return self._repo.get_record(record_id)

    def iter_records(self):
        self.iter_records_calls += 1
        yield from self._repo.iter_records()

    def iter_chunk_records(self, chunk_id: int | str):
        self.iter_chunk_calls.append(f"{int(chunk_id):04d}")
        yield from self._repo.iter_chunk_records(chunk_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._repo, name)


def test_new_profiles_default_to_v3(tmp_path: Path):
    proj = _project_with_chunk(tmp_path)
    assert detect_store_format(proj) == StoreFormat.V3
    assert translation_store_v3_manifest_path(proj).is_file()
    assert not translation_store_path(proj).is_file()

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

    repo = open_translation_store(proj, default_format=StoreFormat.V3)
    repo.write_materialized_v2(store)
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
    # All three envelopes advance together so readers can reject a mixed
    # publication even when one payload is unchanged.
    assert after[affected_chunk]["review"] != before[affected_chunk]["review"]

    revisions = []
    for kind in ("current", "translation", "review"):
        path = {
            "current": current_shard_path,
            "translation": translation_candidates_shard_path,
            "review": review_candidates_shard_path,
        }[kind](fixture.project, affected_chunk)
        revisions.append(json.loads(path.read_text("utf-8"))["revision"])
    assert revisions[0] == revisions[1] == revisions[2]

    for chunk_id in chunk_ids:
        if chunk_id == affected_chunk:
            continue
        assert after[chunk_id] == before[chunk_id]

    assert manifest_after["chunk_ids"] == manifest_before["chunk_ids"]
    assert manifest_after["source_sha256"] == manifest_before["source_sha256"]
    assert manifest_after["updated_at"] == manifest_before["updated_at"]


def test_v3_read_rejects_cross_shard_source_hash_conflict(tmp_path: Path):
    fixture = create_rich_store_fixture(
        tmp_path / "conflict", store_format=StoreFormat.V3
    )
    record_id = fixture.record_ids["mantis"]
    path = translation_candidates_shard_path(
        fixture.project, record_id.split("-", 1)[0]
    )
    payload = json.loads(path.read_text("utf-8"))
    conflicting = "conflicting-source-hash"
    payload["records"][record_id]["source_sha256"] = conflicting
    for candidate in payload["records"][record_id]["versions"]:
        candidate["source_sha256"] = conflicting
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    repo = open_translation_store(fixture.project, default_format=StoreFormat.V3)
    with pytest.raises(BooktxError, match="failed consistency checks") as excinfo:
        repo.get_record(record_id)
    assert excinfo.value.code == "invalid_translation_store"


def test_v3_read_rejects_mixed_shard_revisions(tmp_path: Path):
    fixture = create_rich_store_fixture(
        tmp_path / "mixed-revision", store_format=StoreFormat.V3
    )
    chunk_id = fixture.record_ids["mantis"].split("-", 1)[0]
    path = current_shard_path(fixture.project, chunk_id)
    payload = json.loads(path.read_text("utf-8"))
    payload["revision"] += 1
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    repo = open_translation_store(fixture.project, default_format=StoreFormat.V3)
    with pytest.raises(BooktxError, match="consistent v3 chunk snapshot") as excinfo:
        repo.get_record(fixture.record_ids["mantis"])
    assert excinfo.value.code == "store_concurrent_update"


def test_edit_records_rejects_cross_chunk_mutation(tmp_path: Path):
    fixture = create_rich_store_fixture(
        tmp_path / "scope-violation", store_format=StoreFormat.V3
    )
    repo = open_translation_store(fixture.project, default_format=StoreFormat.V3)
    source_id = fixture.record_ids["mantis"]
    other_id = fixture.record_ids["cicada"]

    def _mutate(store: TranslationStoreV2) -> None:
        store.records[other_id] = fixture.store.records[other_id].model_copy(deep=True)

    with pytest.raises(
        BooktxError, match="outside the requested chunk scope"
    ) as excinfo:
        repo.edit_records([source_id], _mutate, summary="scope violation")

    assert excinfo.value.code == "translation_store_scope_violation"
    assert repo.get_record(other_id) == fixture.store.records[other_id]


def test_store_record_payload_does_not_materialize_full_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fixture = create_rich_store_fixture(
        tmp_path / "payload", store_format=StoreFormat.V3, activate_stale_review=False
    )
    proxy = _NoMaterializeRepo(
        open_translation_store(fixture.project, default_format=StoreFormat.V3)
    )
    monkeypatch.setattr(cli_support, "open_translation_store", lambda _project: proxy)

    selected, details = _store_record_payload(
        fixture.project, fixture.record_ids["mantis"]
    )

    assert selected["id"] == fixture.record_ids["mantis"]
    assert details["stored"] is not None
    assert proxy.materialize_calls == 0
    assert proxy.iter_chunk_calls == ["0002"]


def test_translation_list_workflow_uses_chunk_reads_without_materializing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fixture = create_rich_store_fixture(
        tmp_path / "list", store_format=StoreFormat.V3, activate_stale_review=False
    )
    proxy = _NoMaterializeRepo(
        open_translation_store(fixture.project, default_format=StoreFormat.V3)
    )
    outputs: list[str] = []

    def _fail_loader(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("load_translation_store must not be used here")

    monkeypatch.setattr(cli_support, "open_translation_store", lambda _project: proxy)
    monkeypatch.setattr(translate_workflows, "load_translation_store", _fail_loader)
    monkeypatch.setattr(
        translate_workflows.console,
        "print_json",
        lambda payload: outputs.append(payload),
    )

    translation_list_workflow(
        fixture.project.root,
        chapter=1,
        profile="de_default",
        as_json=True,
    )

    assert outputs
    assert proxy.materialize_calls == 0
    assert proxy.iter_chunk_calls
    assert proxy.iter_records_calls == 0


def test_translation_search_workflow_streams_records_without_materializing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fixture = create_rich_store_fixture(
        tmp_path / "search", store_format=StoreFormat.V3, activate_stale_review=False
    )
    proxy = _NoMaterializeRepo(
        open_translation_store(fixture.project, default_format=StoreFormat.V3)
    )
    outputs: list[str] = []

    def _fail_loader(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("load_translation_store must not be used here")

    monkeypatch.setattr(cli_support, "open_translation_store", lambda _project: proxy)
    monkeypatch.setattr(translate_workflows, "load_translation_store", _fail_loader)
    monkeypatch.setattr(
        translate_workflows.console,
        "print_json",
        lambda payload: outputs.append(payload),
    )

    translation_search_cmd_workflow(
        fixture.project.root,
        profile="de_default",
        target="Kommandantin",
        as_json=True,
    )

    assert outputs
    assert proxy.materialize_calls == 0
    assert proxy.iter_records_calls == 1


def test_load_translation_store_is_limited_to_compat_full_store_paths():
    root = Path(__file__).resolve().parents[1]
    package_root = root / "booktx"
    allowed = {"booktx/config.py", "booktx/workflows/translate.py"}
    offenders: list[str] = []
    pattern = re.compile(r"\bload_translation_store\(")

    for path in sorted(package_root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        for line_number, line in enumerate(
            path.read_text("utf-8").splitlines(), start=1
        ):
            if pattern.search(line) and rel not in allowed:
                offenders.append(f"{rel}:{line_number}")

    assert offenders == []


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
