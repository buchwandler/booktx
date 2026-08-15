"""Short-lived receipts for successful staged translation validation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, cast

from booktx.config import Project, translation_validation_receipt_path
from booktx.io_utils import write_json_text_atomic
from booktx.translation_quality import resolve_submission_quality_policy
from booktx.versioning import canonical_json_sha256

if TYPE_CHECKING:
    from booktx.models import TranslationTask

__all__ = [
    "validation_receipt_key",
    "write_validation_receipt",
    "load_matching_validation_receipt",
]


def _input_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _receipt_identity(
    project: Project,
    task: TranslationTask,
    input_sha256: str,
    requested_quality: str | None = None,
) -> dict[str, str | None]:
    policy = resolve_submission_quality_policy(
        getattr(project.profile_config, "submission_quality", None),
        requested_quality=requested_quality,
    )
    return {
        "task_id": task.task_id,
        "input_sha256": input_sha256,
        "source_sha256": task.source_sha256,
        "context_view_sha256": task.context_view_sha256,
        "mandatory_glossary_sha256": task.mandatory_glossary_sha256,
        "translation_version": task.translation_version,
        "quality_policy_fingerprint": policy.fingerprint,
        "quality_mode": policy.mode,
        "grammar_backend_identity": policy.backend_identity,
    }


def validation_receipt_key(
    task: TranslationTask,
    input_path: Path,
    project: Project | None = None,
    requested_quality: str | None = None,
) -> str:
    """Return a receipt key bound to content, task, and quality policy."""
    if project is None:
        # Compatibility for callers that only have the old task/key contract.
        return canonical_json_sha256(
            {
                "task_id": task.task_id,
                "input_sha256": _input_sha256(input_path),
                "source_sha256": task.source_sha256,
                "context_view_sha256": task.context_view_sha256,
                "mandatory_glossary_sha256": task.mandatory_glossary_sha256,
                "translation_version": task.translation_version,
            }
        )
    return canonical_json_sha256(
        _receipt_identity(project, task, _input_sha256(input_path), requested_quality)
    )


def write_validation_receipt(
    project: Project,
    task: TranslationTask,
    input_path: Path,
    *,
    passed: bool,
    requested_quality: str | None = None,
) -> Path:
    input_sha256 = _input_sha256(input_path)
    identity = _receipt_identity(project, task, input_sha256, requested_quality)
    key = canonical_json_sha256(identity)
    payload = {
        "version": 1,
        "receipt_key": key,
        "state": "pass" if passed else "fail",
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        **identity,
    }
    path = translation_validation_receipt_path(project, key)
    write_json_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return path


def load_matching_validation_receipt(
    project: Project,
    task: TranslationTask,
    input_path: Path,
    requested_quality: str | None = None,
) -> dict[str, object] | None:
    key = validation_receipt_key(
        task, input_path, project, requested_quality=requested_quality
    )
    path = translation_validation_receipt_path(project, key)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if payload.get("receipt_key") != key or payload.get("state") != "pass":
        return None
    return cast(dict[str, object], payload)
