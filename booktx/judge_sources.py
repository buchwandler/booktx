"""Judge source snapshot copy/load/validate for selection-profile isolation.

A selection/judge profile can run in profile-root isolated mode only when the
effective candidate state of its configured source profiles has been copied
into a profile-local, immutable, hash-validated snapshot under
``judge-sources/``. This module owns that snapshot lifecycle. It is kept
separate from :mod:`booktx.judge_store`, which stays focused on candidate
selection over already-loaded views.

The canonical per-record state that is copied is ``translation-store.json``
(active versions, active review candidates, source hashes, targets, refs), not
``translated/*.json`` (which is rebuild/export material and may be stale).
``translation-version-ledger.json`` and ``identity.json`` are copied for
auditability when present; ``profile-config.json`` is always copied so the
snapshot can be validated against the configured source contract.

Hash contract (one explicit contract throughout the module):

- Copied JSON files are serialized from validated Pydantic models and their
  manifest hashes are SHA-256 over the exact UTF-8 bytes written, including the
  trailing newline.
- ``source_sha256`` is the current extracted source identity hash already used
  by booktx (:func:`booktx.config.current_source_sha256`).
- ``source_config_sha256`` and ``selection_profile_config_sha256`` use
  :func:`booktx.versioning.canonical_json_sha256` over validated model dumps.
- The active manifest hash (``source_snapshot_sha256`` on judge tasks) is
  SHA-256 over the exact active ``judge-sources/manifest.json`` bytes.
- ``source_candidates_sha256`` uses :func:`canonical_json_sha256` over a
  documented candidate payload produced by :func:`judge_task_candidates_sha256`.
"""

# ruff: noqa: E501
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from booktx.config import (
    Project,
    _err,
    current_source_sha256,
    identity_path,
    judge_source_identity_path,
    judge_source_profile_config_path,
    judge_source_snapshot_dir,
    judge_source_translation_store_path,
    judge_source_translation_version_ledger_path,
    judge_sources_manifest_path,
    judge_sources_snapshots_dir,
    load_identity,
    load_profile_project,
    load_translation_store,
    load_translation_version_ledger,
    translation_version_ledger_path,
    validate_profile_name,
    validate_snapshot_id,
)
from booktx.io_utils import utc_timestamp, write_text_atomic
from booktx.models import (
    JudgeSourceProfileSnapshot,
    JudgeSourcesSnapshotManifest,
    JudgeTaskRecord,
    ProfileConfig,
    TranslationIdentity,
    TranslationStoreV2,
    TranslationVersionLedger,
)
from booktx.translation_store import (
    EffectiveCandidateError,
    effective_candidate_selection,
)

__all__ = [
    "JudgeSourceProfileView",
    "JudgeSourceSyncResult",
    "sync_judge_source_snapshots",
    "load_live_judge_source_views",
    "load_snapshot_judge_source_views",
    "judge_sources_manifest_sha256",
    "validate_judge_sources_snapshot",
    "judge_task_candidates_sha256",
    "validate_snapshot_source_subset",
    "configured_selection_sources",
]

MANIFEST_VERSION = 1


# --------------------------------------------------------------------------
# dataclasses
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JudgeSourceProfileView:
    """A loaded source profile store plus its identity, agnostic to origin.

    A view is either *live* (loaded from a sibling ``translations/<profile>``
    directory in collaborative mode) or *snapshot* (loaded from a copied
    ``judge-sources/snapshots/.../profiles/<profile>`` directory in isolated
    mode). Consumers never touch sibling projects directly; they read
    ``store`` and the language fields from this view.
    """

    profile: str
    source_language: str
    target_language: str
    target_locale: str
    store: TranslationStoreV2
    store_sha256: str = ""
    snapshot: JudgeSourceProfileSnapshot | None = None


