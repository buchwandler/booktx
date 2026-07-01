"""Controlled same-book context sync across sibling profiles."""

from __future__ import annotations

import re
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from booktx.config import (
    BooktxError,
    Project,
    load_context_sync_ledger,
    load_profile_project,
    load_source_project,
    write_context_sync_ledger,
)
from booktx.context import baseline_sha256, load_context
from booktx.context_packs import (
    ContextPackError,
    ContextPackImportFinding,
    ContextPackImportResult,
    PackConflictMode,
    SeriesContextPack,
    export_context_pack,
    import_context_pack,
    plan_context_pack_import,
)
from booktx.io_utils import utc_timestamp
from booktx.models import ContextSyncLedgerEntry, ContextSyncLedgerFinding
from booktx.versioning import canonical_json_sha256

__all__ = [
    "ContextSyncError",
    "ContextSyncSource",
    "ContextSyncTargetPlan",
    "ContextSyncPlan",
    "build_filtered_context_pack",
    "discover_sync_targets",
    "plan_context_sync",
    "apply_context_sync",
]

_SECTIONS = {"glossary", "style", "global-rules", "questions"}
_SERIES_ID_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class ContextSyncError(BooktxError):
    """Context sync failure with a stable ``code``."""


class ContextSyncSource(BaseModel):
    """Provenance metadata for the source profile in a sync plan."""

    model_config = ConfigDict(extra="forbid")

    profile: str
    target_language: str
    target_locale: str
    baseline_sha256: str
    context_sha256: str


class ContextSyncTargetPlan(BaseModel):
    """Per-target sync plan summary."""

    model_config = ConfigDict(extra="forbid")

    profile: str
    target_language: str
    target_locale: str
    kind: str
    eligible: bool
    skipped_reason: str = ""
    changed: bool = False
    errors: int = 0
    conflicts: int = 0
    warnings: int = 0
    added: int = 0
    updated: int = 0
    skipped: int = 0
    findings: list[ContextPackImportFinding] = Field(default_factory=list)


