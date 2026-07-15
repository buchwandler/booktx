"""Backend-neutral store integrity helpers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from booktx.config import (
    Project,
    current_source_sha256,
    translation_store_path,
    translation_store_v3_root,
)

from .detect import open_translation_store
from .models import (
    StoreFormat,
    V3CurrentShard,
    V3Manifest,
    V3ReviewShard,
    V3TranslationShard,
    validate_v3_shard_consistency,
)
from .paths import (
    current_shard_path,
    review_candidates_shard_path,
    translation_candidates_shard_path,
)

__all__ = ["StoreDoctorFinding", "StoreDoctorReport", "inspect_store"]


@dataclass(slots=True)
class StoreDoctorFinding:
    """One store integrity finding."""

    severity: str
    code: str
    message: str
    path: str | None = None


@dataclass(slots=True)
class StoreDoctorReport:
    """Store health report suitable for migration preflight."""

    format: StoreFormat
    record_count: int
    chunk_ids: list[str] = field(default_factory=list)
    findings: list[StoreDoctorFinding] = field(default_factory=list)
    stored_source_sha256: str | None = None
    live_source_sha256: str | None = None

    def findings_payload(self) -> list[dict[str, str | None]]:
        return [asdict(finding) for finding in self.findings]


def _path(project: Project, path: Path) -> str:
    try:
        return path.relative_to(project.root).as_posix()
    except ValueError:
        return path.as_posix()


def _add_finding(
    findings: list[StoreDoctorFinding],
    *,
    severity: str,
    code: str,
    message: str,
    path: Path | None = None,
    project: Project | None = None,
) -> None:
    findings.append(
        StoreDoctorFinding(
            severity=severity,
            code=code,
            message=message,
            path=(
                _path(project, path)
                if path is not None and project is not None
                else None
            ),
        )
    )


def _safe_live_source_sha(project: Project) -> str | None:
    try:
        return current_source_sha256(project)
    except Exception:  # noqa: BLE001
        return None


def _inspect_v3_store(project: Project) -> StoreDoctorReport:
    findings: list[StoreDoctorFinding] = []
    store_root = translation_store_v3_root(project)
    manifest_file = store_root / "manifest.json"
    manifest: V3Manifest | None = None
    try:
        manifest = V3Manifest.model_validate_json(manifest_file.read_text("utf-8"))
    except Exception as exc:  # noqa: BLE001
        _add_finding(
            findings,
            severity="error",
            code="invalid_manifest",
            message=f"v3 manifest is invalid: {exc}",
            path=manifest_file,
            project=project,
        )
        return StoreDoctorReport(
            format=StoreFormat.V3,
            record_count=0,
            findings=findings,
            live_source_sha256=_safe_live_source_sha(project),
        )

    record_count = 0
    for chunk_id in manifest.chunk_ids:
        current_file = current_shard_path(project, chunk_id)
        translation_file = translation_candidates_shard_path(project, chunk_id)
        review_file = review_candidates_shard_path(project, chunk_id)
        if not current_file.is_file():
            _add_finding(
                findings,
                severity="error",
                code="missing_current_shard",
                message=f"current shard for chunk {chunk_id} is missing",
                path=current_file,
                project=project,
            )
            continue
        if not translation_file.is_file():
            _add_finding(
                findings,
                severity="error",
                code="missing_translation_shard",
                message=f"translation shard for chunk {chunk_id} is missing",
                path=translation_file,
                project=project,
            )
            continue
        if not review_file.is_file():
            _add_finding(
                findings,
                severity="error",
                code="missing_review_shard",
                message=f"review shard for chunk {chunk_id} is missing",
                path=review_file,
                project=project,
            )
            continue
        try:
            current = V3CurrentShard.model_validate_json(
                current_file.read_text("utf-8")
            )
            translations = V3TranslationShard.model_validate_json(
                translation_file.read_text("utf-8")
            )
            reviews = V3ReviewShard.model_validate_json(review_file.read_text("utf-8"))
            validate_v3_shard_consistency(
                current=current,
                translations=translations,
                reviews=reviews,
            )
        except Exception as exc:  # noqa: BLE001
            _add_finding(
                findings,
                severity="error",
                code="invalid_chunk_shards",
                message=f"chunk {chunk_id} failed shard validation: {exc}",
                path=current_file,
                project=project,
            )
            continue
        record_count += len(
            set(current.records) | set(translations.records) | set(reviews.records)
        )

    transactions_root = store_root / "transactions"
    if transactions_root.is_dir():
        for tx_dir in sorted(
            path for path in transactions_root.iterdir() if path.is_dir()
        ):
            journal_file = tx_dir / "journal.json"
            if journal_file.is_file():
                _add_finding(
                    findings,
                    severity="error",
                    code="pending_transaction",
                    message=f"pending transaction requires recovery: {tx_dir.name}",
                    path=journal_file,
                    project=project,
                )

    live_source_sha256 = _safe_live_source_sha(project)
    if (
        live_source_sha256
        and manifest.source_sha256
        and live_source_sha256 != manifest.source_sha256
    ):
        _add_finding(
            findings,
            severity="warn",
            code="source_drift",
            message=(
                "live source SHA does not match the canonical store source SHA; "
                "migration requires --allow-source-drift to proceed"
            ),
        )
    return StoreDoctorReport(
        format=StoreFormat.V3,
        record_count=record_count,
        chunk_ids=list(manifest.chunk_ids),
        findings=findings,
        stored_source_sha256=manifest.source_sha256,
        live_source_sha256=live_source_sha256,
    )


def _inspect_legacy_store(
    project: Project, store_format: StoreFormat
) -> StoreDoctorReport:
    findings: list[StoreDoctorFinding] = []
    legacy_path = translation_store_path(project)
    try:
        repo = open_translation_store(project, default_format=StoreFormat.V2)
        store = repo.materialize_v2()
    except Exception as exc:  # noqa: BLE001
        _add_finding(
            findings,
            severity="error",
            code="invalid_legacy_store",
            message=f"legacy store is invalid: {exc}",
            path=legacy_path,
            project=project,
        )
        return StoreDoctorReport(
            format=store_format,
            record_count=0,
            findings=findings,
            live_source_sha256=_safe_live_source_sha(project),
        )

    chunk_ids = sorted({record_id.split("-", 1)[0] for record_id in store.records})
    live_source_sha256 = _safe_live_source_sha(project)
    if (
        live_source_sha256
        and store.source_sha256
        and live_source_sha256 != store.source_sha256
    ):
        _add_finding(
            findings,
            severity="warn",
            code="source_drift",
            message=(
                "live source SHA does not match the canonical store source SHA; "
                "migration requires --allow-source-drift to proceed"
            ),
            path=legacy_path,
            project=project,
        )
    return StoreDoctorReport(
        format=store_format,
        record_count=len(store.records),
        chunk_ids=chunk_ids,
        findings=findings,
        stored_source_sha256=store.source_sha256,
        live_source_sha256=live_source_sha256,
    )


def inspect_store(project: Project) -> StoreDoctorReport:
    """Inspect the current store backend without changing it."""

    store_root = translation_store_v3_root(project)
    legacy_path = translation_store_path(project)
    if store_root.exists():
        return _inspect_v3_store(project)
    if legacy_path.is_file():
        store_format = StoreFormat.V2
        try:
            raw = json.loads(legacy_path.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            raw = None
        if not (isinstance(raw, dict) and raw.get("version") == 2):
            store_format = StoreFormat.V1
        return _inspect_legacy_store(project, store_format)
    return StoreDoctorReport(
        format=StoreFormat.MISSING,
        record_count=0,
        live_source_sha256=_safe_live_source_sha(project),
    )
