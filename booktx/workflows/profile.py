"""Domain workflow functions for translation-profile management (Phase 3 slice 4).

Wraps the config / translation_store / status service layer so the command
layer never imports ``booktx.config`` or ``booktx.translation_store`` directly.
Not-found / invalid cases raise :class:`booktx.errors.BooktxError`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from booktx.config import (
    create_profile,
    load_profile_config,
    load_profile_project,
    load_source_project,
    load_translation_selection_ledger,
    load_translation_store,
    load_translation_version_ledger,
    migrate_current_project,
)
from booktx.context import load_context
from booktx.errors import BooktxError
from booktx.progress import load_source_records
from booktx.record_refs import parse_record_ref
from booktx.status import build_profiles_overview, build_status_snapshot
from booktx.translation_store import active_candidate
from booktx.versioning import resolve_identity

if TYPE_CHECKING:
    from booktx.config import Project
    from booktx.status import ProfilesOverview


def build_profiles_overview_payload(root: Path) -> ProfilesOverview:
    project = load_source_project(root)
    return build_profiles_overview(project)


def build_profile_detail_payload(
    project_dir: Path, profile_name: str
) -> dict[str, Any]:
    profile_project = load_profile_project(project_dir, profile_name)
    profile_cfg = load_profile_config(project_dir, profile_name)
    resolved_identity = resolve_identity(profile_project)
    context = load_context(profile_project)
    active_version = load_translation_version_ledger(profile_project).active_version
    records_translated = 0
    records_total = 0
    chapters_complete = 0
    chapters_total = 0
    if profile_project.chunks():
        bundle = build_status_snapshot(
            profile_project,
            context_exists=context is not None,
            context_ready=bool(context and context.ready),
        )
        records_translated = bundle.snapshot.totals.records_translated
        records_total = bundle.snapshot.totals.records_total
        chapters_complete = bundle.snapshot.totals.chapters_complete
        chapters_total = bundle.snapshot.totals.chapters_total
    from booktx.tasks import project_relative

    return {
        "profile": profile_name,
        "kind": profile_cfg.kind,
        "path": project_relative(profile_project.profile_dir, profile_project.root)
        if profile_project.profile_dir is not None
        else "",
        "target_language": profile_cfg.target_language,
        "target_locale": profile_cfg.target_locale or profile_cfg.target_language,
        "output_filename": profile_cfg.output_filename,
        # Live identity comes from translations/<profile>/identity.json;
        # profile_cfg.identity is only the initial default captured at creation.
        "actor": resolved_identity.actor,
        "harness": resolved_identity.harness,
        "model": resolved_identity.model,
        "context_ready": bool(context and context.ready),
        "active_version": active_version,
        "records_translated": records_translated,
        "records_total": records_total,
        "chapters_complete": chapters_complete,
        "chapters_total": chapters_total,
    }


def create_profile_workflow(
    project_dir: Path,
    profile_name: str,
    *,
    target_language: str,
    target_locale: str | None = None,
    actor: str | None = None,
    harness: str | None = None,
    model: str | None = None,
    output_filename: str | None = None,
    kind: str = "translation",
) -> Project:
    return create_profile(
        project_dir,
        profile_name,
        target_language=target_language,
        target_locale=target_locale,
        actor=actor,
        harness=harness,
        model=model,
        output_filename=output_filename,
        kind=kind,  # type: ignore[arg-type]
    )


def migrate_current_workflow(
    project_dir: Path,
    profile_name: str,
    *,
    target_language: str | None = None,
    target_locale: str | None = None,
    actor: str | None = None,
    harness: str | None = None,
    model: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    return migrate_current_project(
        project_dir,
        profile_name,
        target_language=target_language,
        target_locale=target_locale,
        actor=actor,
        harness=harness,
        model=model,
        dry_run=dry_run,
    )


def create_pass_through_workflow(
    project_dir: Path,
    profile_name: str,
    *,
    output_filename: str | None = None,
) -> Project:
    source_project = load_source_project(project_dir)
    target = source_project.source_config.source_language
    return create_profile(
        project_dir,
        profile_name,
        target_language=target,
        target_locale=target,
        actor="booktx:pass-through",
        harness="booktx",
        model="booktx/pass-through",
        output_filename=output_filename,
        kind="pass-through",
    )


def compare_profile_record(root: Path, profiles: str, record: str) -> dict[str, Any]:
    """Build the cross-profile comparison payload for one record.

    Raises :class:`booktx.errors.BooktxError` for an unknown source record.
    Per-profile load errors propagate as ``BooktxError`` from the loaders.
    """
    requested = [item.strip() for item in profiles.split(",") if item.strip()]
    if len(requested) < 2:
        raise BooktxError(
            "invalid_compare_profiles",
            "--profiles must contain at least two profile names",
        )
    canonical_id = parse_record_ref(record).canonical_id
    source_project = load_source_project(root)
    source_by_id = {
        item.record_id: item for item in load_source_records(source_project)
    }
    source_record = source_by_id.get(canonical_id)
    if source_record is None:
        raise BooktxError(
            "unknown_source_record", f"unknown source record id: {canonical_id}"
        )
    comparisons: list[dict[str, Any]] = []
    for profile_name in requested:
        profile_project = load_profile_project(root, profile_name)
        store = load_translation_store(profile_project)
        stored = store.records.get(canonical_id)
        candidate = active_candidate(stored) if stored is not None else None
        provenance = None
        if (
            profile_project.profile_config is not None
            and profile_project.profile_config.kind == "selection"
        ):
            decision = load_translation_selection_ledger(profile_project).records.get(
                canonical_id
            )
            if decision is not None:
                provenance = {
                    "selected_profile": decision.selected_profile,
                    "selected_ref": decision.selected_ref,
                    "selected_kind": decision.selected_kind,
                }
        comparisons.append(
            {
                "profile": profile_name,
                "target_language": profile_project.config.target_language,
                "target_locale": profile_project.config.target_locale,
                "active_version": stored.active_version if stored is not None else None,
                "target": candidate.target if candidate is not None else None,
                "status": candidate.status if candidate is not None else None,
                "selection_provenance": provenance,
            }
        )
    return {
        "record_ref": canonical_id,
        "source": source_record.source,
        "comparisons": comparisons,
    }


__all__ = [
    "build_profile_detail_payload",
    "build_profiles_overview_payload",
    "compare_profile_record",
    "create_pass_through_workflow",
    "create_profile_workflow",
    "migrate_current_workflow",
]
