"""Workflow functions for the translation preference dictionary CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from booktx import __version__
from booktx.config import (
    _err,
    canonical_language_key,
    lexicon_language_keys,
    load_translation_store,
)
from booktx.context import load_context
from booktx.io_utils import utc_timestamp
from booktx.lexicon import (
    EffectiveTranslationLexicon,
    LexiconEntry,
    TranslationLexicon,
    create_lexicon_backup,
    infer_mutation_language_key,
    merge_effective_lexicon,
    resolve_effective_lexicon,
    resolved_lexicon_layers,
    write_lexicon_shard,
)
from booktx.lexicon_audit import audit_lexicon, scan_source_lexicon
from booktx.path_display import display_path
from booktx.review_refs import format_review_ref
from booktx.review_tasks import ReviewSelectedRecord, create_review_task
from booktx.runtime import RuntimeContext, resolve_runtime
from booktx.status import build_status_snapshot
from booktx.translation_store import (
    active_candidate,
    active_review_candidate,
    sha256_text,
)
from booktx.workflows.context import (
    add_question_workflow,
    mandate_term_workflow,
    reset_term_workflow,
)

__all__ = [
    "lexicon_add_workflow",
    "lexicon_audit_workflow",
    "lexicon_export_workflow",
    "lexicon_import_workflow",
    "lexicon_promote_context_workflow",
    "lexicon_scan_source_workflow",
    "lexicon_status_workflow",
    "lexicon_write_review_workflow",
]


def _load_runtime(
    project_dir: Path | None,
    *,
    profile: str | None,
    require_profile: bool,
) -> RuntimeContext | None:
    if project_dir is None:
        return None
    return resolve_runtime(
        project_dir, profile=profile, require_profile=require_profile
    )


def _display_global_path(path: Path) -> str:
    exact = path.expanduser().resolve()
    override_path = os.environ.get("BOOKTX_LEXICON_PATH", "").strip()
    if override_path and Path(override_path).expanduser().resolve() == exact:
        return "$BOOKTX_LEXICON_PATH"
    override_dir = os.environ.get("BOOKTX_LEXICON_DIR", "").strip()
    if override_dir:
        resolved_dir = Path(override_dir).expanduser().resolve()
        try:
            rel = exact.relative_to(resolved_dir).as_posix()
            return f"$BOOKTX_LEXICON_DIR/{rel}" if rel else "$BOOKTX_LEXICON_DIR"
        except ValueError:
            pass
    home = Path.home().resolve()
    try:
        rel = exact.relative_to(home).as_posix()
        return f"~/{rel}" if rel else "~"
    except ValueError:
        return exact.as_posix()


def _display_lexicon_path(path: Path, runtime: RuntimeContext | None) -> str:
    if runtime is not None:
        try:
            rendered = display_path(path, runtime.mode)
            if rendered != "<hidden>":
                return rendered
        except Exception:  # noqa: BLE001 - fall back to global formatting
            pass
    return _display_global_path(path)


def _default_scope(
    project_dir: Path | None, scope: str | None, *, project_default: str
) -> str:
    if scope is not None:
        return scope
    return "global" if project_dir is None else project_default


def _empty_shard(
    language_key: str,
    *,
    source_language: str | None,
) -> TranslationLexicon:
    key = canonical_language_key(language_key)
    parts = key.split("-", 1)
    target_language = parts[0]
    target_locale = key if len(parts) > 1 else ""
    return TranslationLexicon(
        language_key=key,
        source_language=source_language,
        target_language=target_language,
        target_locale=target_locale,
        entries=[],
    )


def _scope_shard(
    runtime: RuntimeContext | None,
    *,
    scope: Literal["global", "project", "profile"],
    language_key: str,
    allow_global_exact_override: bool = False,
) -> tuple[Path, TranslationLexicon | None]:
    project = runtime.project if runtime is not None else None
    layers = resolved_lexicon_layers(
        project,
        language_keys=[language_key],
        scope=scope,
        allow_global_exact_override=allow_global_exact_override,
    )
    layer = layers[0]
    return layer.path, layer.shard


def _require_mutation_scope(
    runtime: RuntimeContext | None,
    *,
    scope: str,
) -> None:
    if runtime is None and scope != "global":
        raise _err("lexicon_project_required", "this lexicon scope requires a project")
    if (
        runtime is not None
        and runtime.mode.isolated_output
        and scope in {"global", "project"}
    ):
        raise _err(
            "lexicon_isolated_scope_blocked",
            "global and project lexicon mutations are blocked in "
            "profile-root isolated mode",
        )


def _effective_counts(effective: EffectiveTranslationLexicon) -> tuple[int, int]:
    active = sum(1 for entry in effective.entries if entry.status == "approved")
    disabled = sum(1 for entry in effective.entries if entry.status == "disabled")
    return active, disabled


def lexicon_status_workflow(
    project_dir: Path | None,
    *,
    profile: str | None,
    scope: str | None,
    language: str | None,
) -> dict[str, Any]:
    runtime = _load_runtime(project_dir, profile=profile, require_profile=False)
    resolved_scope = _default_scope(project_dir, scope, project_default="effective")
    if runtime is None and resolved_scope != "global":
        raise _err("lexicon_project_required", "this lexicon scope requires a project")
    if runtime is None and language is None:
        raise _err(
            "lexicon_language_required", "--language is required outside a project"
        )
    if resolved_scope == "effective":
        if runtime is None:
            raise _err(
                "lexicon_project_required",
                "effective lexicon status requires a project",
            )
        language_keys = lexicon_language_keys(runtime.project, language)
        layers = resolved_lexicon_layers(
            runtime.project, language_keys=language_keys, scope="effective"
        )
        effective = merge_effective_lexicon(layers, language_keys=language_keys)
        active_entries, disabled_entries = _effective_counts(effective)
        target_locale = effective.target_locale or effective.target_language
    else:
        language_keys = (
            lexicon_language_keys(runtime.project, language)
            if runtime is not None
            else [canonical_language_key(language or "")]
        )
        layers = resolved_lexicon_layers(
            runtime.project if runtime is not None else None,
            language_keys=language_keys,
            scope=resolved_scope,  # type: ignore[arg-type]
        )
        effective = merge_effective_lexicon(layers, language_keys=language_keys)
        active_entries, disabled_entries = _effective_counts(effective)
        target_locale = effective.target_locale or effective.target_language
    conflict_ids: dict[str, int] = {}
    for layer in layers:
        if layer.shard is None:
            continue
        for entry in layer.shard.entries:
            conflict_ids[entry.id] = conflict_ids.get(entry.id, 0) + 1
    return {
        "scope": resolved_scope,
        "language_keys": language_keys,
        "target_locale": target_locale,
        "active_entries": active_entries,
        "disabled_entries": disabled_entries,
        "conflicts": sorted(
            entry_id for entry_id, count in conflict_ids.items() if count > 1
        ),
        "layers": [
            {
                "scope": layer.scope,
                "language_key": layer.language_key,
                "path": _display_lexicon_path(layer.path, runtime),
                "exists": layer.exists,
                "entry_count": len(layer.shard.entries)
                if layer.shard is not None
                else 0,
            }
            for layer in layers
        ],
    }


def lexicon_add_workflow(
    project_dir: Path | None,
    *,
    profile: str | None,
    scope: str | None,
    language: str | None,
    entry_id: str,
    kind: str,
    source: str,
    source_variants: list[str],
    source_regex: str | None,
    preferred: list[str],
    allowed: list[str],
    forbidden: list[str],
    forbidden_regex: list[str],
    preferred_policy: str,
    sense: str,
    rationale: str,
    severity: str,
    approve: bool,
) -> dict[str, Any]:
    runtime = _load_runtime(project_dir, profile=profile, require_profile=False)
    resolved_scope = _default_scope(project_dir, scope, project_default="global")
    if project_dir is not None and scope is None:
        raise _err(
            "lexicon_scope_required",
            "--scope is required when a project path is supplied",
        )
    _require_mutation_scope(runtime, scope=resolved_scope)
    if resolved_scope not in {"global", "project", "profile"}:
        raise _err(
            "lexicon_scope_invalid", "--scope must be global, project, or profile"
        )
    if runtime is None and language is None:
        raise _err(
            "lexicon_language_required", "--language is required outside a project"
        )
    language_key = (
        canonical_language_key(language)
        if language is not None
        else infer_mutation_language_key(runtime.project)
    )
    path, shard = _scope_shard(
        runtime,
        scope=resolved_scope,  # type: ignore[arg-type]
        language_key=language_key,
        allow_global_exact_override=True,
    )
    project = runtime.project if runtime is not None else None
    existing = shard or _empty_shard(
        language_key,
        source_language=(
            project.config.source_language if project is not None else None
        ),
    )
    if any(entry.id == entry_id for entry in existing.entries):
        raise _err("lexicon_entry_exists", f"lexicon entry already exists: {entry_id}")
    new_entry = LexiconEntry(
        id=entry_id,
        status="approved" if approve else "draft",
        kind=kind,  # type: ignore[arg-type]
        source=source,
        source_variants=source_variants,
        source_regex=source_regex,
        source_language=(
            project.config.source_language
            if project is not None
            else existing.source_language or "en"
        ),
        target_preferred=preferred,
        target_allowed=allowed,
        target_forbidden=forbidden,
        target_regex_forbidden=forbidden_regex,
        preferred_policy=preferred_policy,  # type: ignore[arg-type]
        target_language=existing.target_language,
        target_locale=existing.target_locale,
        sense=sense,
        rationale=rationale,
        severity=severity,  # type: ignore[arg-type]
        created_at=utc_timestamp(),
        updated_at=utc_timestamp(),
        created_by_kind="user" if approve else "unknown",
    )
    write_lexicon_shard(
        path, existing.model_copy(update={"entries": [*existing.entries, new_entry]})
    )
    return {
        "path": _display_lexicon_path(path, runtime),
        "scope": resolved_scope,
        "language_key": language_key,
        "entry_id": entry_id,
        "status": new_entry.status,
    }


def _export_bundle(
    *,
    scope: str,
    language_keys: list[str],
    path_strings: list[str],
    payload: dict[str, Any],
) -> str:
    return (
        json.dumps(
            {
                "bundle_version": 1,
                "booktx_version": __version__,
                "scope": scope,
                "language_keys": language_keys,
                "layer_paths": path_strings,
                **payload,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def lexicon_export_workflow(
    project_dir: Path | None,
    *,
    profile: str | None,
    scope: str | None,
    language: str | None,
    output: Path | None,
    stdout: bool,
    export_format: str,
) -> dict[str, Any]:
    runtime = _load_runtime(project_dir, profile=profile, require_profile=False)
    resolved_scope = _default_scope(project_dir, scope, project_default="effective")
    if runtime is None and resolved_scope != "global":
        raise _err("lexicon_project_required", "this lexicon scope requires a project")
    if runtime is None and language is None:
        raise _err(
            "lexicon_language_required", "--language is required outside a project"
        )
    if not stdout and output is None:
        raise _err(
            "lexicon_output_required", "--output is required unless --stdout is passed"
        )
    if export_format not in {"shard", "bundle"}:
        raise _err("lexicon_export_format", "--format must be shard or bundle")
    if resolved_scope == "effective":
        if runtime is None:
            raise _err(
                "lexicon_project_required", "effective export requires a project"
            )
        effective, layers = resolve_effective_lexicon(
            runtime.project, language=language
        )
        payload = {
            "effective": {
                "language_keys": effective.language_keys,
                "source_language": effective.source_language,
                "target_language": effective.target_language,
                "target_locale": effective.target_locale,
                "entries": [
                    entry.model_dump(mode="json") for entry in effective.entries
                ],
            }
        }
        text = _export_bundle(
            scope=resolved_scope,
            language_keys=effective.language_keys,
            path_strings=[
                _display_lexicon_path(layer.path, runtime) for layer in layers
            ],
            payload=payload,
        )
    else:
        language_key = (
            canonical_language_key(language)
            if language is not None
            else infer_mutation_language_key(runtime.project)
        )
        path, shard = _scope_shard(
            runtime,
            scope=resolved_scope,  # type: ignore[arg-type]
            language_key=language_key,
            allow_global_exact_override=True,
        )
        project = runtime.project if runtime is not None else None
        exported = shard or _empty_shard(
            language_key,
            source_language=(
                project.config.source_language if project is not None else None
            ),
        )
        if export_format == "bundle":
            text = _export_bundle(
                scope=resolved_scope,
                language_keys=[language_key],
                path_strings=[_display_lexicon_path(path, runtime)],
                payload={"shard": exported.model_dump(mode="json")},
            )
        else:
            from booktx.lexicon import canonical_lexicon_json

            text = canonical_lexicon_json(exported)
    if stdout:
        return {"stdout": text}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    return {"path": str(output), "scope": resolved_scope}


def _load_import_shard(input_path: Path, import_format: str) -> TranslationLexicon:
    payload = json.loads(input_path.read_text("utf-8"))
    if import_format == "auto":
        if "language_key" in payload and "entries" in payload:
            return TranslationLexicon.model_validate(payload)
        if "shard" in payload:
            return TranslationLexicon.model_validate(payload["shard"])
        raise _err("lexicon_import_format", "could not detect shard or bundle format")
    if import_format == "bundle":
        if "shard" not in payload:
            raise _err("lexicon_import_format", "bundle payload is missing 'shard'")
        return TranslationLexicon.model_validate(payload["shard"])
    return TranslationLexicon.model_validate(payload)


def _merge_import_entries(
    existing: TranslationLexicon,
    imported: TranslationLexicon,
    *,
    on_conflict: str,
) -> tuple[list[LexiconEntry], int, int]:
    if on_conflict not in {"fail", "skip", "overwrite", "newer"}:
        raise _err(
            "lexicon_conflict_policy",
            "--on-conflict must be fail, skip, overwrite, or newer",
        )
    merged = {entry.id: entry for entry in existing.entries}
    added = 0
    updated = 0
    for incoming in imported.entries:
        current = merged.get(incoming.id)
        if current is None:
            merged[incoming.id] = incoming
            added += 1
            continue
        if on_conflict == "skip":
            continue
        if on_conflict == "fail":
            raise _err(
                "lexicon_conflict",
                f"entry id conflict while importing: {incoming.id}",
            )
        if on_conflict == "overwrite":
            merged[incoming.id] = incoming
            updated += 1
            continue
        if not current.updated_at or not incoming.updated_at:
            raise _err(
                "lexicon_conflict_newer",
                f"entry {incoming.id} is missing updated_at required by "
                f"--on-conflict newer",
            )
        if current.updated_at == incoming.updated_at:
            raise _err(
                "lexicon_conflict_newer",
                f"entry {incoming.id} has equal updated_at values; "
                f"cannot resolve --on-conflict newer",
            )
        if incoming.updated_at > current.updated_at:
            merged[incoming.id] = incoming
            updated += 1
    return sorted(merged.values(), key=lambda entry: entry.id), added, updated


def lexicon_import_workflow(
    project_dir: Path | None,
    *,
    profile: str | None,
    scope: str | None,
    language: str | None,
    input_path: Path,
    mode: str,
    on_conflict: str,
    approve_drafts: bool,
    import_format: str,
) -> dict[str, Any]:
    runtime = _load_runtime(project_dir, profile=profile, require_profile=False)
    resolved_scope = _default_scope(project_dir, scope, project_default="global")
    if resolved_scope == "effective":
        raise _err("lexicon_import_scope", "effective is not a valid import scope")
    if project_dir is not None and scope is None:
        raise _err(
            "lexicon_scope_required",
            "--scope is required when a project path is supplied",
        )
    _require_mutation_scope(runtime, scope=resolved_scope)
    if runtime is None and language is None:
        raise _err(
            "lexicon_language_required", "--language is required outside a project"
        )
    if mode not in {"dry-run", "merge", "replace"}:
        raise _err("lexicon_import_mode", "--mode must be dry-run, merge, or replace")
    language_key = (
        canonical_language_key(language)
        if language is not None
        else infer_mutation_language_key(runtime.project)
    )
    path, existing = _scope_shard(
        runtime,
        scope=resolved_scope,  # type: ignore[arg-type]
        language_key=language_key,
        allow_global_exact_override=True,
    )
    imported = _load_import_shard(input_path, import_format)
    if approve_drafts:
        imported = imported.model_copy(
            update={
                "entries": [
                    entry
                    if entry.status != "draft"
                    else entry.model_copy(update={"status": "approved"})
                    for entry in imported.entries
                ]
            }
        )
    if imported.language_key != language_key:
        raise _err(
            "lexicon_language_mismatch",
            f"imported shard language_key {imported.language_key!r} does not match "
            f"destination {language_key!r}",
        )
    destination = existing or _empty_shard(
        language_key,
        source_language=(
            runtime.project.config.source_language
            if runtime is not None
            else imported.source_language
        ),
    )
    if mode == "replace":
        summary = {
            "scope": resolved_scope,
            "language_key": language_key,
            "added": len(imported.entries),
            "updated": 0,
            "mode": mode,
        }
        if mode != "dry-run" and existing is not None:
            summary["backup_path"] = str(create_lexicon_backup(path))
        if mode == "replace":
            write_lexicon_shard(path, imported)
        return summary
    entries, added, updated = _merge_import_entries(
        destination, imported, on_conflict=on_conflict
    )
    summary = {
        "scope": resolved_scope,
        "language_key": language_key,
        "added": added,
        "updated": updated,
        "mode": mode,
    }
    if mode == "merge":
        if existing is not None and updated > 0:
            summary["backup_path"] = str(create_lexicon_backup(path))
        write_lexicon_shard(path, destination.model_copy(update={"entries": entries}))
    return summary


def lexicon_scan_source_workflow(
    project_dir: Path,
    *,
    profile: str | None,
    language: str | None,
    chapter: str | None,
    entry_ids: list[str],
) -> dict[str, Any]:
    runtime = resolve_runtime(project_dir, profile=profile, require_profile=True)
    context = load_context(runtime.project)
    effective, _ = resolve_effective_lexicon(runtime.project, language=language)
    result = scan_source_lexicon(
        runtime.project,
        build_status_snapshot(
            runtime.project,
            context_exists=context is not None,
            context_ready=bool(context and context.ready),
        ),
        effective,
        chapter_id=chapter,
        entry_ids=set(entry_ids) or None,
    )
    return result.model_dump(mode="json")


def lexicon_audit_workflow(
    project_dir: Path,
    *,
    profile: str | None,
    language: str | None,
    chapter: str | None,
    entry_ids: list[str],
) -> dict[str, Any]:
    runtime = resolve_runtime(project_dir, profile=profile, require_profile=True)
    context = load_context(runtime.project)
    bundle = build_status_snapshot(
        runtime.project,
        context_exists=context is not None,
        context_ready=bool(context and context.ready),
    )
    effective, _ = resolve_effective_lexicon(runtime.project, language=language)
    result = audit_lexicon(
        runtime.project,
        bundle,
        effective,
        chapter_id=chapter,
        entry_ids=set(entry_ids) or None,
    )
    return result.model_dump(mode="json")


def lexicon_promote_context_workflow(
    project_dir: Path,
    *,
    profile: str | None,
    language: str | None,
    entry_id: str,
    as_advisory: bool,
    as_binding: bool,
    as_question: bool,
) -> str:
    runtime = resolve_runtime(project_dir, profile=profile, require_profile=True)
    effective, _ = resolve_effective_lexicon(runtime.project, language=language)
    entry = next((item for item in effective.entries if item.id == entry_id), None)
    if entry is None or entry.status != "approved":
        raise _err(
            "lexicon_entry_missing", f"approved lexicon entry not found: {entry_id}"
        )
    if sum(1 for flag in (as_advisory, as_binding, as_question) if flag) > 1:
        raise _err(
            "lexicon_promote_mode",
            "choose only one of --as-advisory, --as-binding, or --as-question",
        )
    if as_binding:
        if len(entry.target_preferred) != 1:
            raise _err(
                "lexicon_promote_binding",
                "--as-binding requires exactly one preferred target",
            )
        ctx = load_context(runtime.project)
        if ctx is None:
            raise _err(
                "missing_context",
                "translation context is missing. Run: booktx context init .",
            )
        return mandate_term_workflow(
            runtime.project,
            ctx,
            source=entry.source,
            target=entry.target_preferred[0],
            source_variant=entry.source_variants,
            target_variant=[],
            forbid=entry.target_forbidden,
            category="phrase",
            notes=(entry.sense or entry.rationale).strip(),
            enforce="error",
        )
    if as_question or (not as_advisory and len(entry.target_preferred) != 1):
        ctx = load_context(runtime.project)
        if ctx is None:
            raise _err(
                "missing_context",
                "translation context is missing. Run: booktx context init .",
            )
        ctx.ready = False
        preferred = (
            ", ".join(entry.target_preferred) or "(no preferred targets recorded)"
        )
        return add_question_workflow(
            runtime.project,
            ctx,
            topic="lexicon",
            question=(
                f"How should {entry.source!r} be promoted into the local glossary? "
                f"Current preferred targets: {preferred}."
            ),
            required=True,
            origin="agent_review",
            recommendation=entry.target_preferred[0]
            if len(entry.target_preferred) == 1
            else None,
            reason="Promoted from translation lexicon.",
            source="lexicon",
            question_id=None,
            allow_duplicate=False,
        )
    ctx = load_context(runtime.project)
    if ctx is None:
        raise _err(
            "missing_context",
            "translation context is missing. Run: booktx context init .",
        )
    return reset_term_workflow(
        runtime.project,
        ctx,
        source=entry.source,
        target=entry.target_preferred[0],
        forbid=None,
        category="phrase",
        notes=(entry.sense or entry.rationale).strip(),
        enforce="warn",
        source_variant=entry.source_variants,
        target_variant=[],
        require_target=False,
        allow_disable_enforcement=False,
        create=True,
    )


def lexicon_write_review_workflow(
    project_dir: Path,
    *,
    profile: str | None,
    language: str | None,
    entry_ids: list[str],
    pass_number: int,
    include_clean_matches: bool,
) -> dict[str, Any]:
    runtime = resolve_runtime(project_dir, profile=profile, require_profile=True)
    quality_cfg = (
        runtime.project.profile_config.quality_review
        if runtime.project.profile_config is not None
        else None
    )
    if quality_cfg is None or not quality_cfg.enabled:
        raise _err(
            "review_not_enabled",
            "quality review is not enabled for this profile",
        )
    if pass_number not in quality_cfg.active_passes:
        raise _err(
            "review_pass_not_active",
            f"pass {pass_number} is not in active_passes {quality_cfg.active_passes}",
        )
    context = load_context(runtime.project)
    bundle = build_status_snapshot(
        runtime.project,
        context_exists=context is not None,
        context_ready=bool(context and context.ready),
    )
    effective, _ = resolve_effective_lexicon(runtime.project, language=language)
    audit_result = audit_lexicon(
        runtime.project,
        bundle,
        effective,
        entry_ids=set(entry_ids) or None,
    )
    candidate_matches = [
        match
        for match in audit_result.matches
        if include_clean_matches or match.status != "clean"
    ]
    if not candidate_matches:
        raise _err(
            "lexicon_no_review_records",
            "no lexicon-matched records need review for the requested selection",
        )
    first_chapter_id = candidate_matches[0].chapter_id
    store = load_translation_store(runtime.project)
    selected: list[ReviewSelectedRecord] = []
    seen_records: set[str] = set()
    for match in candidate_matches:
        if match.record_id in seen_records or match.chapter_id != first_chapter_id:
            continue
        stored = store.records.get(match.record_id)
        if stored is None:
            continue
        base_review = active_review_candidate(stored)
        if base_review is not None:
            if base_review.pass_number != pass_number:
                raise _err(
                    "lexicon_review_active_pass_mismatch",
                    f"record {match.record_id} has active review "
                    f"{base_review.review_ref}; use --pass {base_review.pass_number}",
                )
            base_kind = "review"
            base_ref = base_review.review_ref
            base_target = base_review.target
        else:
            base_translation = active_candidate(stored)
            if base_translation is None:
                continue
            base_kind = "translation"
            base_ref = base_translation.version_ref
            base_target = base_translation.target
        next_run = 1 + max(
            (
                review.run_number
                for review in stored.reviews
                if review.pass_number == pass_number
            ),
            default=0,
        )
        source_view = bundle.index.source_by_id[match.record_id]
        selected.append(
            ReviewSelectedRecord(
                record_id=match.record_id,
                chunk_id=source_view.chunk_id,
                source=source_view.source,
                base_kind=base_kind,
                base_ref=base_ref,
                base_target=base_target,
                base_target_sha256=sha256_text(base_target),
                review_ref=format_review_ref(pass_number, next_run),
                pass_number=pass_number,
            )
        )
        seen_records.add(match.record_id)
    if not selected:
        raise _err(
            "lexicon_no_review_records",
            "no lexicon-matched records were eligible for review task creation",
        )
    chapter = bundle.index.chapters_by_id[first_chapter_id]
    task = create_review_task(
        runtime.project,
        bundle,
        quality_cfg,
        selected,
        pass_number=pass_number,
        chapter=chapter,
    )
    review_dir = runtime.project.profile_dir or runtime.project.root
    return {
        "review_task_id": task.review_task_id,
        "record_count": task.record_count,
        "source_block": display_path(
            review_dir / "reviews" / f"{task.review_task_id}.source.block.txt",
            runtime.mode,
        ),
        "ingest_block": display_path(
            review_dir / "reviews" / f"{task.review_task_id}.block.txt", runtime.mode
        ),
    }