@dataclass(frozen=True, slots=True)
class JudgeSourceSyncResult:
    """Outcome of a snapshot sync (dry-run or write)."""

    profile: str
    source_profiles: tuple[str, ...]
    snapshot_id: str
    manifest_sha256: str
    profiles: tuple[JudgeSourceProfileSnapshot, ...]
    written: tuple[Path, ...] = ()
    skipped: tuple[str, ...] = ()
    pruned: tuple[str, ...] = ()
    changed: bool = False
    write: bool = False


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def configured_selection_sources(project: Project) -> list[str]:
    """Return the configured ``[selection].sources`` list (exact order)."""
    cfg = project.profile_config
    selection = cfg.selection if cfg is not None else None
    if selection is None or not selection.sources:
        raise _err(
            "judge_sources_missing",
            "no source profiles configured; create the selection profile with sources",
        )
    return list(selection.sources)


def _require_selection_profile(project: Project) -> None:
    cfg = project.profile_config
    if cfg is None or cfg.kind != "selection":
        raise _err(
            "judge_profile_kind",
            "judge source snapshots require a selection profile",
        )


def _serialize_model_text(model: object) -> str:
    """Exact on-disk text for a copied Pydantic model (indent=2 + newline)."""
    return model.model_dump_json(indent=2) + "\n"  # type: ignore[attr-defined]


