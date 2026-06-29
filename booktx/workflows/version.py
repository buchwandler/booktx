"""Domain workflow functions for translation-version management (Phase 3 slice 4).

Wraps the versioning/config service layer so the command layer never imports
``booktx.config`` or ``booktx.translation_store`` directly. Not-found cases
raise :class:`booktx.errors.BooktxError`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from booktx.config import load_translation_version_ledger
from booktx.errors import BooktxError
from booktx.versioning import (
    fork_current_context,
    lookup_version,
    select_active_version,
    set_track_label,
)

if TYPE_CHECKING:
    from booktx.config import Project
    from booktx.models import TranslationVersionLedger
    from booktx.versioning import VersionResolution


def version_current_payload(proj: Project) -> dict[str, Any]:
    ledger = load_translation_version_ledger(proj)
    return {
        "active_version": ledger.active_version,
        "track_count": len(ledger.tracks),
    }


def load_version_ledger(proj: Project) -> TranslationVersionLedger:
    return load_translation_version_ledger(proj)


def select_version(proj: Project, version_ref: str) -> TranslationVersionLedger:
    return select_active_version(proj, version_ref)


def set_version_label(
    proj: Project, major_version: int, label: str
) -> TranslationVersionLedger:
    return set_track_label(proj, major_version, label)


def fork_context(proj: Project, note: str | None = None) -> VersionResolution:
    return fork_current_context(proj, note=note)


def version_show_payload(proj: Project, selector: str) -> dict[str, Any]:
    """Build the show payload for a track number or dotted version ref.

    Raises :class:`booktx.errors.BooktxError` when a track number is unknown.
    """
    ledger = load_translation_version_ledger(proj)
    if "." in selector:
        track, sub = lookup_version(ledger, selector)
        return {
            "version_ref": sub.version_ref,
            "version": track.version,
            "subversion": sub.subversion,
            "actor": track.actor,
            "harness": track.harness,
            "model": track.model,
            "label": track.label,
            "context_sha256": sub.context_sha256,
            "baseline_sha256": sub.baseline_sha256,
            "baseline_path": sub.baseline_path,
            "legacy_full_context_sha256": sub.legacy_full_context_sha256,
            "legacy_full_context_path": sub.legacy_full_context_path,
            "context_label": sub.context_label,
            "forced": sub.forced,
        }
    track_entry = ledger.tracks.get(str(int(selector)))
    if track_entry is None:
        raise BooktxError("unknown_version_track", f"track {selector} not found")
    return {
        "version": track_entry.version,
        "actor": track_entry.actor,
        "harness": track_entry.harness,
        "model": track_entry.model,
        "label": track_entry.label,
        "subversions": [
            sub.model_dump(mode="json") for sub in track_entry.subversions.values()
        ],
    }


__all__ = [
    "fork_context",
    "load_version_ledger",
    "select_version",
    "set_version_label",
    "version_current_payload",
    "version_show_payload",
]
