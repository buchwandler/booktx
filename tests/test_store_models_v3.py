from __future__ import annotations

import pytest

from booktx.store.models import V3CurrentShard, V3Manifest


def test_v3_manifest_roundtrips():
    manifest = V3Manifest(
        source_sha256="abc123",
        chunk_ids=["0001", "0002"],
        created_at="2026-06-22T12:00:00Z",
        updated_at="2026-06-22T12:00:00Z",
    )
    assert V3Manifest.model_validate_json(manifest.model_dump_json()) == manifest


def test_v3_current_shard_rejects_unknown_fields():
    with pytest.raises(ValueError):
        V3CurrentShard.model_validate(
            {
                "chunk_id": "0001",
                "records": {},
                "unexpected": True,
            }
        )
