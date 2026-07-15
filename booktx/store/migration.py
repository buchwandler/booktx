"""Safe migration and rollback helpers for canonical translation stores."""

from __future__ import annotations

import json
import os
import shutil
import socket
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Literal, cast

from booktx.config import (
    Project,
    _err,
    translation_store_path,
    translation_store_v3_root,
)
from booktx.io_utils import utc_timestamp, write_text_atomic
from booktx.models import TranslationStoreV2
from booktx.progress import load_source_records

from .detect import detect_store_format, open_translation_store
from .doctor import inspect_store
from .models import (
    StoreFormat,
    StoreMigrationPlan,
    StoreMigrationResult,
    V3Manifest,
    V3ManifestMigration,
)
from .paths import transactions_dir
from .transactions import recover_v3_transactions
from .v1_v2 import V1V2TranslationStoreRepository
from .v3 import V3TranslationStoreRepository

__all__ = ["execute_store_migration", "plan_store_migration"]


LockPolicy = Literal["reject", "repair"]


def _backup_base_dir(project: Project) -> Path:
    profile_dir = project.profile_dir or project.booktx_dir
    return profile_dir / "reports" / "store-migration"


def _migration_id() -> str:
    stamp = utc_timestamp().replace(":", "").replace("-", "")
    return f"migration-{stamp}-{uuid.uuid4().hex[:8]}"


def _source_store_hash(project: Project, store_format: StoreFormat) -> str | None:
    path = (
        translation_store_path(project)
        if store_format != StoreFormat.V3
        else translation_store_v3_root(project)
    )
    return _tree_sha256(path)


def _tree_sha256(path: Path) -> str | None:
    if path.is_file():
        return sha256(path.read_bytes()).hexdigest()
    if not path.is_dir():
        return None
    digest = sha256()
    for child in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(child.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256(child.read_bytes()).digest())
    return digest.hexdigest()


def _backup_path(base_dir: Path, migration_id: str, target: StoreFormat) -> Path:
    suffix = (
        "translation-store.v2.backup.json"
        if target == StoreFormat.V3
        else "translation-store.v3.backup"
    )
    return base_dir / f"{migration_id}-{suffix}"


def plan_store_migration(
    project: Project,
    *,
    target_format: StoreFormat,
    dry_run: bool = True,
    allow_source_drift: bool = False,
    backup_dir: Path | None = None,
    keep_legacy_copy: bool = False,
    stale_lock_policy: LockPolicy = "reject",
) -> StoreMigrationPlan:
    """Plan a migration or rollback without changing canonical state."""

    if target_format not in {StoreFormat.V2, StoreFormat.V3}:
        raise ValueError(f"unsupported migration target: {target_format.value}")
    migration_id = _migration_id()
    base_dir = backup_dir if backup_dir is not None else _backup_base_dir(project)
    source_format = detect_store_format(project)
    return StoreMigrationPlan(
        source_format=source_format,
        target_format=target_format,
        store_root=translation_store_v3_root(project),
        legacy_store_path=translation_store_path(project),
        backup_path=_backup_path(base_dir, migration_id, target_format),
        report_path=base_dir / f"{migration_id}-migration-report.json",
        dry_run=dry_run,
        allow_source_drift=allow_source_drift,
        keep_legacy_copy=keep_legacy_copy,
        migration_id=migration_id,
        stale_lock_policy=stale_lock_policy,
    )


def _finding(
    severity: str,
    code: str,
    message: str,
    path: str | None = None,
) -> dict[str, str | None]:
    return {"severity": severity, "code": code, "message": message, "path": path}


def _canonical_store(store: TranslationStoreV2) -> str:
    payload = store.model_dump(mode="json")
    payload["records"] = dict(sorted(payload["records"].items()))
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _copy_verified(source: Path, destination: Path) -> str:
    if destination.exists():
        if destination.is_dir():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination)
    elif source.is_file():
        shutil.copy2(source, destination)
    else:
        raise _err(
            "store_migration_backup_failed", f"backup source is missing: {source}"
        )
    source_hash = _tree_sha256(source)
    backup_hash = _tree_sha256(destination)
    if source_hash != backup_hash:
        raise _err(
            "store_migration_backup_failed",
            "verified backup hash mismatch for "
            f"{source}: {source_hash} != {backup_hash}",
        )
    if backup_hash is None:
        raise _err("store_migration_backup_failed", f"backup is empty: {destination}")
    return backup_hash


