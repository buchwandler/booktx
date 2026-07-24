"""Workflows for generic source-policy interviews."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from booktx.config import load_profile_project
from booktx.context import load_context
from booktx.errors import _err
from booktx.source_analysis import read_canonical_report
from booktx.source_analysis_context import (
    load_decisions,
    promote_candidate,
    set_disposition,
)
from booktx.source_interview import (
    SourceInterviewItem,
    SourceInterviewLedger,
    build_ledger,
    ledger_is_stale,
    load_ledger,
    render_card,
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
    stale = ledger_is_stale(ledger, report, context)
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
    if for_write and ledger_is_stale(ledger, report, context):
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
    ledger, report, profile_project, _context = _load_fresh_ledger(
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
        write_ledger(profile_project, ledger)
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
    ledger, report, profile_project, _context = _load_fresh_ledger(
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
        write_ledger(profile_project, ledger)
    return item
