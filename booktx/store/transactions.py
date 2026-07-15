"""Crash-safe staged publishing for the v3 translation store."""

from __future__ import annotations

import json
import os
import shutil
import socket
import uuid
from hashlib import sha256
from pathlib import Path
from typing import Literal

from booktx.config import BooktxError, _err
from booktx.io_utils import utc_timestamp, write_json_model_atomic, write_text_atomic

from .models import (
    StoreCommitResult,
    StoreFormat,
    StoreTransactionJournal,
    StoreTransactionWrite,
)
from .paths import validate_relative_store_path

__all__ = [
    "commit_v3_transaction",
    "recover_v3_transactions",
]


def _file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return sha256(path.read_bytes()).hexdigest()


def _json_revision(path: Path) -> int | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    revision = payload.get("revision")
    return revision if isinstance(revision, int) else None


def _lock_dir(store_root: Path) -> Path:
    return store_root / ".write-lock"


def _lock_owner_path(store_root: Path) -> Path:
    return _lock_dir(store_root) / "owner.json"


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_lock_owner(store_root: Path) -> dict[str, object] | None:
    owner_path = _lock_owner_path(store_root)
    if not owner_path.is_file():
        return None
    try:
        payload = json.loads(owner_path.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return payload if isinstance(payload, dict) else None


def _owner_is_stale(owner: dict[str, object] | None) -> bool:
    if owner is None:
        return False
    pid = owner.get("pid")
    return isinstance(pid, int) and not _process_alive(pid)


def _acquire_lock(
    store_root: Path,
    *,
    stale_lock_policy: Literal["reject", "repair"],
) -> Path:
    store_root.mkdir(parents=True, exist_ok=True)
    lock_dir = _lock_dir(store_root)
    while True:
        try:
            lock_dir.mkdir()
        except FileExistsError:
            owner = _read_lock_owner(store_root)
            if _owner_is_stale(owner):
                if stale_lock_policy != "repair":
                    raise _err(
                        "translation_store_locked",
                        "translation store write lock is stale; rerun with explicit "
                        "stale-lock recovery",
                    ) from None
                shutil.rmtree(lock_dir, ignore_errors=True)
                continue
            raise _err(
                "translation_store_locked",
                "translation store write lock is held by another process",
            ) from None
        else:
            owner_payload = {
                "pid": os.getpid(),
                "hostname": socket.gethostname(),
                "created_at": utc_timestamp(),
                "command": "booktx.store.transactions.commit_v3_transaction",
            }
            write_text_atomic(
                _lock_owner_path(store_root),
                json.dumps(owner_payload, indent=2) + "\n",
            )
            return lock_dir


def _release_lock(lock_dir: Path) -> None:
    shutil.rmtree(lock_dir, ignore_errors=True)


def _write_group(relative_path: str) -> str:
    if relative_path.startswith("translation-candidates/"):
        return "translation"
    if relative_path.startswith("review-candidates/"):
        return "review"
    if relative_path.startswith("current/"):
        return "current"
    if relative_path == "manifest.json":
        return "manifest"
    return "other"


def _sorted_writes(relative_to_text: dict[str, str]) -> list[tuple[str, str]]:
    order = {"translation": 0, "review": 1, "current": 2, "manifest": 3, "other": 4}
    return sorted(
        relative_to_text.items(),
        key=lambda item: (order[_write_group(item[0])], item[0]),
    )


def _fail_if(stage: str | None, expected: str) -> None:
    if stage == expected:
        raise RuntimeError(f"injected store transaction failure at {expected}")


def _publish_staged_file(stage_path: Path, target_path: Path) -> None:
    if not stage_path.is_file():
        raise _err(
            "store_recovery_required",
            f"staged file is missing during recovery: {stage_path.as_posix()}",
        )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    stage_path.replace(target_path)


def _verify_expected_state(
    store_root: Path,
    *,
    expected_hashes: dict[str, str | None] | None,
    expected_revisions: dict[str, int | None] | None,
) -> None:
    for relative_path, expected_hash in (expected_hashes or {}).items():
        safe_path = validate_relative_store_path(relative_path)
        actual_hash = _file_sha256(store_root / safe_path)
        if actual_hash != expected_hash:
            raise _err(
                "store_concurrent_update",
                f"hash mismatch for {safe_path}: expected {expected_hash!r}, "
                f"found {actual_hash!r}",
            )
    for relative_path, expected_revision in (expected_revisions or {}).items():
        safe_path = validate_relative_store_path(relative_path)
        actual_revision = _json_revision(store_root / safe_path)
        if actual_revision != expected_revision:
            raise _err(
                "store_concurrent_update",
                f"revision mismatch for {safe_path}: expected {expected_revision!r}, "
                f"found {actual_revision!r}",
            )


def _ensure_staged_hash(stage_path: Path, expected_hash: str | None) -> None:
    if expected_hash is None:
        return
    actual_hash = _file_sha256(stage_path)
    if actual_hash != expected_hash:
        raise _err(
            "store_recovery_required",
            f"staged hash mismatch for {stage_path.as_posix()}: expected "
            f"{expected_hash!r}, found {actual_hash!r}",
        )


def _recover_one_transaction(store_root: Path, tx_dir: Path) -> None:
    journal_path = tx_dir / "journal.json"
    if not journal_path.is_file():
        return
    try:
        journal = StoreTransactionJournal.model_validate_json(
            journal_path.read_text("utf-8")
        )
    except Exception as exc:  # noqa: BLE001
        raise _err(
            "store_recovery_required",
            f"invalid transaction journal at {journal_path.as_posix()}: {exc}",
        ) from exc
    committed_marker = tx_dir / "committed"
    if committed_marker.is_file() or journal.status == "committed":
        shutil.rmtree(tx_dir, ignore_errors=True)
        return

    staged_root = tx_dir / "staged"
    if not staged_root.is_dir():
        raise _err(
            "store_recovery_required",
            f"staging directory is missing for transaction {journal.transaction_id}",
        )

    for entry in journal.writes:
        target_path = store_root / entry.relative_path
        target_hash = _file_sha256(target_path)
        if entry.staged_sha256 is not None and target_hash == entry.staged_sha256:
            continue
        if target_hash != entry.expected_sha256 and target_hash is not None:
            raise _err(
                "store_recovery_required",
                f"contradictory published hash for {entry.relative_path}: expected "
                f"{entry.expected_sha256!r}, staged {entry.staged_sha256!r}, "
                f"found {target_hash!r}",
            )
        stage_path = staged_root / entry.relative_path
        _ensure_staged_hash(stage_path, entry.staged_sha256)
        _publish_staged_file(stage_path, target_path)

    for relative_path in journal.deletes:
        (store_root / relative_path).unlink(missing_ok=True)

    write_text_atomic(committed_marker, "committed\n")
    shutil.rmtree(tx_dir, ignore_errors=True)


def recover_v3_transactions(transactions_root: Path, store_root: Path) -> None:
    """Replay or clean up any staged v3 transactions."""

    if not transactions_root.is_dir():
        return
    for tx_path in sorted(
        path for path in transactions_root.iterdir() if path.is_dir()
    ):
        _recover_one_transaction(store_root, tx_path)


def commit_v3_transaction(
    transactions_root: Path,
    store_root: Path,
    *,
    relative_to_text: dict[str, str],
    deletes: list[str],
    changed_chunk_ids: list[str],
    deleted_chunk_ids: list[str],
    changed_record_ids: list[str],
    wrote_manifest: bool,
    expected_hashes: dict[str, str | None] | None = None,
    expected_revisions: dict[str, int | None] | None = None,
    stale_lock_policy: Literal["reject", "repair"] = "reject",
    fail_stage: str | None = None,
) -> StoreCommitResult:
    """Commit a v3 store mutation through a staged transaction."""

    lock_dir = _acquire_lock(store_root, stale_lock_policy=stale_lock_policy)
    try:
        recover_v3_transactions(transactions_root, store_root)
        _verify_expected_state(
            store_root,
            expected_hashes=expected_hashes,
            expected_revisions=expected_revisions,
        )

        transaction_id = f"tx-{uuid.uuid4().hex[:12]}"
        tx_dir = transactions_root / transaction_id
        staged_root = tx_dir / "staged"
        staged_root.mkdir(parents=True, exist_ok=True)

        sorted_writes = _sorted_writes(relative_to_text)
        journal_writes: list[StoreTransactionWrite] = []
        for relative_path, text in sorted_writes:
            safe_path = validate_relative_store_path(relative_path)
            write_text_atomic(staged_root / safe_path, text)
            journal_writes.append(
                StoreTransactionWrite(
                    relative_path=safe_path,
                    group=_write_group(safe_path),  # type: ignore[arg-type]
                    expected_sha256=_file_sha256(store_root / safe_path),
                    staged_sha256=sha256(text.encode("utf-8")).hexdigest(),
                )
            )

        journal = StoreTransactionJournal(
            transaction_id=transaction_id,
            created_at=utc_timestamp(),
            status="prepared",
            writes=journal_writes,
            deletes=sorted(validate_relative_store_path(path) for path in deletes),
        )
        write_json_model_atomic(tx_dir / "journal.json", journal)
        write_text_atomic(tx_dir / "prepared", "prepared\n")
        _fail_if(fail_stage, "after_journal_prepared")

        first_current_published = False
        for entry in journal.writes:
            _publish_staged_file(
                staged_root / entry.relative_path,
                store_root / entry.relative_path,
            )
            if entry.group == "translation":
                _fail_if(fail_stage, "after_translation_publish")
            elif entry.group == "review":
                _fail_if(fail_stage, "after_review_publish")
            elif entry.group == "current" and not first_current_published:
                first_current_published = True
                _fail_if(fail_stage, "after_first_current_publish")

        for relative_path in journal.deletes:
            (store_root / relative_path).unlink(missing_ok=True)

        _fail_if(fail_stage, "before_commit_marker")
        write_text_atomic(tx_dir / "committed", "committed\n")
        _fail_if(fail_stage, "after_commit_marker_before_cleanup")
        shutil.rmtree(tx_dir, ignore_errors=True)
        return StoreCommitResult(
            format=StoreFormat.V3,
            changed_chunk_ids=sorted(changed_chunk_ids),
            deleted_chunk_ids=sorted(deleted_chunk_ids),
            changed_record_ids=sorted(changed_record_ids),
            transaction_id=transaction_id,
            wrote_manifest=wrote_manifest,
        )
    except BooktxError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _err("translation_store_commit_failed", str(exc)) from exc
    finally:
        _release_lock(lock_dir)