def _write_report(
    plan: StoreMigrationPlan, result: StoreMigrationResult
) -> StoreMigrationResult:
    assert plan.report_path is not None
    payload = {
        "schema": "booktx.store-migration-plan.v1",
        "migration_id": plan.migration_id,
        "created_at": utc_timestamp(),
        "source_format": plan.source_format.value,
        "target_format": plan.target_format.value,
        "records": result.records,
        "chunk_ids": result.chunk_ids,
        "backup_path": str(result.backup_path)
        if result.backup_path is not None
        else None,
        "changed": result.changed,
        "source_drift_detected": result.source_drift_detected,
        "parity_verified": result.parity_verified,
        "backup_sha256": result.backup_sha256,
        "findings": result.findings,
    }
    report_hash = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    payload["report_sha256"] = report_hash
    plan.report_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(
        plan.report_path, json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )
    return replace(result, report_sha256=report_hash)


def _blocking_findings(
    result: StoreMigrationResult,
    *,
    allow_source_drift: bool,
) -> list[dict[str, str | None]]:
    blocked: list[dict[str, str | None]] = []
    for finding in result.findings:
        if finding["code"] == "source_drift" and allow_source_drift:
            continue
        if finding["severity"] == "error":
            blocked.append(finding)
        elif finding["code"] == "source_drift":
            blocked.append(finding)
    return blocked


def _migration_lock_path(project: Project) -> Path:
    return translation_store_v3_root(project).with_name(
        "translation-store.migration.lock"
    )