class ContextSyncPlan(BaseModel):
    """Consolidated cross-profile sync plan."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    sync_id: str
    source: ContextSyncSource
    sections: list[str]
    glossary_terms: list[str]
    question_ids: list[str] = Field(default_factory=list)
    conflict: str
    write: bool
    allow_not_ready: bool = False
    init_missing_context: bool = False
    targets: list[ContextSyncTargetPlan]
    would_write_profiles: list[str]
    blocked: bool


def _series_id_for_sync(project: Project) -> str:
    root_name = _SERIES_ID_SAFE.sub("-", project.root.name).strip("-") or "book"
    source = project.source_config.source_language or "src"
    target = project.config.target_language or "tgt"
    return f"{root_name}-{source}-{target}-context-sync"


def _normalize_sections(sections: set[str]) -> list[str]:
    if not sections:
        sections = {"glossary"}
    invalid = sorted(sections - _SECTIONS)
    if invalid:
        raise ContextSyncError(
            "sync_sections_invalid",
            "invalid --section values: " + ", ".join(invalid),
        )
    return [
        name
        for name in ("glossary", "style", "global-rules", "questions")
        if name in sections
    ]


def _normalize_term_identities(terms: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in terms:
        identity = raw.strip().casefold()
        if not identity or identity in seen:
            continue
        seen.add(identity)
        normalized.append(identity)
    return normalized


def _dedupe_preserve(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        value = raw.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def build_filtered_context_pack(
    source_project: Project,
    *,
    sections: set[str],
    terms: list[str],
    question_ids: list[str],
    allow_not_ready: bool,
) -> SeriesContextPack:
    """Export the source profile pack, filter it, and revalidate the payload."""

    section_list = _normalize_sections(sections)
    if terms and "glossary" not in section_list:
        raise ContextSyncError(
            "sync_terms_require_glossary",
            "--term requires --section glossary",
        )
    if question_ids and "questions" not in section_list:
        raise ContextSyncError(
            "sync_questions_require_questions_section",
            "--question-id requires --section questions",
        )

    pack = export_context_pack(
        source_project,
        series_id=_series_id_for_sync(source_project),
        include_style="style" in section_list,
        include_global_rules="global-rules" in section_list,
        include_glossary="glossary" in section_list,
        include_questions="approved" if "questions" in section_list else "none",
        allow_not_ready=allow_not_ready,
    )
    payload = pack.model_dump(mode="json")

    if "style" not in section_list:
        payload["style"] = None
    if "global-rules" not in section_list:
        payload["global_rules"] = []

    if "glossary" not in section_list:
        payload["glossary"] = []
    elif terms:
        normalized_terms = set(_normalize_term_identities(terms))
        glossary = payload.get("glossary", [])
        selected = [
            entry
            for entry in glossary
            if str(entry.get("source", "")).strip().casefold() in normalized_terms
        ]
        found = {str(entry.get("source", "")).strip().casefold() for entry in selected}
        missing = sorted(normalized_terms - found)
        if missing:
            raise ContextSyncError(
                "sync_term_missing",
                "requested source glossary term is missing in the source context: "
                + ", ".join(missing),
            )
        payload["glossary"] = selected

    if "questions" not in section_list:
        payload["questions"] = []
    elif question_ids:
        wanted = set(_dedupe_preserve(question_ids))
        questions = payload.get("questions", [])
        selected = [entry for entry in questions if entry.get("id") in wanted]
        found = {str(entry.get("id")) for entry in selected}
        missing = sorted(wanted - found)
        if missing:
            raise ContextSyncError(
                "sync_question_missing",
                "requested reusable question id is missing in the source context pack: "
                + ", ".join(missing),
            )
        payload["questions"] = selected

    return SeriesContextPack.model_validate(payload)


def discover_sync_targets(
    root: Path,
    *,
    source_profile: str,
    explicit_targets: list[str],
    all_compatible: bool,
    same_locale: bool,
    include_pass_through: bool,
    include_selection: bool,
) -> list[str]:
    """Resolve the target profiles for a sync request."""

    explicit = _dedupe_preserve(explicit_targets)
    if explicit and all_compatible:
        raise ContextSyncError(
            "sync_target_mode",
            "use either --to or --all-compatible, not both",
        )
    if not explicit and not all_compatible:
        raise ContextSyncError(
            "sync_targets_required",
            "provide one or more --to targets or pass --all-compatible",
        )

    source_project = load_profile_project(root, source_profile)
    source_cfg = source_project.profile_config
    assert source_cfg is not None

    if explicit:
        targets = [name for name in explicit if name != source_profile]
        if not targets:
            raise ContextSyncError(
                "sync_no_targets",
                "no target profiles remain after excluding the source profile",
            )
        return targets

    source_root = load_source_project(root)
    from booktx.config import list_profiles

    compatible: list[str] = []
    for profile_name in list_profiles(source_root):
        if profile_name == source_profile:
            continue
        target_project = load_profile_project(root, profile_name)
        target_cfg = target_project.profile_config
        assert target_cfg is not None
        if target_cfg.kind == "pass-through" and not include_pass_through:
            continue
        if target_cfg.kind == "selection" and not include_selection:
            continue
        if target_cfg.source_language != source_cfg.source_language:
            continue
        if target_cfg.target_language != source_cfg.target_language:
            continue
        if same_locale and (
            (target_cfg.target_locale or target_cfg.target_language)
            != (source_cfg.target_locale or source_cfg.target_language)
        ):
            continue
        compatible.append(profile_name)
    return compatible


def _compatibility_warning(
    source_project: Project,
    target_project: Project,
    *,
    same_locale: bool,
) -> list[ContextPackImportFinding]:
    source_cfg = source_project.profile_config
    target_cfg = target_project.profile_config
    assert source_cfg is not None
    assert target_cfg is not None
    if target_cfg.source_language != source_cfg.source_language:
        raise ContextSyncError(
            "sync_source_language_mismatch",
            f"target profile {target_project.profile} source language "
            f"{target_cfg.source_language!r} does not match source profile "
            f"{source_cfg.source_language!r}",
        )
    if target_cfg.target_language != source_cfg.target_language:
        raise ContextSyncError(
            "sync_target_language_mismatch",
            f"target profile {target_project.profile} target language "
            f"{target_cfg.target_language!r} does not match source profile "
            f"{source_cfg.target_language!r}",
        )
    source_locale = source_cfg.target_locale or source_cfg.target_language
    target_locale = target_cfg.target_locale or target_cfg.target_language
    if same_locale and target_locale != source_locale:
        raise ContextSyncError(
            "sync_target_locale_mismatch",
            f"target profile {target_project.profile} target locale "
            f"{target_locale!r} does not match source profile {source_locale!r}",
        )
    if not same_locale and target_locale != source_locale:
        return [
            ContextPackImportFinding(
                section="compatibility",
                key="target_locale",
                action="warning",
                message=(
                    f"target locale differs: source={source_locale!r} "
                    f"target={target_locale!r}"
                ),
            )
        ]
    return []


def _make_sync_id(
    source_profile: str,
    *,
    sections: list[str],
    glossary_terms: list[str],
    question_ids: list[str],
) -> str:
    stamp = utc_timestamp().replace("-", "").replace(":", "").replace(".", "")
    digest = sha256(
        canonical_json_sha256(
            {
                "source_profile": source_profile,
                "sections": sections,
                "glossary_terms": glossary_terms,
                "question_ids": question_ids,
            }
        ).encode("utf-8")
    ).hexdigest()[:8]
    return f"ctxsync-{stamp}-{digest}"


def _target_plan_from_result(
    target_project: Project,
    result: ContextPackImportResult,
) -> ContextSyncTargetPlan:
    cfg = target_project.profile_config
    assert cfg is not None
    return ContextSyncTargetPlan(
        profile=target_project.profile or "",
        target_language=cfg.target_language,
        target_locale=cfg.target_locale or cfg.target_language,
        kind=cfg.kind,
        eligible=True,
        changed=result.changed,
        errors=result.errors,
        conflicts=result.conflicts,
        warnings=result.warnings,
        added=result.added,
        updated=result.updated,
        skipped=result.skipped,
        findings=list(result.findings),
    )


def plan_context_sync(
    root: Path,
    *,
    source_profile: str,
    target_profiles: list[str] | None,
    all_compatible: bool,
    sections: set[str],
    terms: list[str],
    question_ids: list[str],
    conflict: PackConflictMode,
    same_locale: bool,
    include_pass_through: bool,
    include_selection: bool,
    allow_not_ready: bool,
    init_missing_context: bool,
) -> ContextSyncPlan:
    """Build the consolidated sync plan without mutating targets."""

    source_project = load_profile_project(root, source_profile)
    source_cfg = source_project.profile_config
    assert source_cfg is not None
    normalized_sections = _normalize_sections(sections)
    normalized_terms = _dedupe_preserve(terms)
    normalized_question_ids = _dedupe_preserve(question_ids)
    target_names = discover_sync_targets(
        root,
        source_profile=source_profile,
        explicit_targets=target_profiles or [],
        all_compatible=all_compatible,
        same_locale=same_locale,
        include_pass_through=include_pass_through,
        include_selection=include_selection,
    )
    if not target_names:
        raise ContextSyncError(
            "sync_no_targets",
            "no compatible target profiles found",
        )
    pack = build_filtered_context_pack(
        source_project,
        sections=set(normalized_sections),
        terms=normalized_terms,
        question_ids=normalized_question_ids,
        allow_not_ready=allow_not_ready,
    )
    source = ContextSyncSource(
        profile=source_profile,
        target_language=source_cfg.target_language,
        target_locale=source_cfg.target_locale or source_cfg.target_language,
        baseline_sha256=pack.source.baseline_sha256,
        context_sha256=pack.source.context_sha256,
    )
    target_plans: list[ContextSyncTargetPlan] = []
    blocked = False
    would_write: list[str] = []

    for target_name in target_names:
        target_project = load_profile_project(root, target_name)
        target_cfg = target_project.profile_config
        assert target_cfg is not None
        if target_cfg.kind == "pass-through" and not include_pass_through:
            raise ContextSyncError(
                "sync_pass_through_excluded",
                f"target profile {target_name} is pass-through; "
                f"pass --include-pass-through to include it",
            )
        try:
            extra_findings = _compatibility_warning(
                source_project, target_project, same_locale=same_locale
            )
            _planned_ctx, result = plan_context_pack_import(
                target_project,
                pack,
                conflict=conflict,
                init_missing_context=init_missing_context,
            )
            if extra_findings:
                result = ContextPackImportResult.from_findings(
                    list(result.findings) + extra_findings,
                    changed=result.changed,
                )
            target_plan = _target_plan_from_result(target_project, result)
        except ContextPackError as exc:
            target_plan = ContextSyncTargetPlan(
                profile=target_name,
                target_language=target_cfg.target_language,
                target_locale=target_cfg.target_locale or target_cfg.target_language,
                kind=target_cfg.kind,
                eligible=True,
                errors=1,
                findings=[
                    ContextPackImportFinding(
                        section="error",
                        key=target_name,
                        action="error",
                        message=str(exc),
                    )
                ],
            )
        target_plans.append(target_plan)
        if target_plan.errors or target_plan.conflicts:
            blocked = True
        if target_plan.changed and not target_plan.errors and not target_plan.conflicts:
            would_write.append(target_name)

    return ContextSyncPlan(
        sync_id=_make_sync_id(
            source_profile,
            sections=normalized_sections,
            glossary_terms=normalized_terms,
            question_ids=normalized_question_ids,
        ),
        source=source,
        sections=normalized_sections,
        glossary_terms=normalized_terms,
        question_ids=normalized_question_ids,
        conflict=conflict,
        write=False,
        allow_not_ready=allow_not_ready,
        init_missing_context=init_missing_context,
        targets=target_plans,
        would_write_profiles=would_write,
        blocked=blocked,
    )


def _record_sync_ledger(
    target_project: Project,
    *,
    plan: ContextSyncPlan,
    pack: SeriesContextPack,
    result: ContextPackImportResult,
    old_baseline: str,
    new_baseline: str,
) -> None:
    ledger = load_context_sync_ledger(target_project)
    if not ledger.profile:
        ledger.profile = target_project.profile or ""
    ledger.entries.append(
        ContextSyncLedgerEntry(
            sync_id=plan.sync_id,
            source_profile=plan.source.profile,
            source_context_sha256=plan.source.context_sha256,
            source_baseline_sha256=plan.source.baseline_sha256,
            pack_sha256=canonical_json_sha256(pack.model_dump(mode="json")),
            sections=list(plan.sections),
            terms=list(plan.glossary_terms),
            question_ids=list(plan.question_ids),
            conflict=plan.conflict,  # type: ignore[arg-type]
            old_baseline_sha256=old_baseline,
            new_baseline_sha256=new_baseline,
            changed=result.changed,
            applied_at=utc_timestamp(),
            findings=[
                ContextSyncLedgerFinding(
                    section=finding.section,
                    key=finding.key,
                    action=finding.action,
                    message=finding.message,
                )
                for finding in result.findings
            ],
        )
    )
    write_context_sync_ledger(target_project, ledger)


def apply_context_sync(plan: ContextSyncPlan, root: Path) -> ContextSyncPlan:
    """Apply a previously built plan after re-running target-local write guards."""

    if plan.blocked:
        raise ContextSyncError(
            "sync_blocked",
            "sync has unresolved conflicts or errors; nothing written",
        )
    pack = build_filtered_context_pack(
        load_profile_project(root, plan.source.profile),
        sections=set(plan.sections),
        terms=list(plan.glossary_terms),
        question_ids=list(plan.question_ids),
        allow_not_ready=plan.allow_not_ready,
    )
    for target in plan.targets:
        if not target.eligible or target.errors or target.conflicts:
            continue
        target_project = load_profile_project(root, target.profile)
        before = load_context(target_project)
        old_baseline = baseline_sha256(before) if before is not None else ""
        _planned_ctx, result = import_context_pack(
            target_project,
            pack,
            conflict=plan.conflict,  # type: ignore[arg-type]
            init_missing_context=plan.init_missing_context,
        )
        after = load_context(target_project)
        new_baseline = baseline_sha256(after) if after is not None else ""
        _record_sync_ledger(
            target_project,
            plan=plan,
            pack=pack,
            result=result,
            old_baseline=old_baseline,
            new_baseline=new_baseline,
        )
    return plan.model_copy(update={"write": True})
