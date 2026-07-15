from __future__ import annotations

import json
import os
from hashlib import sha256

import pytest

from booktx.errors import BooktxError
from booktx.store.models import (
    CURRENT_SHARD_SCHEMA,
    REVIEW_CANDIDATE_SHARD_SCHEMA,
    TRANSLATION_CANDIDATE_SHARD_SCHEMA,
)
from booktx.store.transactions import commit_v3_transaction, recover_v3_transactions


def _sha(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _payloads() -> dict[str, str]:
    return {
        "translation-candidates/0001.json": json.dumps(
            {
                "schema": TRANSLATION_CANDIDATE_SHARD_SCHEMA,
                "version": 3,
                "chunk_id": "0001",
                "revision": 1,
                "records": {},
            },
            indent=2,
        )
        + "\n",
        "review-candidates/0001.json": json.dumps(
            {
                "schema": REVIEW_CANDIDATE_SHARD_SCHEMA,
                "version": 3,
                "chunk_id": "0001",
                "revision": 1,
                "records": {},
            },
            indent=2,
        )
        + "\n",
        "current/0001.json": json.dumps(
            {
                "schema": CURRENT_SHARD_SCHEMA,
                "version": 3,
                "chunk_id": "0001",
                "revision": 1,
                "records": {
                    "0001-000001": {
                        "chunk_id": 1,
                        "part_id": 1,
                        "source_sha256": "src-sha",
                        "active_version": "1.1",
                        "active_review": None,
                    }
                },
            },
            indent=2,
        )
        + "\n",
        "manifest.json": json.dumps(
            {
                "schema": "booktx.translation-store.v3",
                "version": 3,
                "format": "v3",
                "source_sha256": "src-sha",
                "record_id_scheme": "chunk-local:v1",
                "shard_scheme": "source-chunk:v1",
                "chunk_ids": ["0001"],
                "created_at": "2026-06-22T12:00:00Z",
                "updated_at": "2026-06-22T12:00:00Z",
            },
            indent=2,
        )
        + "\n",
    }


def _commit_kwargs() -> dict[str, object]:
    payloads = _payloads()
    return {
        "relative_to_text": payloads,
        "deletes": [],
        "changed_chunk_ids": ["0001"],
        "deleted_chunk_ids": [],
        "changed_record_ids": ["0001-000001"],
        "wrote_manifest": True,
    }


def _transaction_dir(transactions_root):
    entries = [path for path in transactions_root.iterdir() if path.is_dir()]
    assert len(entries) == 1
    return entries[0]


def _write_lock(root, *, pid: int) -> None:
    lock_dir = root / ".write-lock"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "owner.json").write_text(
        json.dumps(
            {
                "pid": pid,
                "hostname": "test-host",
                "created_at": "2026-06-22T12:00:00Z",
                "command": "test",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "fail_stage",
    [
        "after_journal_prepared",
        "after_translation_publish",
        "after_review_publish",
        "after_first_current_publish",
        "before_commit_marker",
        "after_commit_marker_before_cleanup",
    ],
)
def test_recovery_rolls_forward_after_injected_failures(tmp_path, fail_stage):
    root = tmp_path / "translation-store"
    transactions_root = root / "transactions"
    payloads = _payloads()

    with pytest.raises(BooktxError):
        commit_v3_transaction(
            transactions_root=transactions_root,
            store_root=root,
            fail_stage=fail_stage,
            **_commit_kwargs(),
        )

    recover_v3_transactions(transactions_root, root)

    for relative_path, expected_text in payloads.items():
        assert (root / relative_path).read_text("utf-8") == expected_text


def test_recovery_blocks_on_corrupt_staged_hash(tmp_path):
    root = tmp_path / "translation-store"
    transactions_root = root / "transactions"
    with pytest.raises(BooktxError):
        commit_v3_transaction(
            transactions_root=transactions_root,
            store_root=root,
            fail_stage="after_journal_prepared",
            **_commit_kwargs(),
        )

    tx_dir = _transaction_dir(transactions_root)
    staged = tx_dir / "staged" / "current" / "0001.json"
    staged.write_text('{"tampered": true}\n', encoding="utf-8")

    with pytest.raises(BooktxError, match="staged hash mismatch") as excinfo:
        recover_v3_transactions(transactions_root, root)
    assert excinfo.value.code == "store_recovery_required"
    assert tx_dir.exists()


def test_recovery_blocks_on_contradictory_published_hash(tmp_path):
    root = tmp_path / "translation-store"
    transactions_root = root / "transactions"
    with pytest.raises(BooktxError):
        commit_v3_transaction(
            transactions_root=transactions_root,
            store_root=root,
            fail_stage="after_translation_publish",
            **_commit_kwargs(),
        )

    tampered_target = root / "translation-candidates" / "0001.json"
    tampered_target.write_text('{"tampered": true}\n', encoding="utf-8")

    with pytest.raises(BooktxError, match="contradictory published hash") as excinfo:
        recover_v3_transactions(transactions_root, root)
    assert excinfo.value.code == "store_recovery_required"


def test_commit_fails_when_live_lock_exists(tmp_path):
    root = tmp_path / "translation-store"
    _write_lock(root, pid=os.getpid())

    with pytest.raises(BooktxError, match="held by another process") as excinfo:
        commit_v3_transaction(
            transactions_root=root / "transactions",
            store_root=root,
            **_commit_kwargs(),
        )
    assert excinfo.value.code == "translation_store_locked"


def test_stale_lock_requires_explicit_policy_and_repair_succeeds(tmp_path):
    root = tmp_path / "translation-store"
    _write_lock(root, pid=999_999_999)

    with pytest.raises(BooktxError, match="stale") as excinfo:
        commit_v3_transaction(
            transactions_root=root / "transactions",
            store_root=root,
            **_commit_kwargs(),
        )
    assert excinfo.value.code == "translation_store_locked"

    result = commit_v3_transaction(
        transactions_root=root / "transactions",
        store_root=root,
        stale_lock_policy="repair",
        **_commit_kwargs(),
    )
    assert result.format.value == "v3"
    assert not (root / ".write-lock").exists()
    assert (root / "manifest.json").is_file()


def test_commit_rejects_optimistic_hash_mismatch(tmp_path):
    root = tmp_path / "translation-store"
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text('{"old": true}\n', encoding="utf-8")

    with pytest.raises(BooktxError, match="hash mismatch") as excinfo:
        commit_v3_transaction(
            transactions_root=root / "transactions",
            store_root=root,
            relative_to_text={"manifest.json": '{"new": true}\n'},
            deletes=[],
            changed_chunk_ids=[],
            deleted_chunk_ids=[],
            changed_record_ids=[],
            wrote_manifest=True,
            expected_hashes={"manifest.json": _sha('{"different": true}\n')},
        )
    assert excinfo.value.code == "store_concurrent_update"


def test_commit_rejects_optimistic_revision_mismatch(tmp_path):
    root = tmp_path / "translation-store"
    current_dir = root / "current"
    current_dir.mkdir(parents=True, exist_ok=True)
    existing_text = (
        json.dumps(
            {
                "schema": CURRENT_SHARD_SCHEMA,
                "version": 3,
                "chunk_id": "0001",
                "revision": 2,
                "records": {},
            },
            indent=2,
        )
        + "\n"
    )
    (current_dir / "0001.json").write_text(existing_text, encoding="utf-8")

    new_text = (
        json.dumps(
            {
                "schema": CURRENT_SHARD_SCHEMA,
                "version": 3,
                "chunk_id": "0001",
                "revision": 3,
                "records": {},
            },
            indent=2,
        )
        + "\n"
    )

    with pytest.raises(BooktxError, match="revision mismatch") as excinfo:
        commit_v3_transaction(
            transactions_root=root / "transactions",
            store_root=root,
            relative_to_text={"current/0001.json": new_text},
            deletes=[],
            changed_chunk_ids=["0001"],
            deleted_chunk_ids=[],
            changed_record_ids=[],
            wrote_manifest=False,
            expected_hashes={"current/0001.json": _sha(existing_text)},
            expected_revisions={"current/0001.json": 1},
        )
    assert excinfo.value.code == "store_concurrent_update"