def _owner_is_stale(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return False
    pid = payload.get("pid") if isinstance(payload, dict) else None
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


@contextmanager
def _migration_lock(project: Project, policy: LockPolicy) -> Iterator[None]:
    lock_path = _migration_lock_path(project)
    try:
        lock_path.mkdir(parents=True)
    except FileExistsError:
        owner = lock_path / "owner.json"
        if policy == "repair" and _owner_is_stale(owner):
            shutil.rmtree(lock_path)
            lock_path.mkdir()
        else:
            raise _err(
                "store_migration_locked",
                "store migration lock is held; use explicit stale-lock recovery "
                "only after verifying the owner",
            ) from None
    try:
        write_text_atomic(
            lock_path / "owner.json",
            json.dumps(
                {
                    "pid": os.getpid(),
                    "hostname": socket.gethostname(),
                    "created_at": utc_timestamp(),
                }
            )
            + "\n",
        )
        yield
    finally:
        shutil.rmtree(lock_path, ignore_errors=True)


def _dual_format_finding(project: Project) -> dict[str, str | None] | None:
    legacy = translation_store_path(project)
    v3_root = translation_store_v3_root(project)
    if legacy.is_file() and v3_root.is_dir():
        return _finding(
            "error",
            "dual_format",
            "both the legacy translation-store.json and v3 translation-store "
            "directory exist; resolve the active format before migration",
            str(legacy),
        )
    return None


def _source_reconstruction_findings(
    project: Project, store: TranslationStoreV2
) -> list[dict[str, str | None]]:
    try:
        source_ids = {record.record_id for record in load_source_records(project)}
    except Exception as exc:  # noqa: BLE001
        return [
            _finding(
                "error",
                "source_unavailable",
                f"source records could not be loaded: {exc}",
            )
        ]
    missing = sorted(set(store.records) - source_ids)
    return [
        _finding(
            "error",
            "missing_source_record",
            f"source record {record_id} is unavailable for "
            "deterministic reconstruction",
        )
        for record_id in missing
    ]


def _prepare_result(
    plan: StoreMigrationPlan,
    *,
    store: TranslationStoreV2 | None,
    findings: list[dict[str, str | None]],
) -> StoreMigrationResult:
    record_ids = sorted(store.records) if store is not None else []
    return StoreMigrationResult(
        plan=plan,
        records=len(record_ids),
        chunk_ids=sorted({record_id.split("-", 1)[0] for record_id in record_ids}),
        backup_path=plan.backup_path,
        report_path=plan.report_path,
        changed=False,
        findings=findings,
        source_drift_detected=any(f["code"] == "source_drift" for f in findings),
    )


def _preflight(
    project: Project, plan: StoreMigrationPlan
) -> tuple[StoreMigrationResult, TranslationStoreV2 | None]:
    findings: list[dict[str, str | None]] = []
    dual = _dual_format_finding(project)
    if dual is not None:
        findings.append(dual)
    report = inspect_store(project)
    findings.extend(report.findings_payload())
    store: TranslationStoreV2 | None = None
    pending = any(f["code"] == "pending_transaction" for f in findings)
    if not pending and plan.source_format in {
        StoreFormat.V1,
        StoreFormat.V2,
        StoreFormat.V3,
    }:
        try:
            store = open_translation_store(
                project, default_format=StoreFormat.V2
            ).materialize_v2()
        except Exception as exc:  # noqa: BLE001
            findings.append(_finding("error", "invalid_source_store", str(exc)))
        if store is not None:
            findings.extend(_source_reconstruction_findings(project, store))
    if plan.source_format == plan.target_format and plan.source_format in {
        StoreFormat.V2,
        StoreFormat.V3,
    }:
        findings.append(
            _finding(
                "warn",
                "already_target_format",
                "the requested target format is already active",
            )
        )
    if plan.target_format == StoreFormat.V3 and plan.source_format not in {
        StoreFormat.V1,
        StoreFormat.V2,
    }:
        findings.append(
            _finding(
                "error",
                "unsupported_source_format",
                f"cannot migrate {plan.source_format.value} to v3",
            )
        )
    if plan.target_format == StoreFormat.V2 and plan.source_format != StoreFormat.V3:
        findings.append(
            _finding(
                "error",
                "unsupported_source_format",
                f"cannot roll back {plan.source_format.value} to v2",
            )
        )
    return _prepare_result(plan, store=store, findings=findings), store


def _temporary_project(project: Project, directory: Path) -> Project:
    return replace(project, store_path=directory / "translation-store.json")


def _set_migration_metadata(
    target_project: Project, source_project: Project, plan: StoreMigrationPlan
) -> None:
    manifest_path = translation_store_v3_root(target_project) / "manifest.json"
    manifest = V3Manifest.model_validate_json(manifest_path.read_text("utf-8"))
    source_format = plan.source_format.value
    manifest.migrated_from = V3ManifestMigration(
        format=cast(Literal[1, 2, 3], {"v1": 1, "v2": 2, "v3": 3}[source_format]),
        migration_id=plan.migration_id,
        source_store_sha256=_source_store_hash(source_project, plan.source_format)
        or "",
    )
    write_text_atomic(manifest_path, manifest.model_dump_json(indent=2) + "\n")


def _build_temporary_v3(
    project: Project, plan: StoreMigrationPlan, store: TranslationStoreV2
) -> tuple[Path, StoreMigrationResult]:
    temporary_base = (
        plan.store_root.parent / f".{plan.store_root.name}.tmp-{plan.migration_id}"
    )
    if temporary_base.exists():
        shutil.rmtree(temporary_base)
    temp_project = _temporary_project(project, temporary_base)
    V3TranslationStoreRepository(temp_project).write_materialized_v2(store)
    _set_migration_metadata(temp_project, project, plan)
    readback = V3TranslationStoreRepository(temp_project).materialize_v2()
    temp_doctor = inspect_store(temp_project)
    findings = temp_doctor.findings_payload()
    parity = _canonical_store(store) == _canonical_store(readback)
    if not parity:
        findings.append(
            _finding(
                "error",
                "parity_mismatch",
                "temporary v3 read-back differs from the source store",
            )
        )
    result = replace(
        _prepare_result(plan, store=store, findings=findings),
        parity_verified=parity,
    )
    return temporary_base, result


def _activate_v3(
    project: Project, plan: StoreMigrationPlan, temporary_base: Path
) -> None:
    temporary_root = temporary_base / "translation-store"
    if plan.store_root.exists():
        raise _err(
            "store_migration_blocked",
            f"target v3 directory already exists: {plan.store_root}",
        )
    temporary_root.replace(plan.store_root)
    if plan.legacy_store_path.is_file() and not plan.keep_legacy_copy:
        plan.legacy_store_path.unlink()


def _build_temporary_v2(
    project: Project, plan: StoreMigrationPlan, store: TranslationStoreV2
) -> tuple[Path, StoreMigrationResult]:
    temporary_base = (
        plan.store_root.parent / f".{plan.store_root.name}.tmp-{plan.migration_id}"
    )
    if temporary_base.exists():
        shutil.rmtree(temporary_base)
    temporary_base.mkdir(parents=True)
    temp_project = _temporary_project(project, temporary_base)
    V1V2TranslationStoreRepository(
        temp_project, format=StoreFormat.V2
    ).write_materialized_v2(store)
    readback = V1V2TranslationStoreRepository(
        temp_project, format=StoreFormat.V2
    ).materialize_v2()
    parity = _canonical_store(store) == _canonical_store(readback)
    findings: list[dict[str, str | None]] = []
    if not parity:
        findings.append(
            _finding(
                "error",
                "parity_mismatch",
                "temporary v2 read-back differs from the v3 store",
            )
        )
    result = replace(
        _prepare_result(plan, store=store, findings=findings), parity_verified=parity
    )
    return temporary_base, result


def _activate_v2(
    project: Project, plan: StoreMigrationPlan, temporary_base: Path
) -> None:
    temporary_file = temporary_base / "translation-store.json"
    temporary_file.replace(plan.legacy_store_path)
    if plan.store_root.exists():
        if plan.backup_path is not None and not plan.backup_path.exists():
            plan.store_root.replace(plan.backup_path)
        else:
            shutil.rmtree(plan.store_root)


def execute_store_migration(
    project: Project,
    *,
    target_format: StoreFormat,
    dry_run: bool = True,
    allow_source_drift: bool = False,
    backup_dir: Path | None = None,
    keep_legacy_copy: bool = False,
    stale_lock_policy: LockPolicy = "reject",
) -> StoreMigrationResult:
    """Execute a validated v1/v2 to v3 migration or v3 to v2 rollback."""

    plan = plan_store_migration(
        project,
        target_format=target_format,
        dry_run=dry_run,
        allow_source_drift=allow_source_drift,
        backup_dir=backup_dir,
        keep_legacy_copy=keep_legacy_copy,
        stale_lock_policy=stale_lock_policy,
    )
    result, store = _preflight(project, plan)
    if dry_run:
        return _write_report(plan, result)
    blocked = [
        finding
        for finding in _blocking_findings(result, allow_source_drift=allow_source_drift)
        if not (
            target_format == StoreFormat.V2 and finding["code"] == "pending_transaction"
        )
    ]
    if blocked:
        blocked_codes = ", ".join(str(finding["code"]) for finding in blocked)
        failed = replace(
            result,
            findings=result.findings
            + [
                _finding(
                    "error",
                    "preflight_blocked",
                    f"migration blocked by preflight findings: {blocked_codes}",
                )
            ],
        )
        _write_report(plan, failed)
        raise _err(
            "store_migration_blocked",
            f"store migration blocked by preflight findings: {blocked_codes}",
        )
    if store is None and target_format != StoreFormat.V2:
        raise _err(
            "store_migration_blocked",
            "source store could not be materialized",
        )

    with _migration_lock(project, stale_lock_policy):
        # Recheck the source after taking the lock so a concurrent writer cannot
        # invalidate the preflight while the temporary store is being built.
        latest = open_translation_store(
            project, default_format=StoreFormat.V2
        ).materialize_v2()
        if store is None:
            store = latest
        elif _canonical_store(latest) != _canonical_store(store):
            raise _err(
                "store_concurrent_update",
                "canonical store changed during migration preflight",
            )
        if target_format == StoreFormat.V3:
            temporary_base, built = _build_temporary_v3(project, plan, store)
            if _blocking_findings(built, allow_source_drift=allow_source_drift):
                shutil.rmtree(temporary_base, ignore_errors=True)
                _write_report(plan, built)
                raise _err(
                    "store_migration_blocked",
                    "temporary v3 store failed doctor or parity checks",
                )
            assert plan.backup_path is not None
            backup_hash = (
                _copy_verified(plan.legacy_store_path, plan.backup_path)
                if plan.legacy_store_path.is_file()
                else None
            )
            _activate_v3(project, plan, temporary_base)
            completed = replace(built, changed=True, backup_sha256=backup_hash)
            shutil.rmtree(temporary_base, ignore_errors=True)
        elif target_format == StoreFormat.V2:
            recover_v3_transactions(transactions_dir(project), plan.store_root)
            source_store = V3TranslationStoreRepository(project).materialize_v2()
            temporary_base, built = _build_temporary_v2(project, plan, source_store)
            built = replace(
                built,
                findings=built.findings
                + _source_reconstruction_findings(project, source_store),
            )
            if _blocking_findings(built, allow_source_drift=allow_source_drift):
                shutil.rmtree(temporary_base, ignore_errors=True)
                _write_report(plan, built)
                raise _err(
                    "store_migration_blocked", "temporary v2 store failed parity checks"
                )
            assert plan.backup_path is not None
            backup_hash = (
                _copy_verified(plan.store_root, plan.backup_path)
                if plan.store_root.is_dir()
                else None
            )
            _activate_v2(project, plan, temporary_base)
            completed = replace(built, changed=True, backup_sha256=backup_hash)
            shutil.rmtree(temporary_base, ignore_errors=True)
        else:
            raise ValueError(f"unsupported migration target: {target_format.value}")

        post = inspect_store(project)
        post_errors = [
            finding
            for finding in post.findings_payload()
            if finding["severity"] == "error"
        ]
        if post_errors:
            completed = replace(completed, findings=completed.findings + post_errors)
        return _write_report(plan, completed)
