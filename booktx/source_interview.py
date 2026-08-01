"""Source-policy interview ledger and card rendering."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from booktx.config import Project
from booktx.context import TranslationContext
from booktx.source_analysis import SourceAnalysisReport, SourceCandidate
from booktx.source_analysis_context import SourceAnalysisDecisions
from booktx.source_candidate_coverage import classify_candidate
from booktx.workflows.termbase import termbase_status_workflow

INTERVIEW_SCHEMA: Literal["booktx.source-interview.v1"] = "booktx.source-interview.v1"

STATUS_VALUES = Literal["queued", "asked", "stored", "ignored", "deferred"]
DECISION_ACTIONS = Literal["answer", "skip"]
BUCKET_PRIORITY = {
    "binding_glossary": 0,
    "name_policy": 1,
    "invented_or_rare": 2,
    "maybe": 3,
    "advisory": 4,
    "no_action": 99,
}


class SourceInterviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    status: STATUS_VALUES = "queued"
    priority: int
    bucket: str
    source_text: str
    selected_record_id: str = ""
    selected_snippet: str = ""
    chosen_target: str | None = None
    storage_refs: list[str] = Field(default_factory=list)
    rationale: str = ""


class SourceInterviewLedger(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["booktx.source-interview.v1"] = Field(
        default=INTERVIEW_SCHEMA, alias="schema"
    )
    profile: str
    source_analysis_sha256: str
    context_fingerprint: str
    # Added without changing the v1 schema name so older ledgers remain
    # readable. New ledgers use this basis for freshness checks.
    basis_fingerprint: str | None = None
    items: list[SourceInterviewItem] = Field(default_factory=list)


class SourceInterviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    action: DECISION_ACTIONS
    target: str | None = None
    forbid: list[str] = Field(default_factory=list)
    storage: Literal["context", "termbase", "both"] = "context"
    disposition: Literal["reviewed", "ignored"] | None = None
    reason: str = ""
    rationale: str = ""


class SourceInterviewDecisions(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["booktx.source-interview-decisions.v1"] = Field(
        default="booktx.source-interview-decisions.v1", alias="schema"
    )
    profile: str
    source_analysis_sha256: str
    basis_fingerprint: str
    decisions: list[SourceInterviewDecision] = Field(default_factory=list)


def source_interview_path(project: Project) -> Path:
    if project.profile_dir is None:
        raise ValueError("source interview ledger requires a profile project")
    return project.profile_dir / "source-interview.json"


def context_fingerprint(context: TranslationContext) -> str:
    """Hash policy inputs only; lifecycle/rendered state is excluded."""
    payload = {
        "glossary": [g.model_dump(mode="json") for g in context.glossary],
        "questions": [q.model_dump(mode="json") for q in context.questions],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def source_interview_basis_fingerprint(
    report: SourceAnalysisReport,
    context: TranslationContext,
    decisions: SourceAnalysisDecisions,
    project: Project,
) -> str:
    """Hash every canonical input that can change interview membership."""
    payload = {
        "source_analysis_sha256": report.analysis_sha256,
        "context": {
            "glossary": [g.model_dump(mode="json") for g in context.glossary],
            "questions": [q.model_dump(mode="json") for q in context.questions],
        },
        "termbase_terms": sorted(_termbase_terms(project)),
        "decisions": decisions.model_dump(mode="json"),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def load_ledger(project: Project) -> SourceInterviewLedger | None:
    path = source_interview_path(project)
    if not path.is_file():
        return None
    return SourceInterviewLedger.model_validate_json(path.read_text("utf-8"))


def write_ledger(project: Project, ledger: SourceInterviewLedger) -> None:
    from booktx.io_utils import write_json_text_atomic

    write_json_text_atomic(
        source_interview_path(project), ledger.model_dump_json(by_alias=True, indent=2)
    )


def _candidate_order_key(
    candidate: SourceCandidate,
) -> tuple[int, float, int, int, str]:
    return (
        BUCKET_PRIORITY.get(candidate.review_bucket, 50),
        -candidate.risk_score,
        -candidate.chapter_frequency,
        -candidate.count,
        candidate.first_record_id or "",
    )


def _context_terms(context: TranslationContext) -> set[str]:
    terms: set[str] = set()
    for entry in context.glossary:
        if entry.status == "approved" or entry.source_analysis_candidate_id:
            terms.add(entry.source.casefold())
            terms.update(v.casefold() for v in entry.source_variants)
    return terms


def _termbase_terms(project: Project) -> set[str]:
    try:
        payload = termbase_status_workflow(
            project.root, profile=project.profile, scope="effective", language=None
        )
    except Exception:
        return set()
    terms: set[str] = set()
    for entry in payload.get("entries", []):
        source = entry.get("source")
        if source:
            terms.add(str(source).casefold())
        terms.update(str(v).casefold() for v in entry.get("source_variants", []))
    return terms


def build_ledger(
    profile: str,
    report: SourceAnalysisReport,
    context: TranslationContext,
    decisions: SourceAnalysisDecisions,
    project: Project,
) -> SourceInterviewLedger:
    ignored = {
        d.candidate_id for d in decisions.dispositions if d.disposition == "ignored"
    }
    reviewed = {
        d.candidate_id for d in decisions.dispositions if d.disposition == "reviewed"
    }
    promoted = {p.candidate_id for p in decisions.promotions}
    covered = _context_terms(context) | _termbase_terms(project)
    items: list[SourceInterviewItem] = []
    for idx, candidate in enumerate(
        sorted(report.candidates, key=_candidate_order_key), start=1
    ):
        if (
            candidate.review_bucket == "no_action"
            or candidate.id in ignored
            or candidate.id in reviewed
            or candidate.id in promoted
        ):
            continue
        if (
            candidate.normalized.casefold() in covered
            or candidate.text.casefold() in covered
        ):
            continue
        occurrence = candidate.examples[0] if candidate.examples else None
        snippet = (
            getattr(occurrence, "snippet", "")
            or getattr(occurrence, "source", "")
            or ""
        )
        record_id = (
            getattr(occurrence, "record_id", None) or candidate.first_record_id or ""
        )
        items.append(
            SourceInterviewItem(
                candidate_id=candidate.id,
                priority=idx,
                bucket=candidate.review_bucket,
                source_text=candidate.text,
                selected_record_id=record_id,
                selected_snippet=snippet,
                storage_refs=[f"context:{profile}", "termbase:project"],
                rationale=candidate.reason,
            )
        )
    return SourceInterviewLedger(
        profile=profile,
        source_analysis_sha256=report.analysis_sha256,
        context_fingerprint=context_fingerprint(context),
        basis_fingerprint=source_interview_basis_fingerprint(
            report, context, decisions, project
        ),
        items=items,
    )


def ledger_is_stale(
    ledger: SourceInterviewLedger,
    report: SourceAnalysisReport,
    context: TranslationContext,
    project: Project | None = None,
    decisions: SourceAnalysisDecisions | None = None,
) -> bool:
    if ledger.source_analysis_sha256 != report.analysis_sha256:
        return True
    if ledger.basis_fingerprint is not None and project is not None:
        current = source_interview_basis_fingerprint(
            report, context, decisions or SourceAnalysisDecisions(), project
        )
        return ledger.basis_fingerprint != current
    # Old v1 ledgers refresh once because their context hash included `ready`.
    return ledger.context_fingerprint != context_fingerprint(context)


def reconcile_ledger(
    previous: SourceInterviewLedger | None,
    current: SourceInterviewLedger,
) -> SourceInterviewLedger:
    """Preserve transient review states for candidates still in the plan."""
    if previous is None:
        return current
    old = {item.candidate_id: item for item in previous.items}
    for item in current.items:
        prior = old.get(item.candidate_id)
        if prior is None:
            continue
        if prior.status in {"asked", "deferred"}:
            item.status = prior.status
        if prior.rationale:
            item.rationale = prior.rationale
    return current


def interview_report_payload(
    ledger: SourceInterviewLedger,
    report: SourceAnalysisReport,
    context: TranslationContext,
    project: Project,
    decisions: SourceAnalysisDecisions,
    *,
    include_snippets: bool = True,
    status: str = "all",
    bucket: str | None = None,
) -> dict[str, Any]:
    """Build a stable public report without exposing ledger implementation keys."""
    ledger_by_id = {item.candidate_id: item for item in ledger.items}
    rows: list[dict[str, Any]] = []
    by_status: dict[str, int] = {}
    by_bucket: dict[str, int] = {}
    by_coverage: dict[str, int] = {}
    for candidate in sorted(report.candidates, key=_candidate_order_key):
        item = ledger_by_id.get(candidate.id)
        item_status = item.status if item else "covered"
        coverage = classify_candidate(candidate, context, project, decisions)
        status_match = (
            status == "all"
            or item_status == status
            or (status == "open" and item_status in {"queued", "asked", "deferred"})
        )
        if not status_match:
            continue
        if bucket is not None and candidate.review_bucket != bucket:
            continue
        by_status[item_status] = by_status.get(item_status, 0) + 1
        by_bucket[candidate.review_bucket] = (
            by_bucket.get(candidate.review_bucket, 0) + 1
        )
        by_coverage[coverage.state] = by_coverage.get(coverage.state, 0) + 1
        occurrence = candidate.examples[0] if candidate.examples else None
        row: dict[str, Any] = {
            "candidate_id": candidate.id,
            "source_text": candidate.text,
            "normalized": candidate.normalized,
            "bucket": candidate.review_bucket,
            "status": item_status,
            "reason": candidate.reason,
            "reason_codes": candidate.reason_codes,
            "risk_score": candidate.risk_score,
            "count": candidate.count,
            "chapter_frequency": candidate.chapter_frequency,
            "selected_record_id": item.selected_record_id
            if item
            else candidate.first_record_id,
            "coverage": {
                "state": coverage.state,
                "confidence": coverage.confidence,
                "evidence": list(coverage.evidence),
                "automatic_disposition": coverage.automatic_disposition,
            },
            "suggested_action": (
                "answer"
                if item is not None and item_status in {"queued", "asked", "deferred"}
                else coverage.automatic_disposition
            ),
            "decision": None,
        }
        if include_snippets:
            row["selected_snippet"] = (
                item.selected_snippet
                if item
                else (occurrence.snippet if occurrence else "")
            )
        rows.append(row)
    return {
        "schema": "booktx.source-interview-report.v1",
        "profile": ledger.profile,
        "source_analysis_sha256": ledger.source_analysis_sha256,
        "basis_fingerprint": ledger.basis_fingerprint,
        "stale": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "candidates": len(rows),
            "open": sum(
                1 for row in rows if row["status"] in {"queued", "asked", "deferred"}
            ),
            "by_status": by_status,
            "by_bucket": by_bucket,
            "by_coverage": by_coverage,
        },
        "items": rows,
    }


def render_interview_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Source interview report",
        "",
        f"Profile: `{payload['profile']}`",
        f"Source-analysis hash: `{payload['source_analysis_sha256']}`",
        f"Basis fingerprint: `{payload.get('basis_fingerprint') or '(legacy)'}`",
        "",
        "## Summary",
        "",
    ]
    summary = payload["summary"]
    lines.extend(
        [
            f"- Candidates: {summary['candidates']}",
            f"- Open decisions: {summary['open']}",
            f"- By status: {summary['by_status']}",
            f"- By bucket: {summary['by_bucket']}",
            f"- By coverage: {summary['by_coverage']}",
            "",
            "## Candidates",
            "",
        ]
    )
    for row in payload["items"]:
        lines.extend(
            [
                f"### {row['candidate_id']} — {row['source_text']}",
                "",
                f"- Bucket: `{row['bucket']}`",
                f"- Status: `{row['status']}`",
                f"- Coverage: `{row['coverage']['state']}` "
                f"({row['coverage']['confidence']})",
                f"- Evidence: {', '.join(row['coverage']['evidence']) or '(none)'}",
                f"- Suggested action: `{row['suggested_action']}`",
                f"- Reason: {row['reason'] or '(none recorded)'}",
            ]
        )
        if "selected_snippet" in row:
            lines.extend(
                [
                    "- Source:",
                    "",
                    f"> {row['selected_snippet'] or '(no snippet recorded)'}",
                ]
            )
        lines.extend(
            [
                "",
                "- Decision: unanswered"
                if row["status"] in {"queued", "asked", "deferred"}
                else "- Decision: resolved",
                "",
            ]
        )
    return "\n".join(lines)


def decision_template(
    ledger: SourceInterviewLedger, report: SourceAnalysisReport
) -> SourceInterviewDecisions:
    return SourceInterviewDecisions(
        profile=ledger.profile,
        source_analysis_sha256=ledger.source_analysis_sha256,
        basis_fingerprint=ledger.basis_fingerprint or "",
        decisions=[
            SourceInterviewDecision(
                candidate_id=item.candidate_id,
                action="skip",
                disposition="reviewed",
                reason="",
            )
            for item in ledger.items
            if item.status in {"queued", "asked", "deferred"}
        ],
    )


def render_card(ledger: SourceInterviewLedger, item: SourceInterviewItem) -> str:
    return "\n".join(
        [
            f"# Source interview: {item.candidate_id}",
            "",
            f"Profile: `{ledger.profile}`",
            f"Bucket: `{item.bucket}`",
            f"Priority: `{item.priority}`",
            f"Source term: `{item.source_text}`",
            f"Record: `{item.selected_record_id}`",
            "",
            "## Evidence",
            item.selected_snippet or "(no snippet recorded)",
            "",
            "## Rationale",
            item.rationale or "(no rationale recorded)",
            "",
            "## Agent task",
            "Ask the user for an approved target policy, then persist it with "
            "`booktx source interview-answer BOOK CAND-... "
            "--profile PROFILE --target TARGET --write` "
            "or skip it with "
            "`booktx source interview-skip BOOK CAND-... "
            "--profile PROFILE --disposition ignored --reason REASON --write`.",
        ]
    )
