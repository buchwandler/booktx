"""Crash-safe staged publishing for the v3 translation store."""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from booktx.config import BooktxError, _err
from booktx.io_utils import utc_timestamp, write_json_model_atomic, write_text_atomic

from .models import (
    StoreCommitResult,
    StoreFormat,
    StoreTransactionJournal,
    StoreTransactionWrite,
)

__all__ = [
    "commit_v3_transaction",
    "recover_v3_transactions",
]


def _lock_path(transactions_root: Path) -> Path:
    return transactions_root / ".lock"


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


def _acquire_lock(transactions_root: Path) -> Path:
    transactions_root.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path(transactions_root)
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                payload = lock_path.read_text("utf-8").strip()
            except OSError:
                payload = ""
            pid = 0
            if payload.startswith("pid="):
                try:
                    pid = int(payload.split("=", 1)[1].split()[0])
                except ValueError:
                    pid = 0
            if pid and not _process_alive(pid):
                lock_path.unlink(missing_ok=True)
                continue
            raise _err(
                "translation_store_locked",
                "translation store transaction lock is held by another process",
            ) from None
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(f"pid={os.getpid()} created_at={utc_timestamp()}\n")
            return lock_path


def _release_lock(lock_path: Path) -> None:
    lock_path.unlink(missing_ok=True)


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
            "translation_store_recovery_failed",
            f"staged file is missing during recovery: {stage_path.as_posix()}",
        )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    stage_path.replace(target_path)


def _recover_one_transaction(
    transactions_root: Path,
    store_root: Path,
    tx_dir: Path,
) -> None:
    journal_path = tx_dir / "journal.json"
    if not journal_path.is_file():
        return
    try:
        journal = StoreTransactionJournal.model_validate_json(
            journal_path.read_text("utf-8")
        )
    except Exception as exc:  # noqa: BLE001
        raise _err(
            "translation_store_recovery_failed",
            f"invalid transaction journal at {journal_path.as_posix()}: {exc}",
        ) from exc
    committed_marker = tx_dir / "committed"
    if committed_marker.is_file():
        shutil.rmtree(tx_dir, ignore_errors=True)
        return
    staged_root = tx_dir / "staged"
    if not staged_root.is_dir():
        raise _err(
            "translation_store_recovery_failed",
            f"staging directory is missing for transaction {journal.transaction_id}",
        )
    for entry in journal.writes:
        _publish_staged_file(
            staged_root / entry.relative_path, store_root / entry.relative_path
        )
    for relative_path in journal.deletes:
        (store_root / relative_path).unlink(missing_ok=True)
    write_text_atomic(committed_marker, "committed\n")
    shutil.rmtree(tx_dir, ignore_errors=True)


def recover_v3_transactions(transactions_root: Path, store_root: Path) -> None:
    """Replay or clean up any staged v3 transactions."""

    if not transactions_root.is_dir():
        return
    for tx_dir in sorted(path for path in transactions_root.iterdir() if path.is_dir()):
        _recover_one_transaction(transactions_root, store_root, tx_dir)


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
    fail_stage: str | None = None,
) -> StoreCommitResult:
    """Commit a v3 store mutation through a staged transaction."""

    lock_path = _acquire_lock(transactions_root)
    try:
        recover_v3_transactions(transactions_root, store_root)
        transaction_id = f"tx-{uuid.uuid4().hex[:12]}"
        tx_dir = transactions_root / transaction_id
        staged_root = tx_dir / "staged"
        staged_root.mkdir(parents=True, exist_ok=True)
        sorted_writes = _sorted_writes(relative_to_text)
        for relative_path, text in sorted_writes:
            write_text_atomic(staged_root / relative_path, text)
        journal = StoreTransactionJournal(
            transaction_id=transaction_id,
            created_at=utc_timestamp(),
            writes=[
                StoreTransactionWrite(
                    relative_path=relative_path,
                    group=_write_group(relative_path),  # type: ignore[arg-type]
                )
                for relative_path, _text in sorted_writes
            ],
            deletes=sorted(deletes),
        )
        write_json_model_atomic(tx_dir / "journal.json", journal)
        _fail_if(fail_stage, "after_journal_prepared")

        first_current_published = False
        for relative_path, _text in sorted_writes:
            _publish_staged_file(
                staged_root / relative_path, store_root / relative_path
            )
            group = _write_group(relative_path)
            if group == "translation":
                _fail_if(fail_stage, "after_translation_publish")
            elif group == "review":
                _fail_if(fail_stage, "after_review_publish")
            elif group == "current" and not first_current_published:
                first_current_published = True
                _fail_if(fail_stage, "after_first_current_publish")

        for relative_path in deletes:
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
        _release_lock(lock_path)