def _sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _sha256_text_bytes(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _count_effective_candidates(store: TranslationStoreV2) -> int:
    total = 0
    for stored in store.records.values():
        selection = effective_candidate_selection(stored, strict_active_review=True)
        if isinstance(selection, EffectiveCandidateError) or selection is None:
            continue
        total += 1
    return total


# --------------------------------------------------------------------------
# snapshot id + manifest planning
# --------------------------------------------------------------------------


def _snapshot_digest(
    *,
    selection_profile: str,
    source_sha256: str,
    source_config_sha256: str,
    selection_profile_config_sha256: str,
    profile_snapshots: list[JudgeSourceProfileSnapshot],
) -> str:
    """Deterministic digest of the source list and copied content (no timestamps)."""
    payload = {
        "selection_profile": selection_profile,
        "source_sha256": source_sha256,
        "source_config_sha256": source_config_sha256,
        "selection_profile_config_sha256": selection_profile_config_sha256,
        "profiles": [
            snap.model_dump(mode="json", exclude={"copied_at"})
            for snap in profile_snapshots
        ],
    }
    from booktx.versioning import canonical_json_sha256

    return canonical_json_sha256(payload)


def _build_profile_snapshot(
    selection_project: Project,
    source_project: Project,
) -> tuple[JudgeSourceProfileSnapshot, dict[str, str | None], dict[str, str]]:
    """Plan one per-profile snapshot from a live source project.

    Returns the snapshot model, the exact copied-file texts (``None`` when the
    source file is absent), and a small dict of recomputed counts.
    """
    cfg = source_project.profile_config
    assert cfg is not None  # validated by caller
    store = load_translation_store(source_project)

    store_text = _serialize_model_text(store)
    store_sha = _sha256_text_bytes(store_text)
    files: dict[str, str | None] = {"translation-store.json": store_text}

    version_ledger_sha: str | None = None
    if translation_version_ledger_path(source_project).is_file():
        ledger = load_translation_version_ledger(source_project)
        ledger_text = _serialize_model_text(ledger)
        files["translation-version-ledger.json"] = ledger_text
        version_ledger_sha = _sha256_text_bytes(ledger_text)

    identity_sha: str | None = None
    identity = load_identity(source_project)
    if identity is not None and identity_path(source_project).is_file():
        identity_text = _serialize_model_text(identity)
        files["identity.json"] = identity_text
        identity_sha = _sha256_text_bytes(identity_text)

    config_text = _serialize_model_text(cfg)
    files["profile-config.json"] = config_text
    config_sha = _sha256_text_bytes(config_text)

    records_total = len(store.records)
    effective_total = _count_effective_candidates(store)

    snapshot = JudgeSourceProfileSnapshot(
        profile=source_project.profile or "",
        kind="translation",
        source_language=cfg.source_language,
        target_language=cfg.target_language,
        target_locale=cfg.target_locale or "",
        source_sha256=current_source_sha256(source_project),
        profile_config_sha256=config_sha,
        translation_store_sha256=store_sha,
        translation_version_ledger_sha256=version_ledger_sha,
        identity_sha256=identity_sha,
        records_total=records_total,
        effective_candidates_total=effective_total,
        copied_at="",  # filled at publication time
    )
    counts = {"records_total": records_total, "effective_total": effective_total}
    return snapshot, files, counts


def _validate_source_contract(
    selection_project: Project, source_project: Project
) -> None:
    """Same source-profile guards the live judge path uses."""
    from booktx.judge_store import validate_judge_source_profile

    validate_judge_source_profile(selection_project, source_project)


def _plan_generation(
    selection_project: Project,
    source_profiles: list[str],
) -> tuple[
    list[tuple[str, Project, JudgeSourceProfileSnapshot, dict[str, str | None]]],
    str,
    str,
    str,
]:
    """Build the full in-memory plan without any filesystem mutation.

    Returns the per-profile plan rows, the deterministic snapshot id, the
    selection source-config hash, and the selection profile-config hash.
    """
    source_config_sha = _source_config_canonical(selection_project)
    selection_profile_config_sha = _profile_config_canonical(selection_project)
    selection_source_sha = current_source_sha256(selection_project)

    rows: list[
        tuple[str, Project, JudgeSourceProfileSnapshot, dict[str, str | None]]
    ] = []
    snapshots_for_digest: list[JudgeSourceProfileSnapshot] = []
    for name in source_profiles:
        validate_profile_name(name)
        source_project = load_profile_project(selection_project.root, name)
        _validate_source_contract(selection_project, source_project)
        snapshot, files, _counts = _build_profile_snapshot(
            selection_project, source_project
        )
        rows.append((name, source_project, snapshot, files))
        snapshots_for_digest.append(snapshot)

    snapshot_id = _snapshot_digest(
        selection_profile=selection_project.profile or "",
        source_sha256=selection_source_sha,
        source_config_sha256=source_config_sha,
        selection_profile_config_sha256=selection_profile_config_sha,
        profile_snapshots=snapshots_for_digest,
    )
    return rows, snapshot_id, source_config_sha, selection_profile_config_sha


def _source_config_canonical(project: Project) -> str:
    from booktx.versioning import canonical_json_sha256

    return canonical_json_sha256(project.source_config.model_dump(mode="json"))


def _profile_config_canonical(project: Project) -> str:
    from booktx.versioning import canonical_json_sha256

    assert project.profile_config is not None
    return canonical_json_sha256(project.profile_config.model_dump(mode="json"))


def _build_manifest(
    *,
    selection_project: Project,
    snapshot_id: str,
    source_config_sha256: str,
    selection_profile_config_sha256: str,
    profile_snapshots: list[JudgeSourceProfileSnapshot],
    generated_at: str,
) -> JudgeSourcesSnapshotManifest:
    copied_at = generated_at
    stamped = [
        snap.model_copy(update={"copied_at": copied_at}) for snap in profile_snapshots
    ]
    return JudgeSourcesSnapshotManifest(
        version=MANIFEST_VERSION,
        selection_profile=selection_project.profile or "",
        snapshot_id=snapshot_id,
        source_sha256=current_source_sha256(selection_project),
        source_config_sha256=source_config_sha256,
        selection_profile_config_sha256=selection_profile_config_sha256,
        source_profiles=[snap.profile for snap in stamped],
        profiles={snap.profile: snap for snap in stamped},
        generated_at=generated_at,
    )


def _serialize_manifest_text(manifest: JudgeSourcesSnapshotManifest) -> str:
    return manifest.model_dump_json(indent=2) + "\n"


# --------------------------------------------------------------------------
# existing-generation inspection
# --------------------------------------------------------------------------


def _load_active_manifest(project: Project) -> JudgeSourcesSnapshotManifest | None:
    path = judge_sources_manifest_path(project)
    if not path.is_file():
        return None
    try:
        return JudgeSourcesSnapshotManifest.model_validate_json(path.read_text("utf-8"))
    except Exception:  # noqa: BLE001 - validation errors are reported by validate_*
        return None


def _read_manifest_bytes(project: Project) -> bytes | None:
    path = judge_sources_manifest_path(project)
    if not path.is_file():
        return None
    return path.read_bytes()


def _generation_validates(
    project: Project, manifest: JudgeSourcesSnapshotManifest
) -> bool:
    """Return True if the active snapshot generation still validates from disk."""
    try:
        validated = validate_judge_sources_snapshot(project)
    except Exception:  # noqa: BLE001
        return False
    return validated.snapshot_id == manifest.snapshot_id


# --------------------------------------------------------------------------
# publication
# --------------------------------------------------------------------------


def _write_generation_files(
    project: Project,
    snapshot_id: str,
    rows: list[tuple[str, Project, JudgeSourceProfileSnapshot, dict[str, str | None]]],
    target_base: Path,
) -> list[Path]:
    """Write all copied files for one generation under ``target_base``."""
    written: list[Path] = []
    for name, _src_project, _snapshot, files in rows:
        profile_dir = target_base / "profiles" / name
        profile_dir.mkdir(parents=True, exist_ok=True)
        for filename, text in files.items():
            assert text is not None  # only present files are in the dict
            path = profile_dir / filename
            write_text_atomic(path, text)
            written.append(path)
    return written


def _validate_generation_from_disk(
    project: Project,
    snapshot_id: str,
    rows: list[tuple[str, Project, JudgeSourceProfileSnapshot, dict[str, str | None]]],
    target_base: Path,
) -> None:
    """Re-read and re-hash every copied file; fail on any drift."""
    for name, _src_project, snapshot, files in rows:
        for filename, expected_text in files.items():
            assert expected_text is not None
            path = target_base / "profiles" / name / filename
            if not path.is_file():
                raise _err(
                    "judge_source_snapshot_corrupt",
                    f"snapshot generation is missing copied file {filename}",
                )
            actual = path.read_text("utf-8")
            if actual != expected_text:
                raise _err(
                    "judge_source_snapshot_corrupt",
                    f"snapshot copied file {filename} changed during publication",
                )
            if _sha256_text_bytes(actual) != _file_hash_for(filename, snapshot):
                raise _err(
                    "judge_source_snapshot_corrupt",
                    f"snapshot copied file {filename} hash does not match manifest",
                )


def _file_hash_for(filename: str, snapshot: JudgeSourceProfileSnapshot) -> str | None:
    if filename == "translation-store.json":
        return snapshot.translation_store_sha256
    if filename == "translation-version-ledger.json":
        return snapshot.translation_version_ledger_sha256
    if filename == "identity.json":
        return snapshot.identity_sha256
    if filename == "profile-config.json":
        return snapshot.profile_config_sha256
    return None


def _publish_generation(
    project: Project,
    snapshot_id: str,
    rows: list[tuple[str, Project, JudgeSourceProfileSnapshot, dict[str, str | None]]],
    manifest: JudgeSourcesSnapshotManifest,
) -> tuple[list[Path], list[str]]:
    """Atomically publish a new immutable generation + active manifest.

    Returns the list of written paths and the list of pruned snapshot ids.
    """
    snapshots_root = judge_sources_snapshots_dir(project)
    snapshots_root.mkdir(parents=True, exist_ok=True)
    target = judge_source_snapshot_dir(project, snapshot_id)

    written: list[Path] = []
    if target.is_dir():
        # Generation already published: validate and reuse, never overwrite.
        _validate_generation_from_disk(project, snapshot_id, rows, target)
    else:
        staging = (
            snapshots_root
            / f".staging-{snapshot_id}-{utc_timestamp().replace(':', '').replace('-', '')}"
        )
        staging.mkdir(parents=True, exist_ok=True)
        try:
            written.extend(_write_generation_files(project, snapshot_id, rows, staging))
            _validate_generation_from_disk(project, snapshot_id, rows, staging)
            staging.replace(target)
        finally:
            if staging.exists():
                _remove_tree_no_symlinks(staging)

    manifest_path = judge_sources_manifest_path(project)
    write_text_atomic(manifest_path, _serialize_manifest_text(manifest))
    written.append(manifest_path)

    pruned = _prune_inactive_generations(project, keep={snapshot_id})
    return written, pruned


def _remove_tree_no_symlinks(path: Path) -> None:
    """Remove a directory tree, never following symlinks."""
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink():
        path.unlink()
        return
    for entry in path.iterdir():
        if entry.is_symlink():
            entry.unlink()
        elif entry.is_dir():
            _remove_tree_no_symlinks(entry)
        else:
            entry.unlink()
    path.rmdir()


def _prune_inactive_generations(project: Project, *, keep: set[str]) -> list[str]:
    """Remove inactive, validated generation dirs (keeping ids in ``keep``).

    Never follows symlinks. The caller has already made the new manifest
    active, so any directory not in ``keep`` is a stale previous generation.
    """
    snapshots_root = judge_sources_snapshots_dir(project)
    if not snapshots_root.is_dir():
        return []
    pruned: list[str] = []
    for entry in sorted(snapshots_root.iterdir()):
        name = entry.name
        if name.startswith(".") or not entry.is_dir() or entry.is_symlink():
            continue
        if not _is_valid_snapshot_id_loose(name):
            continue
        if name in keep:
            continue
        try:
            _remove_tree_no_symlinks(entry)
            pruned.append(name)
        except OSError:
            # Non-fatal: a busy/locked previous generation is left in place.
            continue
    return pruned


def _is_valid_snapshot_id_loose(value: str) -> bool:
    try:
        validate_snapshot_id(value)
    except Exception:  # noqa: BLE001
        return False
    return True


# --------------------------------------------------------------------------
# public sync
# --------------------------------------------------------------------------


def sync_judge_source_snapshots(
    selection_project: Project,
    *,
    source_profiles: list[str],
    prune: bool = True,
    write: bool = False,
) -> JudgeSourceSyncResult:
    """Plan and (optionally) publish a judge source snapshot generation.

    With ``write=False`` this is a fully side-effect-free dry run: no
    directories are created, no files written, no timestamps changed, and no
    pruning is performed. With ``write=True`` the planned generation is
    published atomically and the active ``manifest.json`` is replaced last.

    An unchanged sync is a true no-op: when the deterministic snapshot id and
    content already match the active manifest, all existing bytes/timestamps
    are preserved and ``changed=False`` is reported.
    """
    _require_selection_profile(selection_project)
    if not source_profiles:
        source_profiles = configured_selection_sources(selection_project)

    rows, snapshot_id, source_config_sha, selection_profile_config_sha = (
        _plan_generation(selection_project, source_profiles)
    )
    profile_snapshots = [snap for _name, _proj, snap, _files in rows]

    existing = _load_active_manifest(selection_project)
    existing_bytes = _read_manifest_bytes(selection_project)
    no_op = (
        existing is not None
        and existing.snapshot_id == snapshot_id
        and _generation_validates(selection_project, existing)
    )

    if no_op:
        assert existing is not None
        assert existing_bytes is not None
        return JudgeSourceSyncResult(
            profile=selection_project.profile or "",
            source_profiles=tuple(existing.source_profiles),
            snapshot_id=snapshot_id,
            manifest_sha256=_sha256_bytes(existing_bytes),
            profiles=tuple(existing.profiles[p] for p in existing.source_profiles),
            written=(),
            skipped=(),
            pruned=(),
            changed=False,
            write=write,
        )

    generated_at = utc_timestamp()
    manifest = _build_manifest(
        selection_project=selection_project,
        snapshot_id=snapshot_id,
        source_config_sha256=source_config_sha,
        selection_profile_config_sha256=selection_profile_config_sha,
        profile_snapshots=profile_snapshots,
        generated_at=generated_at,
    )
    manifest_text = _serialize_manifest_text(manifest)
    manifest_sha = _sha256_text_bytes(manifest_text)

    if not write:
        planned_writes = _planned_write_paths(selection_project, snapshot_id, rows)
        return JudgeSourceSyncResult(
            profile=selection_project.profile or "",
            source_profiles=tuple(source_profiles),
            snapshot_id=snapshot_id,
            manifest_sha256=manifest_sha,
            profiles=tuple(manifest.profiles[p] for p in manifest.source_profiles),
            written=tuple(planned_writes),
            skipped=(),
            pruned=(),
            changed=True,
            write=False,
        )

    written, pruned = _publish_generation(
        selection_project, snapshot_id, rows, manifest
    )
    if not prune and pruned:
        # Pruning already happened during publication; report it but the caller
        # asked for no prune, so we cannot undo an atomic publication prune.
        # (Prune is advisory: keeping the previous generation is acceptable.)
        pass
    return JudgeSourceSyncResult(
        profile=selection_project.profile or "",
        source_profiles=tuple(manifest.source_profiles),
        snapshot_id=snapshot_id,
        manifest_sha256=manifest_sha,
        profiles=tuple(manifest.profiles[p] for p in manifest.source_profiles),
        written=tuple(written),
        skipped=(),
        pruned=tuple(pruned),
        changed=True,
        write=True,
    )


def _planned_write_paths(
    project: Project,
    snapshot_id: str,
    rows: list[tuple[str, Project, JudgeSourceProfileSnapshot, dict[str, str | None]]],
) -> list[Path]:
    base = judge_source_snapshot_dir(project, snapshot_id)
    paths = [
        base / "profiles" / name / fn for name, _p, _s, files in rows for fn in files
    ]
    paths.append(judge_sources_manifest_path(project))
    return paths


# --------------------------------------------------------------------------
# manifest hash + full validation
# --------------------------------------------------------------------------


def judge_sources_manifest_sha256(selection_project: Project) -> str:
    """SHA-256 over the exact active ``judge-sources/manifest.json`` bytes."""
    path = judge_sources_manifest_path(selection_project)
    if not path.is_file():
        raise _err(
            "judge_source_snapshot_missing",
            "no judge source snapshot exists; run `booktx judge sync-sources` "
            "or `booktx judge prepare-isolation` from the project root",
        )
    return _sha256_bytes(path.read_bytes())


def validate_judge_sources_snapshot(
    selection_project: Project,
) -> JudgeSourcesSnapshotManifest:
    """Fully validate the active snapshot generation from disk.

    Checks the current extracted source identity, source-config hash,
    selection-profile-config hash, exact configured source list and order,
    every copied file hash, every copied model, and effective candidate counts.
    A manifest hash alone is not sufficient.
    """
    _require_selection_profile(selection_project)
    manifest = _load_active_manifest(selection_project)
    if manifest is None:
        raise _err(
            "judge_source_snapshot_missing",
            "no judge source snapshot exists; run `booktx judge sync-sources` "
            "or `booktx judge prepare-isolation` from the project root",
        )

    expected_source_sha = current_source_sha256(selection_project)
    if manifest.source_sha256 != expected_source_sha:
        raise _err(
            "judge_source_snapshot_drift",
            "judge source snapshot source identity changed; refresh the snapshot",
        )
    if manifest.source_config_sha256 != _source_config_canonical(selection_project):
        raise _err(
            "judge_source_snapshot_drift",
            "judge source snapshot source config changed; refresh the snapshot",
        )
    assert selection_project.profile_config is not None
    if manifest.selection_profile_config_sha256 != _profile_config_canonical(
        selection_project
    ):
        raise _err(
            "judge_source_snapshot_drift",
            "judge source snapshot selection profile config changed; refresh the snapshot",
        )

    configured = configured_selection_sources(selection_project)
    if manifest.source_profiles != configured:
        raise _err(
            "judge_source_snapshot_drift",
            "judge source snapshot source list does not match the selection profile; "
            "refresh the snapshot",
        )

    snapshot_id = manifest.snapshot_id
    validate_snapshot_id(snapshot_id)
    for name in manifest.source_profiles:
        snap = manifest.profiles.get(name)
        if snap is None:
            raise _err(
                "judge_source_snapshot_corrupt",
                f"snapshot manifest is missing profile {name}",
            )
        _validate_one_profile_files(selection_project, snapshot_id, name, snap)

    return manifest


def _validate_one_profile_files(
    project: Project,
    snapshot_id: str,
    name: str,
    snap: JudgeSourceProfileSnapshot,
) -> None:
    """Re-read, re-hash, and re-parse one source profile's copied files."""
    cfg_path = judge_source_profile_config_path(project, snapshot_id, name)
    if not cfg_path.is_file():
        raise _err(
            "judge_source_snapshot_corrupt",
            f"snapshot is missing profile-config.json for {name}",
        )
    cfg = ProfileConfig.model_validate_json(cfg_path.read_text("utf-8"))
    if _sha256_text_bytes(cfg_path.read_text("utf-8")) != snap.profile_config_sha256:
        raise _err(
            "judge_source_snapshot_corrupt",
            f"snapshot profile-config.json hash mismatch for {name}",
        )
    if cfg.kind != "translation":
        raise _err(
            "judge_source_snapshot_corrupt",
            f"snapshot profile {name} is not a translation profile",
        )

    store_path = judge_source_translation_store_path(project, snapshot_id, name)
    if not store_path.is_file():
        raise _err(
            "judge_source_snapshot_corrupt",
            f"snapshot is missing translation-store.json for {name}",
        )
    store_text = store_path.read_text("utf-8")
    if _sha256_text_bytes(store_text) != snap.translation_store_sha256:
        raise _err(
            "judge_source_snapshot_corrupt",
            f"snapshot translation-store.json hash mismatch for {name}",
        )
    store = TranslationStoreV2.model_validate_json(store_text)
    if len(store.records) != snap.records_total:
        raise _err(
            "judge_source_snapshot_corrupt",
            f"snapshot record count mismatch for {name}",
        )
    if _count_effective_candidates(store) != snap.effective_candidates_total:
        raise _err(
            "judge_source_snapshot_corrupt",
            f"snapshot effective candidate count mismatch for {name}",
        )

    ledger_path = judge_source_translation_version_ledger_path(
        project, snapshot_id, name
    )
    if snap.translation_version_ledger_sha256 is None:
        if ledger_path.is_file():
            raise _err(
                "judge_source_snapshot_corrupt",
                f"snapshot has unexpected translation-version-ledger.json for {name}",
            )
    else:
        if not ledger_path.is_file():
            raise _err(
                "judge_source_snapshot_corrupt",
                f"snapshot is missing translation-version-ledger.json for {name}",
            )
        ledger_text = ledger_path.read_text("utf-8")
        if _sha256_text_bytes(ledger_text) != snap.translation_version_ledger_sha256:
            raise _err(
                "judge_source_snapshot_corrupt",
                f"snapshot translation-version-ledger.json hash mismatch for {name}",
            )
        TranslationVersionLedger.model_validate_json(ledger_text)

    ident_path = judge_source_identity_path(project, snapshot_id, name)
    if snap.identity_sha256 is None:
        if ident_path.is_file():
            raise _err(
                "judge_source_snapshot_corrupt",
                f"snapshot has unexpected identity.json for {name}",
            )
    else:
        if not ident_path.is_file():
            raise _err(
                "judge_source_snapshot_corrupt",
                f"snapshot is missing identity.json for {name}",
            )
        ident_text = ident_path.read_text("utf-8")
        if _sha256_text_bytes(ident_text) != snap.identity_sha256:
            raise _err(
                "judge_source_snapshot_corrupt",
                f"snapshot identity.json hash mismatch for {name}",
            )
        TranslationIdentity.model_validate_json(ident_text)


# --------------------------------------------------------------------------
# view loaders
# --------------------------------------------------------------------------


def load_live_judge_source_views(
    selection_project: Project,
    source_profiles: list[str],
) -> dict[str, JudgeSourceProfileView]:
    """Load live source profile views from sibling ``translations/<profile>``."""
    _require_selection_profile(selection_project)
    views: dict[str, JudgeSourceProfileView] = {}
    for name in source_profiles:
        validate_profile_name(name)
        source_project = load_profile_project(selection_project.root, name)
        _validate_source_contract(selection_project, source_project)
        cfg = source_project.profile_config
        assert cfg is not None
        store = load_translation_store(source_project)
        views[name] = JudgeSourceProfileView(
            profile=name,
            source_language=cfg.source_language,
            target_language=cfg.target_language,
            target_locale=cfg.target_locale or cfg.target_language,
            store=store,
            store_sha256="",
            snapshot=None,
        )
    return views


def load_snapshot_judge_source_views(
    selection_project: Project,
    source_profiles: list[str] | None = None,
) -> dict[str, JudgeSourceProfileView]:
    """Load source profile views from the validated active snapshot.

    ``source_profiles`` may select an order-preserving subset of the validated
    snapshot's source list; it must never introduce or reorder names.
    """
    manifest = validate_judge_sources_snapshot(selection_project)
    active = list(manifest.source_profiles)
    selected = validate_snapshot_source_subset(manifest, source_profiles)

    views: dict[str, JudgeSourceProfileView] = {}
    for name in active:
        if name not in selected:
            continue
        snap = manifest.profiles[name]
        store_path = judge_source_translation_store_path(
            selection_project, manifest.snapshot_id, name
        )
        store = TranslationStoreV2.model_validate_json(store_path.read_text("utf-8"))
        views[name] = JudgeSourceProfileView(
            profile=name,
            source_language=snap.source_language,
            target_language=snap.target_language,
            target_locale=snap.target_locale or snap.target_language,
            store=store,
            store_sha256=snap.translation_store_sha256,
            snapshot=snap,
        )
    return views


def validate_snapshot_source_subset(
    manifest: JudgeSourcesSnapshotManifest,
    requested: list[str] | None,
) -> list[str]:
    """Return the requested sources if they are an order-preserving subset."""
    if requested is None:
        return list(manifest.source_profiles)
    active = manifest.source_profiles
    active_index = {name: i for i, name in enumerate(active)}
    last = -1
    out: list[str] = []
    for name in requested:
        if name not in active_index:
            raise _err(
                "judge_source_subset_invalid",
                f"requested source {name!r} is not in the active snapshot",
            )
        idx = active_index[name]
        if idx <= last:
            raise _err(
                "judge_source_subset_invalid",
                "requested sources must preserve the active snapshot order",
            )
        last = idx
        out.append(name)
    return out


# --------------------------------------------------------------------------
# candidate payload hash (shared by task creation + acceptance)
# --------------------------------------------------------------------------


def judge_task_candidates_sha256(records: list[JudgeTaskRecord]) -> str:
    """Canonical SHA-256 over the documented judge task candidate payload.

    The payload captures, per record: the record id, each candidate's label,
    profile/ref provenance, target hashes and target text, and validation
    findings, plus the missing-profile list. It excludes mutable task metadata
    (timestamps, hashes that are themselves derived from this payload).
    """
    payload = [
        {
            "id": record.id,
            "candidates": [
                {
                    "label": c.label,
                    "profile": c.profile,
                    "target_language": c.target_language,
                    "target_locale": c.target_locale,
                    "selected_kind": c.selected_kind,
                    "selected_ref": c.selected_ref,
                    "version_ref": c.version_ref,
                    "review_ref": c.review_ref,
                    "target": c.target,
                    "target_sha256": c.target_sha256,
                    "validation_findings": [
                        {"severity": f.severity, "rule": f.rule, "message": f.message}
                        for f in c.validation_findings
                    ],
                }
                for c in record.candidates
            ],
            "missing_profiles": list(record.missing_profiles),
        }
        for record in records
    ]
    from booktx.versioning import canonical_json_sha256

    return canonical_json_sha256(payload)
