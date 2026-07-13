"""Migration helpers between v1, v2, and v3 canonical stores."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from booktx.config import (
    Project,
    translation_store_path,
    translation_store_v3_root,
)
from booktx.io_utils import utc_timestamp, write_text_atomic

from .detect import detect_store_format, open_translation_store
from .models import StoreFormat, StoreMigrationPlan, StoreMigrationResult
from .v1_v2 import V1V2TranslationStoreRepository
from .v3 import V3TranslationStoreRepository

__all__ = ["execute_store_migration", "plan_store_migration"]


def _backup_base_dir(project: Project) -> Path:
    profile_dir = project.profile_dir or project.booktx_dir
    return profile_dir / "reports" / "store-migration"


def plan_store_migration(
    project: Project,
    *,
    target_format: StoreFormat,
    dry_run: bool = True,
) -> StoreMigrationPlan:
    """Plan a store migration or rollback."""

    timestamp = utc_timestamp().replace(":", "").replace("-", "")
    base_dir = _backup_base_dir(project)
    backup_path = None
    if target_format == StoreFormat.V3:
        backup_path = base_dir / f"{timestamp}-translation-store.v2.backup.json"
    elif target_format == StoreFormat.V2:
        backup_path = base_dir / f"{timestamp}-translation-store.v3.backup"
    report_path = base_dir / f"{timestamp}-migration-report.json"
    return StoreMigrationPlan(
        source_format=detect_store_format(project),
        target_format=target_format,
        store_root=translation_store_v3_root(project),
        legacy_store_path=translation_store_path(project),
        backup_path=backup_path,
        report_path=report_path,
        dry_run=dry_run,
    )


def _write_report(plan: StoreMigrationPlan, result: StoreMigrationResult) -> None:
    assert plan.report_path is not None
    payload = {
        "created_at": utc_timestamp(),
        "source_format": plan.source_format.value,
        "target_format": plan.target_format.value,
        "records": result.records,
        "chunk_ids": result.chunk_ids,
        "backup_path": str(result.backup_path)
        if result.backup_path is not None
        else None,
        "changed": result.changed,
    }
    write_text_atomic(plan.report_path, json.dumps(payload, indent=2) + "\n")


def execute_store_migration(
    project: Project,
    *,
    target_format: StoreFormat,
    dry_run: bool = True,
) -> StoreMigrationResult:
    """Execute a store migration or rollback."""

    plan = plan_store_migration(project, target_format=target_format, dry_run=dry_run)
    source_repo = open_translation_store(project, default_format=StoreFormat.V2)
    store = source_repo.materialize_v2()
    chunk_ids = sorted({record_id.split("-", 1)[0] for record_id in store.records})
    result = StoreMigrationResult(
        plan=plan,
        records=len(store.records),
        chunk_ids=chunk_ids,
        backup_path=plan.backup_path,
        report_path=plan.report_path,
        changed=not dry_run,
    )
    if dry_run:
        _write_report(plan, result)
        return result

    if plan.backup_path is not None:
        plan.backup_path.parent.mkdir(parents=True, exist_ok=True)
        if plan.source_format in {StoreFormat.V1, StoreFormat.V2}:
            if plan.legacy_store_path.is_file():
                shutil.copy2(plan.legacy_store_path, plan.backup_path)
        elif plan.source_format == StoreFormat.V3 and plan.store_root.is_dir():
            if plan.backup_path.exists():
                shutil.rmtree(plan.backup_path)
            shutil.copytree(plan.store_root, plan.backup_path)

    if target_format == StoreFormat.V3:
        target_repo = V3TranslationStoreRepository(project)
        target_repo.write_materialized_v2(store)
        if plan.legacy_store_path.is_file():
            plan.legacy_store_path.unlink()
    elif target_format == StoreFormat.V2:
        target_repo = V1V2TranslationStoreRepository(project, format=StoreFormat.V2)
        target_repo.write_materialized_v2(store)
        if plan.store_root.is_dir():
            shutil.rmtree(plan.store_root)
    else:
        raise ValueError(f"unsupported migration target: {target_format.value}")

    _write_report(plan, result)
    return result
