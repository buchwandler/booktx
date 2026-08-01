from __future__ import annotations

import json
from pathlib import Path

import pytest

from booktx.config import translation_store_v3_root
from booktx.store import StoreFormat
from booktx.store.doctor import inspect_store
from booktx.store.paths import (
    current_shard_path,
    review_candidates_shard_path,
    translation_candidates_shard_path,
)
from tests.store_backend_fixtures import create_rich_store_fixture


@pytest.mark.parametrize(
    ("role", "path_builder", "code"),
    [
        ("current", current_shard_path, "orphan_current_shard"),
        ("translation", translation_candidates_shard_path, "orphan_translation_shard"),
        ("review", review_candidates_shard_path, "orphan_review_shard"),
    ],
)
def test_v3_doctor_reports_orphan_shards(tmp_path: Path, role, path_builder, code):
    fixture = create_rich_store_fixture(tmp_path / role, store_format=StoreFormat.V3)
    orphan = path_builder(fixture.project, "0099")
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text("{}\n", encoding="utf-8")

    report = inspect_store(fixture.project)

    assert any(finding.code == code for finding in report.findings)


def test_v3_doctor_reports_incomplete_shard_set(tmp_path: Path):
    fixture = create_rich_store_fixture(
        tmp_path / "incomplete", store_format=StoreFormat.V3
    )
    chunk_id = fixture.record_ids["mantis"].split("-", 1)[0]
    review_candidates_shard_path(fixture.project, chunk_id).unlink()

    report = inspect_store(fixture.project)

    codes = {finding.code for finding in report.findings}
    assert "missing_review_shard" in codes
    assert "incomplete_chunk_shard_set" in codes


def test_v3_doctor_reports_revision_mismatch(tmp_path: Path):
    fixture = create_rich_store_fixture(
        tmp_path / "revision", store_format=StoreFormat.V3
    )
    chunk_id = fixture.record_ids["mantis"].split("-", 1)[0]
    path = current_shard_path(fixture.project, chunk_id)
    payload = json.loads(path.read_text("utf-8"))
    payload["revision"] += 1
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    report = inspect_store(fixture.project)

    assert any(finding.code == "revision_mismatch" for finding in report.findings)


def test_v3_doctor_reports_unexpected_store_files(tmp_path: Path):
    fixture = create_rich_store_fixture(
        tmp_path / "unexpected", store_format=StoreFormat.V3
    )
    root = translation_store_v3_root(fixture.project)
    (root / "unexpected.txt").write_text("unexpected\n", encoding="utf-8")
    (root / "current" / "junk.txt").write_text("unexpected\n", encoding="utf-8")

    report = inspect_store(fixture.project)

    assert (
        sum(finding.code == "unexpected_store_file" for finding in report.findings) == 2
    )
