"""Binding glossary wrappers over the canonical termbase and context workflows."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from booktx.config import Project
    from booktx.context import TranslationContext
from booktx.cli_support import _load_project_or_exit, _project_status_snapshot
from booktx.context import load_context, write_context, write_context_markdown
from booktx.errors import _err
from booktx.workflows.context import (
    audit_term_workflow,
    mandate_term_workflow,
    remove_term_workflow,
    reset_term_workflow,
)
from booktx.workflows.termbase import (
    termbase_add_workflow,
    termbase_export_workflow,
    termbase_import_workflow,
    termbase_status_workflow,
)

__all__ = [
    "glossary_add_workflow",
    "glossary_audit_workflow",
    "glossary_export_workflow",
    "glossary_import_workflow",
    "glossary_mandate_workflow",
    "glossary_remove_workflow",
    "glossary_reset_workflow",
    "glossary_status_workflow",
    "glossary_add_variant_workflow",
    "glossary_set_usage_workflow",
]


def _default_scope(project_dir: Path | None, scope: str | None) -> str | None:
    if scope is not None:
        return scope
    return "profile" if project_dir is not None else None


def _entry_id_for_source(source: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", source.lower()).strip("-") or "term"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:8]
    return f"glossary-{slug}-{digest}"


def glossary_status_workflow(
    project_dir: Path | None,
    *,
    profile: str | None,
    scope: str | None,
    language: str | None,
) -> dict[str, Any]:
    return termbase_status_workflow(
        project_dir,
        profile=profile,
        scope=_default_scope(project_dir, scope),
        language=language,
    )


def glossary_export_workflow(
    project_dir: Path | None,
    *,
    profile: str | None,
    scope: str | None,
    language: str | None,
    output: Path | None,
    stdout: bool,
    export_format: str,
) -> dict[str, Any]:
    return termbase_export_workflow(
        project_dir,
        profile=profile,
        scope=_default_scope(project_dir, scope),
        language=language,
        output=output,
        stdout=stdout,
        export_format=export_format,
    )


def glossary_import_workflow(
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
    return termbase_import_workflow(
        project_dir,
        profile=profile,
        scope=_default_scope(project_dir, scope),
        language=language,
        input_path=input_path,
        mode=mode,
        on_conflict=on_conflict,
        approve_drafts=approve_drafts,
        import_format=import_format,
    )


def glossary_add_workflow(
    project_dir: Path | None,
    *,
    profile: str | None,
    scope: str | None,
    language: str | None,
    entry_file: Path | None,
    source: str,
    source_variants: list[str],
    target: str,
    target_variants: list[str],
    forbidden: list[str],
    require_target: bool,
    enforce: str,
    case_sensitive: bool,
    notes: str,
) -> dict[str, Any]:
    preferred = [target] if target else []
    preferred.extend(target_variants)
    return termbase_add_workflow(
        project_dir,
        profile=profile,
        scope=_default_scope(project_dir, scope),
        language=language,
        entry_file=entry_file,
        entry_id=None if entry_file is not None else _entry_id_for_source(source),
        kind="flat_term",
        source=source,
        source_variants=source_variants,
        source_regex=None,
        case_sensitive=case_sensitive,
        preferred=preferred,
        allowed=[],
        forbidden=forbidden,
        forbidden_regex=[],
        preferred_policy="required" if require_target else "off",
        sense="",
        rationale=notes,
        severity="error" if enforce == "error" else "warn",
        approve=True,
    )


def _load_context_project(
    project_dir: Path | None, profile: str | None
) -> tuple[Project, TranslationContext]:
    if project_dir is None:
        raise _err(
            "glossary_project_required",
            "glossary mutation commands require a project directory or profile root",
        )
    project = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    context = load_context(project)
    if context is None:
        raise _err(
            "glossary_context_missing",
            "translation context is missing; run `booktx context init` first",
        )
    return project, context


def glossary_remove_workflow(
    project_dir: Path | None,
    *,
    profile: str | None,
    source: str,
    missing_ok: bool,
) -> str:
    project, context = _load_context_project(project_dir, profile)
    return remove_term_workflow(project, context, source=source, missing_ok=missing_ok)


def glossary_reset_workflow(
    project_dir: Path | None,
    *,
    profile: str | None,
    source: str,
    target: str | None,
    forbid: list[str],
    category: str | None,
    notes: str,
    enforce: str,
    source_variants: list[str],
    target_variants: list[str],
    require_target: bool,
    case_sensitive: bool,
    create: bool,
) -> str:
    project, context = _load_context_project(project_dir, profile)
    return reset_term_workflow(
        project,
        context,
        source=source,
        target=target,
        forbid=forbid,
        category=category,
        notes=notes,
        enforce=enforce,
        source_variant=source_variants,
        target_variant=target_variants,
        require_target=require_target,
        case_sensitive=case_sensitive,
        allow_disable_enforcement=False,
        create=create,
    )


def glossary_mandate_workflow(
    project_dir: Path | None,
    *,
    profile: str | None,
    source: str,
    target: str | None,
    forbid: list[str],
    category: str | None,
    notes: str,
    enforce: str,
    source_variants: list[str],
    target_variants: list[str],
    case_sensitive: bool,
) -> str:
    project, context = _load_context_project(project_dir, profile)
    return mandate_term_workflow(
        project,
        context,
        source=source,
        target=target,
        source_variant=source_variants,
        target_variant=target_variants,
        forbid=forbid,
        category=category,
        notes=notes,
        enforce=enforce,
        case_sensitive=case_sensitive,
    )


def _update_usage_entry(
    project_dir: Path | None,
    *,
    profile: str | None,
    source: str,
    target: str,
    usage: str,
    add_variant: bool,
) -> str:
    project, context = _load_context_project(project_dir, profile)
    entry = next((item for item in context.glossary if item.source == source), None)
    if entry is None:
        raise _err("term_missing", f"no glossary entry for source: {source}")
    if add_variant and target not in entry.target_variants and target != entry.target:
        entry.target_variants.append(target)
    entry.usage_notes[usage] = target
    write_context(project, context)
    write_context_markdown(project, context)
    return f"updated glossary usage: {source} ({usage})"


def glossary_add_variant_workflow(
    project_dir: Path | None,
    *,
    profile: str | None,
    source: str,
    target: str,
    usage: str,
) -> str:
    return _update_usage_entry(
        project_dir,
        profile=profile,
        source=source,
        target=target,
        usage=usage,
        add_variant=True,
    )


def glossary_set_usage_workflow(
    project_dir: Path | None,
    *,
    profile: str | None,
    source: str,
    target: str,
    usage: str,
) -> str:
    return _update_usage_entry(
        project_dir,
        profile=profile,
        source=source,
        target=target,
        usage=usage,
        add_variant=True,
    )


def glossary_audit_workflow(
    project_dir: Path | None,
    *,
    profile: str | None,
    source: str,
    chapter: str | None,
    include_inactive: bool,
) -> dict[str, Any]:
    project, context = _load_context_project(project_dir, profile)
    bundle = _project_status_snapshot(project)
    result = audit_term_workflow(
        project,
        context,
        source=source,
        chapter=chapter,
        include_inactive=include_inactive,
        bundle=bundle,
    )
    payload = result.as_dict()
    payload["source"] = source
    payload["chapter"] = chapter
    payload["finding_count"] = len(cast(list[Any], payload["records"])) + len(
        cast(list[Any], payload["inactive_records"])
    )
    payload["records_with_matches"] = result.records_with_source_term
    return payload
