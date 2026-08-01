"""Operational status payload for the canonical translation store."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from booktx.config import (
    Project,
    current_source_sha256,
    translation_store_path,
    translation_store_v3_root,
)

from .detect import detect_store_format
from .doctor import inspect_store
from .models import StoreFormat, V3Manifest

__all__ = ["build_store_status"]


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_store_status(project: Project) -> dict[str, Any]:
    """Return stable health and recovery information for one profile store."""

    legacy_path = translation_store_path(project)
    v3_root = translation_store_v3_root(project)
    try:
        store_format = detect_store_format(project)
    except Exception as exc:  # noqa: BLE001 - status must remain inspectable
        store_format = StoreFormat.MISSING
        detection_error: str | None = str(exc)
    else:
        detection_error = None

    report = inspect_store(project)
    canonical_path: Path | None = None
    schema_version: int | None = None
    source_sha256: str | None = report.stored_source_sha256
    if store_format == StoreFormat.V3:
        canonical_path = v3_root
        manifest_path = v3_root / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = V3Manifest.model_validate_json(
                    manifest_path.read_text("utf-8")
                )
                schema_version = manifest.version
                source_sha256 = manifest.source_sha256
            except Exception:
                schema_version = None
    elif store_format in {StoreFormat.V1, StoreFormat.V2}:
        canonical_path = legacy_path
        try:
            raw = json.loads(legacy_path.read_text("utf-8"))
            schema_version = raw.get("version") if isinstance(raw, dict) else None
            if isinstance(raw, dict):
                source_sha256 = raw.get("source_sha256") or source_sha256
        except Exception:
            schema_version = None

    live_source_sha256: str | None
    try:
        live_source_sha256 = current_source_sha256(project)
    except Exception:
        live_source_sha256 = report.live_source_sha256
    if source_sha256 and live_source_sha256:
        source_hash_status = "match" if source_sha256 == live_source_sha256 else "drift"
    elif source_sha256:
        source_hash_status = "unknown"
    else:
        source_hash_status = "missing"

    pending = any(finding.code == "pending_transaction" for finding in report.findings)
    legacy_copy = {
        "present": legacy_path.is_file(),
        "canonical": store_format == StoreFormat.V2,
        "sha256": _sha256(legacy_path),
    }
    findings = report.findings_payload()
    if detection_error:
        findings.insert(
            0,
            {
                "severity": "error",
                "code": "detection_error",
                "message": detection_error,
                "path": None,
            },
        )
    suggested = "booktx validate . --profile " + (project.profile or "PROFILE")
    return {
        "canonical_format": (
            None if store_format == StoreFormat.MISSING else store_format.value
        ),
        "canonical_path": (
            str(canonical_path) if canonical_path is not None else None
        ),
        "schema_version": schema_version,
        "record_count": report.record_count,
        "chunk_count": len(report.chunk_ids),
        "chunk_ids": list(report.chunk_ids),
        "source_sha256": source_sha256,
        "live_source_sha256": live_source_sha256,
        "source_hash_status": source_hash_status,
        "pending_transaction": pending,
        "findings": findings,
        "legacy_copy": legacy_copy,
        "suggested_next_command": suggested,
    }
