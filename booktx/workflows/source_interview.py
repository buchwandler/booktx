"""Workflows for generic source-policy interviews."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from booktx.config import load_profile_project
from booktx.context import load_context
from booktx.errors import _err
from booktx.io_utils import write_json_text_atomic, write_text_atomic
from booktx.source_analysis import read_canonical_report
from booktx.source_analysis_context import (
    SourceAnalysisDecisions,
    load_decisions,
    promote_candidate,
    reconcile_source_analysis_questions,
    set_disposition,
)
from booktx.source_interview import (
    SourceInterviewDecision,
    SourceInterviewDecisions,
    SourceInterviewItem,
    SourceInterviewLedger,
    build_ledger,
    decision_template,
    interview_report_payload,
    ledger_is_stale,
    load_ledger,
    reconcile_ledger,
    render_card,
    render_interview_report,
    write_ledger,
)
from booktx.workflows.termbase import termbase_promote_candidate_workflow

if TYPE_CHECKING:
    from booktx.config import Project
    from booktx.context import TranslationContext
    from booktx.source_analysis import SourceAnalysisReport


@dataclass(frozen=True)
class InterviewPlanResult:
    ledger: SourceInterviewLedger
    written: bool
    path: str


@dataclass(frozen=True)
class InterviewReportResult:
    payload: dict[str, object]
    markdown: str
    report_json: str
    report_markdown: str
    template_path: str
    written: bool


def write_interview_report_output(
    result: InterviewReportResult, output: Path, output_format: str
) -> None:
    """Write a rendered interview report to a caller-selected output path."""
    if output_format in {"markdown", "both"}:
        write_text_atomic(output, result.markdown)
    if output_format == "json":
        write_json_text_atomic(
            output, json.dumps(result.payload, ensure_ascii=False, indent=2)
        )


@dataclass(frozen=True)
class InterviewApplyResult:
    total: int
    promoted: int
    reviewed: int
    ignored: int
    unchanged: int
    open: int
    written: bool


def _load_inputs(
    project: Project,
    profile: str,
) -> tuple[SourceAnalysisReport, Project, TranslationContext]:
    report = read_canonical_report(project)
    if report is None:
        raise _err(
            "source_analysis_missing",
            "no canonical source analysis; run `booktx source analyze BOOK --write`",
        )
    profile_project = load_profile_project(project.root, profile)
    context = load_context(profile_project)
    if context is None:
        raise _err(
            "source_interview_context_missing",
            f"profile {profile!r} has no context; run context init first",
        )
    return report, profile_project, context


def interview_plan(
    project: Project, *, profile: str, write: bool
) -> InterviewPlanResult:
    report, profile_project, context = _load_inputs(project, profile)
    ledger = build_ledger(
        profile, report, context, load_decisions(project), profile_project
    )
    if write:
        write_ledger(profile_project, ledger)
    from booktx.source_interview import source_interview_path

    return InterviewPlanResult(
        ledger=ledger,
        written=write,
        path=str(source_interview_path(profile_project).relative_to(project.root)),
    )


def _report_paths(project: Project) -> tuple[Path, Path, Path]:
    reports = project.root / ".booktx" / "reports"
    return (
        reports / "source-interview.json",
        reports / "source-interview.md",
        reports / "source-interview-decisions.json",
    )


def interview_report(
    project: Project,
    *,
    profile: str,
    write: bool,
    include_snippets: bool = True,
    status: str = "all",
    bucket: str | None = None,
) -> InterviewReportResult:
    report, profile_project, context = _load_inputs(project, profile)
    ledger = load_ledger(profile_project)
    if ledger is None:
        ledger = build_ledger(
            profile, report, context, load_decisions(project), profile_project
        )
    if ledger_is_stale(
        ledger, report, context, profile_project, load_decisions(project)
    ):
        raise _err(
            "source_interview_stale",
            "source interview ledger is stale; regenerate with "
            "`booktx source interview-plan BOOK --profile PROFILE --write`",
        )
    payload = interview_report_payload(
        ledger,
        report,
        context,
        profile_project,
        load_decisions(project),
        include_snippets=include_snippets,
        status=status,
        bucket=bucket,
    )
    markdown = render_interview_report(payload)
    report_json, report_md, template_path = _report_paths(project)
    template = decision_template(ledger, report)
    if write:
        write_json_text_atomic(
            report_json, json.dumps(payload, ensure_ascii=False, indent=2)
        )
        write_text_atomic(report_md, markdown)
        write_json_text_atomic(
            template_path, template.model_dump_json(by_alias=True, indent=2)
        )
    return InterviewReportResult(
        payload=payload,
        markdown=markdown,
        report_json=str(report_json),
        report_markdown=str(report_md),
        template_path=str(template_path),
        written=write,
    )


def _snapshot_tree(paths: list[Path]) -> dict[Path, bytes | None]:
    snapshot: dict[Path, bytes | None] = {}
    for root in paths:
        if not root.exists():
            snapshot[root] = None
            continue
        if root.is_file():
            snapshot[root] = root.read_bytes()
            continue
        for path in root.rglob("*"):
            if path.is_file():
                snapshot[path] = path.read_bytes()
    return snapshot


def _restore_tree(snapshot: dict[Path, bytes | None]) -> None:
    for path, data in snapshot.items():
        if data is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    # No destructive cleanup is needed for canonical files; all writes use
    # atomic replacement and the snapshot restores their previous bytes.


def _decision_already_applied(
    decision: SourceInterviewDecision,
    report: SourceAnalysisReport,
    context: TranslationContext,
    canonical: SourceAnalysisDecisions,
) -> bool:
    if decision.action == "skip":
        return any(
            item.candidate_id == decision.candidate_id
            and item.disposition == decision.disposition
            for item in canonical.dispositions
        )
    return any(
        item.candidate_id == decision.candidate_id for item in canonical.promotions
    ) or any(
        entry.source_analysis_candidate_id == decision.candidate_id
        for entry in context.glossary
    )


def _validate_manifest(
    project: Project,
    profile: str,
    manifest: SourceInterviewDecisions,
    ledger: SourceInterviewLedger,
    report: SourceAnalysisReport,
    context: TranslationContext,
    canonical: SourceAnalysisDecisions,
) -> tuple[int, int]:
    if manifest.profile != profile:
        raise _err(
            "source_interview_manifest_profile",
            "decision manifest profile does not match --profile",
        )
    if manifest.source_analysis_sha256 != report.analysis_sha256:
        raise _err(
            "source_interview_manifest_source",
            "decision manifest source-analysis hash does not match",
        )
    seen: set[str] = set()
    pending = 0
    unchanged = 0
    for decision in manifest.decisions:
        if decision.candidate_id in seen:
            raise _err(
                "source_interview_manifest_duplicate",
                f"duplicate candidate id: {decision.candidate_id}",
            )
        seen.add(decision.candidate_id)
        next(
            (c for c in report.candidates if c.id == decision.candidate_id), None
        ) or _raise_manifest_candidate(decision.candidate_id)
        already = _decision_already_applied(decision, report, context, canonical)
        if (
            decision.candidate_id not in {item.candidate_id for item in ledger.items}
            and not already
        ):
            raise _err(
                "source_interview_manifest_ledger",
                f"candidate is not in the current ledger: {decision.candidate_id}",
            )
        if decision.action == "answer":
            if not decision.target and not decision.forbid:
                raise _err(
                    "source_interview_manifest_answer",
                    f"answer requires target or forbid: {decision.candidate_id}",
                )
        elif decision.disposition not in {"reviewed", "ignored"}:
            raise _err(
                "source_interview_manifest_disposition",
                f"skip requires reviewed or ignored disposition: "
                f"{decision.candidate_id}",
            )
        if already:
            unchanged += 1
        else:
            pending += 1
    if manifest.basis_fingerprint != ledger.basis_fingerprint and pending:
        raise _err(
            "source_interview_manifest_basis",
            "decision manifest basis fingerprint does not match the current ledger",
        )
    return pending, unchanged


def _raise_manifest_candidate(candidate_id: str) -> None:
    raise _err(
        "source_interview_manifest_candidate",
        f"unknown source-analysis candidate: {candidate_id}",
    )


def interview_apply(
    project: Project,
    *,
    profile: str,
    manifest: SourceInterviewDecisions,
    write: bool,
) -> InterviewApplyResult:
    report, profile_project, context = _load_inputs(project, profile)
    ledger = load_ledger(profile_project)
    if ledger is None:
        raise _err(
            "source_interview_missing",
            "no source interview ledger; generate it before applying decisions",
        )
    canonical = load_decisions(project)
    pending, unchanged = _validate_manifest(
        project, profile, manifest, ledger, report, context, canonical
    )
    snapshot = (
        _snapshot_tree([project.booktx_dir, profile_project.profile_dir])
        if write
        else {}
    )
    promoted = reviewed = ignored = 0
    try:
        if write:
            for decision in manifest.decisions:
                if _decision_already_applied(decision, report, context, canonical):
                    continue
                if decision.action == "answer":
                    if decision.storage in {"context", "both"}:
                        promote_candidate(
                            project,
                            report,
                            profile=profile,
                            candidate_id=decision.candidate_id,
                            category=None,
                            target=decision.target,
                            forbidden_targets=decision.forbid,
                            require_target=bool(decision.target),
                            enforce="error"
                            if decision.target or decision.forbid
                            else "warn",
                            as_question=False,
                            promoted_by="source-interview-batch",
                            write=True,
                        )
                    if decision.storage in {"termbase", "both"} and decision.target:
                        termbase_promote_candidate_workflow(
                            project.root,
                            profile=profile,
                            candidate_id=decision.candidate_id,
                            scope="project",
                            preferred=[decision.target],
                            preferred_policy="required",
                            severity="error",
                            approve=True,
                            write=True,
                        )
                    promoted += 1
                else:
                    set_disposition(
                        project,
                        report,
                        candidate_id=decision.candidate_id,
                        disposition=decision.disposition or "reviewed",
                        reason=decision.reason or decision.rationale,
                        decided_by="source-interview-batch",
                        write=True,
                    )
                    if decision.disposition == "ignored":
                        ignored += 1
                    else:
                        reviewed += 1
            refreshed_context = load_context(profile_project) or context
            refreshed = build_ledger(
                profile,
                report,
                refreshed_context,
                load_decisions(project),
                profile_project,
            )
            if reconcile_source_analysis_questions(
                refreshed_context, report, load_decisions(project), refreshed
            ):
                from booktx.context import write_context, write_context_markdown

                write_context(profile_project, refreshed_context)
                write_context_markdown(profile_project, refreshed_context)
                refreshed = build_ledger(
                    profile,
                    report,
                    refreshed_context,
                    load_decisions(project),
                    profile_project,
                )
            write_ledger(profile_project, reconcile_ledger(ledger, refreshed))
        else:
            for decision in manifest.decisions:
                if _decision_already_applied(decision, report, context, canonical):
                    continue
                if decision.action == "answer":
                    promoted += 1
                elif decision.disposition == "ignored":
                    ignored += 1
                else:
                    reviewed += 1
    except Exception:
        if write:
            _restore_tree(snapshot)
        raise
    final_ledger = load_ledger(profile_project) if write else ledger
    open_count = sum(
        1
        for item in (final_ledger.items if final_ledger else ledger.items)
        if item.status in {"queued", "asked", "deferred"}
    )
    return InterviewApplyResult(
        total=len(manifest.decisions),
        promoted=promoted,
        reviewed=reviewed,
        ignored=ignored,
        unchanged=unchanged,
        open=open_count,
        written=write,
    )


def interview_status(
    project: Project, *, profile: str, fail_if_open: bool = False
) -> dict[str, object]:
    report, profile_project, context = _load_inputs(project, profile)
    ledger = load_ledger(profile_project)
    if ledger is None:
        counts = {"queued": 0, "asked": 0, "stored": 0, "ignored": 0, "deferred": 0}
        return {
            "profile": profile,
            "missing": True,
            "stale": False,
            "counts": counts,
            "open": 0,
            "fail": fail_if_open,
        }
    counts = {name: 0 for name in ["queued", "asked", "stored", "ignored", "deferred"]}
    for item in ledger.items:
        counts[item.status] += 1
    open_count = counts["queued"] + counts["asked"] + counts["deferred"]
    stale = ledger_is_stale(
        ledger, report, context, profile_project, load_decisions(project)
    )
    return {
        "profile": profile,
        "missing": False,
        "stale": stale,
        "counts": counts,
        "open": open_count,
        "fail": fail_if_open and open_count > 0,
    }


def _load_fresh_ledger(
    project: Project, profile: str, *, for_write: bool
) -> tuple[SourceInterviewLedger, SourceAnalysisReport, Project, TranslationContext]:
    report, profile_project, context = _load_inputs(project, profile)
    ledger = load_ledger(profile_project)
    if ledger is None:
        raise _err(
            "source_interview_missing",
            "no source interview ledger; run `booktx source interview-plan BOOK "
            "--profile PROFILE --write`",
        )
    if for_write and ledger_is_stale(
        ledger, report, context, profile_project, load_decisions(project)
    ):
        raise _err(
            "source_interview_stale",
            "source interview ledger is stale; regenerate with "
            "`booktx source interview-plan BOOK --profile PROFILE --write`",
        )
    return ledger, report, profile_project, context


def interview_next(
    project: Project, *, profile: str
) -> tuple[SourceInterviewLedger, SourceInterviewItem, str]:
    ledger, _report, _profile_project, _context = _load_fresh_ledger(
        project, profile, for_write=False
    )
    item = next(
        (i for i in ledger.items if i.status in {"queued", "asked", "deferred"}), None
    )
    if item is None:
        raise _err("source_interview_complete", "no open source interview items")
    return ledger, item, render_card(ledger, item)


def _find_item(ledger: SourceInterviewLedger, candidate_id: str) -> SourceInterviewItem:
    item = next((i for i in ledger.items if i.candidate_id == candidate_id), None)
    if item is None:
        raise _err(
            "source_interview_candidate_missing",
            f"candidate is not in the source interview ledger: {candidate_id}",
        )
    return item


def interview_answer(
    project: Project,
    *,
    profile: str,
    candidate_id: str,
    target: str | None,
    forbid: list[str],
    rationale: str,
    storage: Literal["context", "termbase", "both"],
    write: bool,
) -> SourceInterviewItem:
    ledger, report, profile_project, context = _load_fresh_ledger(
        project, profile, for_write=write
    )
    item = _find_item(ledger, candidate_id)
    if write:
        if storage in {"context", "both"}:
            promote_candidate(
                project,
                report,
                profile=profile,
                candidate_id=candidate_id,
                category=None,
                target=target,
                forbidden_targets=forbid,
                require_target=bool(target),
                enforce="error" if target or forbid else "warn",
                as_question=False,
                promoted_by="source-interview",
                write=True,
            )
        if storage in {"termbase", "both"} and target:
            termbase_promote_candidate_workflow(
                project.root,
                profile=profile,
                candidate_id=candidate_id,
                scope="project",
                preferred=[target],
                preferred_policy="required",
                severity="error",
                approve=True,
                write=True,
            )
        item.status = "stored"
        item.chosen_target = target
        item.rationale = rationale or item.rationale
        refreshed = build_ledger(
            profile,
            report,
            load_context(profile_project) or context,
            load_decisions(project),
            profile_project,
        )
        write_ledger(profile_project, reconcile_ledger(ledger, refreshed))
    return item


def interview_skip(
    project: Project,
    *,
    profile: str,
    candidate_id: str,
    disposition: Literal["ignored", "reviewed", "deferred"],
    reason: str,
    write: bool,
) -> SourceInterviewItem:
    ledger, report, profile_project, context = _load_fresh_ledger(
        project, profile, for_write=write
    )
    item = _find_item(ledger, candidate_id)
    if write:
        if disposition in {"ignored", "reviewed"}:
            set_disposition(
                project,
                report,
                candidate_id=candidate_id,
                disposition=disposition,
                reason=reason,
                decided_by="source-interview",
                write=True,
            )
            item.status = "ignored" if disposition == "ignored" else "stored"
        else:
            item.status = "deferred"
        item.rationale = reason or item.rationale
        refreshed = build_ledger(
            profile,
            report,
            load_context(profile_project) or context,
            load_decisions(project),
            profile_project,
        )
        write_ledger(profile_project, reconcile_ledger(ledger, refreshed))
    return item
