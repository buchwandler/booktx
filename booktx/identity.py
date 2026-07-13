"""Identity payload assembly for the ``whoami`` / identity commands.

Extracted from the CLI so the pure payload logic (no Rich console) is testable
and reusable. The human rendering stays in the CLI layer where the console
lives.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from booktx.config import (
    Project,
    load_translation_version_ledger,
    project_source_sha256,
)
from booktx.context import context_path, load_context
from booktx.path_display import display_path
from booktx.runtime import RuntimeMode
from booktx.store import StoreFormat, detect_store_format, open_translation_store
from booktx.versioning import canonical_json_sha256, resolve_identity

__all__ = [
    "identity_payload",
    "context_identity_payload",
    "store_identity_payload",
]


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def context_identity_payload(
    proj: Project,
    *,
    mode: RuntimeMode | None = None,
) -> dict[str, Any]:
    path = context_path(proj)
    rel_path = (
        display_path(path, mode) if mode is not None else _relative(path, proj.root)
    )
    if not path.is_file():
        return {
            "path": rel_path,
            "exists": False,
            "ready": None,
            "sha256": None,
            "status": "missing",
        }
    try:
        context = load_context(proj)
    except Exception:
        return {
            "path": rel_path,
            "exists": True,
            "ready": None,
            "sha256": None,
            "status": "invalid",
        }
    if context is None:  # pragma: no cover - guarded by is_file() above
        return {
            "path": rel_path,
            "exists": False,
            "ready": None,
            "sha256": None,
            "status": "missing",
        }
    return {
        "path": rel_path,
        "exists": True,
        "ready": context.ready,
        "sha256": canonical_json_sha256(context.model_dump(mode="json", by_alias=True)),
        "status": "ready" if context.ready else "not_ready",
    }


def store_identity_payload(proj: Project) -> dict[str, Any]:
    try:
        store_format = detect_store_format(proj)
    except Exception:
        store_format = StoreFormat.MISSING
    if store_format == StoreFormat.MISSING:
        return {
            "exists": False,
            "version": None,
            "format": None,
            "record_count": None,
            "status": "missing",
        }
    try:
        repo = open_translation_store(proj, default_format=StoreFormat.V2)
        store = repo.materialize_v2()
    except Exception:
        return {
            "exists": True,
            "version": 3 if store_format == StoreFormat.V3 else None,
            "format": store_format.value,
            "record_count": None,
            "status": "invalid",
        }
    return {
        "exists": True,
        "version": 3 if store_format == StoreFormat.V3 else store.version,
        "format": store_format.value,
        "record_count": len(store.records),
        "status": "ok",
    }


def identity_payload(
    proj: Project, *, mode: RuntimeMode | None = None
) -> dict[str, Any]:
    identity = resolve_identity(proj)
    active_version = None
    try:
        active_version = load_translation_version_ledger(proj).active_version
    except Exception:  # noqa: BLE001
        active_version = None

    try:
        source_sha256 = project_source_sha256(proj)
    except Exception:  # noqa: BLE001
        source_sha256 = None

    return {
        "project_dir": (
            display_path(proj.profile_dir or proj.root, mode)
            if mode is not None and mode.isolated_output
            else str(proj.root)
        ),
        "actor": identity.actor,
        "harness": identity.harness,
        "model": identity.model,
        "active_version": active_version,
        "context": context_identity_payload(proj, mode=mode),
        "source_sha256": source_sha256,
        "store": store_identity_payload(proj),
    }
