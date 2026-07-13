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

__all__ = ["detect_store_format", "open_translation_store"]


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
    project: Project, *, default_format: StoreFormat = StoreFormat.V2
) -> TranslationStoreRepository:
    """Open the detected canonical store repository."""

    detected = detect_store_format(project)
    if detected == StoreFormat.V3:
        return V3TranslationStoreRepository(project)
    if detected == StoreFormat.MISSING and default_format == StoreFormat.V3:
        return V3TranslationStoreRepository(project)
    if detected == StoreFormat.MISSING:
        return V1V2TranslationStoreRepository(project, format=default_format)
    return V1V2TranslationStoreRepository(project, format=detected)
