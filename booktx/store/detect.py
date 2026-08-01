"""Store format detection and repository opening."""

from __future__ import annotations

import json

from booktx.config import (
    Project,
    _err,
    translation_store_path,
    translation_store_v3_root,
)

from .models import StoreFormat, TranslationStoreRepository, V3Manifest
from .v1_v2 import V1V2TranslationStoreRepository
from .v3 import V3TranslationStoreRepository

DEFAULT_NEW_PROFILE_STORE_FORMAT = StoreFormat.V3

__all__ = [
    "DEFAULT_NEW_PROFILE_STORE_FORMAT",
    "detect_store_format",
    "open_translation_store",
    "create_translation_store",
]


def detect_store_format(project: Project) -> StoreFormat:
    """Detect the canonical store backend for one project/profile."""

    legacy_path = translation_store_path(project)
    v3_root = translation_store_v3_root(project)
    if v3_root.exists():
        if not v3_root.is_dir():
            raise _err(
                "invalid_translation_store",
                f"expected a directory at {v3_root.as_posix()}",
            )
        manifest_path = v3_root / "manifest.json"
        if not manifest_path.is_file():
            raise _err(
                "invalid_translation_store",
                f"v3 store manifest is missing at {manifest_path.as_posix()}",
            )
        try:
            V3Manifest.model_validate_json(manifest_path.read_text("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise _err(
                "invalid_translation_store",
                f"v3 store manifest is invalid at {manifest_path.as_posix()}: {exc}",
            ) from exc
        return StoreFormat.V3
    if not legacy_path.is_file():
        return StoreFormat.MISSING
    try:
        raw = json.loads(legacy_path.read_text("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise _err(
            "invalid_translation_store",
            f"translation store is invalid at {legacy_path.as_posix()}: {exc}",
        ) from exc
    if isinstance(raw, dict) and raw.get("version") == 2:
        return StoreFormat.V2
    return StoreFormat.V1


def open_translation_store(
    project: Project, *, default_format: StoreFormat | None = None
) -> TranslationStoreRepository:
    """Open an existing canonical store repository."""

    detected = detect_store_format(project)
    if detected == StoreFormat.V3:
        return V3TranslationStoreRepository(project)
    if detected == StoreFormat.MISSING:
        if default_format is not None:
            # Explicit backend selection remains available for migrations and
            # test fixture construction; normal workflows must not use it.
            return (
                V3TranslationStoreRepository(project)
                if default_format == StoreFormat.V3
                else V1V2TranslationStoreRepository(project, format=default_format)
            )
        raise _err(
            "translation_store_missing",
            "canonical translation store is missing; create it explicitly",
        )
    return V1V2TranslationStoreRepository(project, format=detected)


def create_translation_store(
    project: Project,
    *,
    format: StoreFormat = DEFAULT_NEW_PROFILE_STORE_FORMAT,
    source_sha256: str = "",
) -> TranslationStoreRepository:
    """Create one canonical store with an explicit backend policy."""

    detected = detect_store_format(project)
    if detected != StoreFormat.MISSING:
        raise _err(
            "translation_store_exists",
            f"cannot create a new store while {detected.value} is canonical",
        )
    if format == StoreFormat.V3:
        repository: TranslationStoreRepository = V3TranslationStoreRepository(project)
    elif format in {StoreFormat.V1, StoreFormat.V2}:
        repository = V1V2TranslationStoreRepository(project, format=format)
    else:
        raise _err(
            "invalid_translation_store_format",
            f"unsupported store format: {format}",
        )
    repository.clear_all(source_sha256=source_sha256)
    return repository
