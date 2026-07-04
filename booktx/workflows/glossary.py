"""Binding glossary wrappers over the canonical termbase workflows."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from booktx.workflows.termbase import (
    termbase_add_workflow,
    termbase_export_workflow,
    termbase_import_workflow,
    termbase_status_workflow,
)

__all__ = [
    "glossary_add_workflow",
    "glossary_export_workflow",
    "glossary_import_workflow",
    "glossary_status_workflow",
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
