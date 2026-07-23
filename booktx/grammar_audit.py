"""Advisory copy audits for grammar-focused judge tasks."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Literal

from booktx.models import JudgeTask


@dataclass(frozen=True, slots=True)
class GrammarAuditFinding:
    record_id: str
    severity: Literal["info", "warn", "error"]
    rule: str
    message: str
    excerpt: str = ""


_JOINED_SEPARABLE_RE = re.compile(
    r"\b(?:zurück|vor|mit|auf|ein|aus|an|ab|weg)bin\b",
    re.IGNORECASE,
)
_APPOSITION_CASE_RE = re.compile(
    r"\bin\s+[^.!,;]+\bStoff\s+gekleidet,\s+strapazierfähiges\s+Zeug\b",
    re.IGNORECASE,
)
_DANGLING_RE = re.compile(
    r"\bGehemmt\s+von\b[^.]+,\s*(?:würde|konnte|könnte|war|ist)\s+es\b",
    re.IGNORECASE,
)


def audit_text(text: str, record_id: str) -> list[GrammarAuditFinding]:
    findings: list[GrammarAuditFinding] = []
    if _JOINED_SEPARABLE_RE.search(text):
        findings.append(
            GrammarAuditFinding(
                record_id,
                "error",
                "grammar_separable_verb_spacing",
                "suspicious joined separable-verb form; inspect spacing "
                "(for example zurück bin)",
            )
        )
    if _APPOSITION_CASE_RE.search(text):
        findings.append(
            GrammarAuditFinding(
                record_id,
                "error",
                "grammar_apposition_case",
                "apposition may not agree in case with the governed phrase; "
                "inspect adjective ending",
            )
        )
    if _DANGLING_RE.search(text):
        findings.append(
            GrammarAuditFinding(
                record_id,
                "warn",
                "grammar_dangling_participial_phrase",
                "participial phrase may modify an inanimate or unintended subject",
            )
        )
    return findings


def audit_judge_task(task: JudgeTask) -> list[GrammarAuditFinding]:
    findings: list[GrammarAuditFinding] = []
    for record in task.records:
        for candidate in record.candidates[:1]:
            findings.extend(audit_text(candidate.target, record.id))
    return findings


def audit_submitted_decisions(
    task: JudgeTask, submitted: list[object]
) -> list[GrammarAuditFinding]:
    """Audit the actual targets represented by copy/edited decisions."""
    by_id = {getattr(item, "id", ""): item for item in submitted}
    findings: list[GrammarAuditFinding] = []
    for record in task.records:
        item = by_id.get(record.id)
        if item is None:
            continue
        if getattr(item, "decision_kind", "") == "copy":
            selected = next(
                (
                    candidate
                    for candidate in record.candidates
                    if candidate.label == getattr(item, "selected", "")
                ),
                record.candidates[0] if record.candidates else None,
            )
            text = selected.target if selected is not None else ""
        else:
            text = getattr(item, "target", "")
        findings.extend(audit_text(text, record.id))
    return findings


def audit_payload(task: JudgeTask) -> dict[str, object]:
    findings = audit_judge_task(task)
    return {
        "judge_task_id": task.judge_task_id,
        "chapter_id": task.chapter_id,
        "findings": [asdict(finding) for finding in findings],
        "error_count": sum(finding.severity == "error" for finding in findings),
        "warning_count": sum(finding.severity == "warn" for finding in findings),
    }
