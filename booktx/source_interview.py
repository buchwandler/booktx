"""Source-policy interview ledger and card rendering."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from booktx.config import Project
from booktx.context import TranslationContext
from booktx.source_analysis import SourceAnalysisReport, SourceCandidate
from booktx.source_analysis_context import SourceAnalysisDecisions
from booktx.workflows.termbase import termbase_status_workflow

INTERVIEW_SCHEMA: Literal["booktx.source-interview.v1"] = "booktx.source-interview.v1"

STATUS_VALUES = Literal["queued", "asked", "stored", "ignored", "deferred"]
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
    items: list[SourceInterviewItem] = Field(default_factory=list)


def source_interview_path(project: Project):
    if project.profile_dir is None:
        raise ValueError("source interview ledger requires a profile project")
    return project.profile_dir / "source-interview.json"


def context_fingerprint(context: TranslationContext) -> str:
    payload = {
        "glossary": [g.model_dump(mode="json") for g in context.glossary],
        "questions": [q.model_dump(mode="json") for q in context.questions],
        "ready": context.ready,
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
            project.root, profile=project.profile_name, scope="effective", language=None
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
        items=items,
    )


def ledger_is_stale(
    ledger: SourceInterviewLedger,
    report: SourceAnalysisReport,
    context: TranslationContext,
) -> bool:
    return (
        ledger.source_analysis_sha256 != report.analysis_sha256
        or ledger.context_fingerprint != context_fingerprint(context)
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
