"""Acceptance logic for judge submissions into selection profiles."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

# ruff: noqa: E501
from booktx.acceptance import SubmissionValidationError
from booktx.config import (
    Project,
    _err,
    current_source_sha256,
    load_profile_project,
    load_translation_selection_ledger,
    load_translation_store,
    load_translation_version_ledger,
    write_translation_selection_ledger,
)
from booktx.context import TranslationContext
from booktx.errors import BooktxError
from booktx.glossary_audit import evaluate_glossary_entries
from booktx.glossary_diagnostics import (
    format_glossary_missing_message,
    source_phrase_window,
)
from booktx.glossary_match import (
    target_terms,
)
from booktx.models import (
    JudgeCandidateEvidence,
    JudgeDecision,
    JudgeTask,
    JudgeTaskCandidate,
    JudgeTaskRecord,
    Record,
    TranslatedRecord,
)
from booktx.selection_mode import revision_focus, selection_purpose
from booktx.termbase_tasking import (
    applicable_termbase_sha256_for_record_sources,
    validate_termbase_record_pair,
)
from booktx.translation_store import (
    EffectiveCandidateError,
    effective_candidate_selection,
    ensure_store_record,
    sha256_text,
    upsert_translation_version,
)
from booktx.validate import (
    Finding,
    Severity,
    load_validation_context,
    validate_record_pair,
)
from booktx.versioning import canonical_json_sha256, lookup_version, resolve_identity

if TYPE_CHECKING:
    from booktx.models import Chunk, TranslationStoreV2
    from booktx.progress import SourceRecordView
    from booktx.status import StatusBundle
    from booktx.validate import Finding

__all__ = [
    "SubmittedJudgeRecord",
    "JudgeInsertResult",
    "parse_judge_block_submission",
    "parse_judge_decisions_submission",
    "parse_judge_json_submission",
    "lint_judge_submission",
    "accept_judge_submission",
]

_BLOCK_HEADER_RE = re.compile(r"^##\s+(?P<id>\S+)\s*$")
_BOUNDARY_CORRUPTION_RE = re.compile(r"(?<!\n)##\s+\d{4}-\d{6}")


@dataclass(slots=True)
class SubmittedJudgeRecord:
    id: str
    selected: str
    decision_kind: str
    target: str
    reason: str = ""


@dataclass(slots=True)
class JudgeInsertResult:
    accepted_records: int
    version_refs: list[str]
    record_findings: list[Finding] = field(default_factory=list)


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _grammar_sentence_count(text: str) -> int:
    return len(re.findall(r"[^.!?]+(?:[.!?]+|$)", text.strip())) if text.strip() else 0


def _validate_grammar_scope(
    item: SubmittedJudgeRecord, base_target: str, target: str
) -> None:
    """Reject non-minimal or structurally incompatible grammar edits."""
    if item.decision_kind != "edited":
        return
    if _grammar_sentence_count(base_target) != _grammar_sentence_count(target):
        raise _err(
            "judge_grammar_sentence_count", f"record {item.id} changed sentence count"
        )
    if re.findall(r"<[^>]+>", base_target) != re.findall(r"<[^>]+>", target):
        raise _err(
            "judge_grammar_structure",
            f"record {item.id} changed inline XHTML structure",
        )
    similarity = SequenceMatcher(None, base_target, target).ratio()
    if max(len(base_target.strip()), len(target.strip())) >= 40 and similarity < 0.72:
        raise _err(
            "judge_grammar_nonminimal",
            f"record {item.id} grammar edit is too large (similarity {similarity:.2f})",
        )


def parse_judge_json_submission(
    text: str,
) -> tuple[str | None, list[SubmittedJudgeRecord]]:
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise _err("judge_submission_json", "judge JSON submission must be an object")
    task_id = payload.get("judge_task_id")
    records_raw = payload.get("records")
    if not isinstance(records_raw, list):
        raise _err(
            "judge_submission_json",
            "judge JSON submission must contain a records array",
        )
    records: list[SubmittedJudgeRecord] = []
    for item in records_raw:
        if not isinstance(item, dict):
            raise _err("judge_submission_json", "each judge record must be an object")
        records.append(
            SubmittedJudgeRecord(
                id=str(item.get("id") or "").strip(),
                selected=str(item.get("selected") or "").strip(),
                decision_kind=str(item.get("decision_kind") or "").strip(),
                target=str(item.get("target") or ""),
                reason=str(item.get("reason") or ""),
            )
        )
    return (str(task_id).strip() if task_id else None, records)


def parse_judge_block_submission(
    text: str,
) -> tuple[str | None, list[SubmittedJudgeRecord]]:
    task_id: str | None = None
    records: list[SubmittedJudgeRecord] = []
    current_id: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_id, current_lines
        if current_id is None:
            return
        decision: dict[str, str] = {}
        target_lines: list[str] = []
        section = ""
        for raw in current_lines:
            stripped = raw.strip()
            if stripped == "DECISION:":
                section = "decision"
                continue
            if stripped == "TARGET:":
                section = "target"
                continue
            if section == "decision":
                if ":" in raw:
                    key, value = raw.split(":", 1)
                    decision[key.strip().lower()] = value.strip()
            elif section == "target":
                target_lines.append(raw)
        target = "\n".join(target_lines).strip("\n")
        records.append(
            SubmittedJudgeRecord(
                id=current_id,
                selected=decision.get("selected", ""),
                decision_kind=decision.get("decision_kind", ""),
                target=target,
                reason=decision.get("reason", ""),
            )
        )
        current_id = None
        current_lines = []

    for raw in text.splitlines():
        header = _BLOCK_HEADER_RE.match(raw)
        if header:
            flush()
            current_id = header.group("id")
            continue
        if current_id is None:
            if raw.startswith("judge_task_id:"):
                task_id = raw.split(":", 1)[1].strip() or None
            continue
        current_lines.append(raw)
    flush()
    if not records:
        raise _err(
            "judge_submission_block",
            "judge block submission did not contain any records",
        )
    return task_id, records


_COMPACT_DECISION_RE = re.compile(
    r"^([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)(?:\s*\|\s*(.*))?$"
)


def _parse_compact_grammar_decisions(
    text: str,
) -> tuple[str | None, list[SubmittedJudgeRecord]]:
    task_id: str | None = None
    records: list[SubmittedJudgeRecord] = []
    current: SubmittedJudgeRecord | None = None
    target_lines: list[str] = []
    in_target = False

    def flush() -> None:
        nonlocal current, target_lines, in_target
        if current is not None:
            records.append(
                SubmittedJudgeRecord(
                    id=current.id,
                    selected=current.selected,
                    decision_kind=current.decision_kind,
                    target="\n".join(target_lines).strip("\n"),
                    reason=current.reason,
                )
            )
        current = None
        target_lines = []
        in_target = False

    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("judge_task_id:"):
            task_id = stripped.split(":", 1)[1].strip() or None
            continue
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "TARGET:" or stripped.startswith("TARGET:"):
            in_target = True
            inline = stripped.split(":", 1)[1].lstrip()
            if inline:
                target_lines.append(inline)
            continue
        if stripped == "END_TARGET":
            in_target = False
            continue
        if in_target:
            target_lines.append(raw)
            continue
        match = _COMPACT_DECISION_RE.match(stripped)
        if match:
            flush()
            record_id, kind, selected, reason = match.groups()
            current = SubmittedJudgeRecord(
                id=record_id.strip(),
                selected=selected.strip(),
                decision_kind=kind.strip(),
                target="",
                reason=(reason or "").strip(),
            )
    flush()
    if not records:
        raise _err(
            "judge_submission_decisions",
            "compact grammar decisions submission did not contain any records; "
            "use RECORD_ID | copy|edited | A|B|C|edited",
        )
    return task_id, records


def parse_judge_decisions_submission(
    text: str,
) -> tuple[str | None, list[SubmittedJudgeRecord]]:
    if "format: grammar-decisions-v2" in text:
        return _parse_compact_grammar_decisions(text)
    task_id: str | None = None
    records: list[SubmittedJudgeRecord] = []
    current_id: str | None = None
    decision: dict[str, str] = {}
    target_lines: list[str] = []
    in_target = False

    def flush() -> None:
        nonlocal current_id, decision, target_lines, in_target
        if current_id is None:
            return
        records.append(
            SubmittedJudgeRecord(
                id=current_id,
                selected=decision.get("selected", ""),
                decision_kind=decision.get("decision_kind", ""),
                target="\n".join(target_lines).strip("\n"),
                reason=decision.get("reason", ""),
            )
        )
        current_id = None
        decision = {}
        target_lines = []
        in_target = False

    for raw in text.splitlines():
        header = _BLOCK_HEADER_RE.match(raw)
        if header:
            flush()
            current_id = header.group("id")
            continue
        if current_id is None:
            if raw.startswith("judge_task_id:"):
                task_id = raw.split(":", 1)[1].strip() or None
            continue
        if raw.strip() == "END_TARGET":
            in_target = False
            continue
        if raw.strip().startswith("TARGET:"):
            in_target = True
            inline = raw.split(":", 1)[1].lstrip()
            if inline:
                target_lines.append(inline)
            continue
        if in_target:
            target_lines.append(raw)
            continue
        if ":" in raw:
            key, value = raw.split(":", 1)
            decision[key.strip().lower()] = value.strip()
    flush()
    if not records:
        raise _err(
            "judge_submission_decisions",
            "judge decisions submission did not contain any records",
        )
    return task_id, records


def _error_findings(findings: list[Finding]) -> list[Finding]:
    blocking_rules = {"glossary_target_missing", "forbidden_term_used"}
    return [
        finding
        for finding in findings
        if finding.severity == Severity.ERROR or finding.rule in blocking_rules
    ]


def _rough_sentence_count(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    parts = [
        item.strip()
        for item in re.split(r'(?<=[.!?])(?:["»”\')\]]+)?\s+', stripped)
        if item.strip()
    ]
    return len(parts) if parts else 1


def _grammar_revision_warning_findings(
    *,
    item: SubmittedJudgeRecord,
    task_record: JudgeTaskRecord,
    target_text: str,
) -> list[Finding]:
    if item.decision_kind != "edited" or not task_record.candidates:
        return []
    base_target = task_record.candidates[0].target
    if _normalize_newlines(base_target) == _normalize_newlines(target_text):
        return []
    findings: list[Finding] = []
    before_sentences = _rough_sentence_count(base_target)
    after_sentences = _rough_sentence_count(target_text)
    if before_sentences != after_sentences:
        findings.append(
            Finding(
                chunk_id=task_record.chunk_id,
                severity=Severity.WARN,
                rule="grammar_revision_sentence_count_changed",
                message=(
                    "grammar-focused revision changed the sentence count from "
                    f"{before_sentences} to {after_sentences}; verify this was a "
                    "minimal grammatical repair"
                ),
                record_id=item.id,
            )
        )
    similarity = SequenceMatcher(
        None,
        _normalize_newlines(base_target),
        _normalize_newlines(target_text),
    ).ratio()
    if (
        max(len(base_target.strip()), len(target_text.strip())) >= 40
        and similarity < 0.72
    ):
        findings.append(
            Finding(
                chunk_id=task_record.chunk_id,
                severity=Severity.WARN,
                rule="grammar_revision_large_edit",
                message=(
                    "grammar-focused revision made a large edit to BASE_TARGET; "
                    "verify wording, terminology, and meaning stayed frozen"
                ),
                record_id=item.id,
            )
        )
    return findings


def _binding_glossary_findings(
    source_record: Record,
    *,
    target_text: str,
    chunk_id: str,
    context: TranslationContext | None,
) -> list[Finding]:
    if context is None:
        return []
    from booktx.validate import Finding

    findings: list[Finding] = []
    evaluations_by_entry = evaluate_glossary_entries(
        glossary=context.glossary,
        source_text=source_record.source,
        target=target_text,
    )

    for idx, entry in enumerate(context.glossary):
        evaluations = evaluations_by_entry.get(idx, [])
        if entry.enforce == "off" or not evaluations:
            continue
        severity = Severity.ERROR if entry.enforce == "error" else Severity.WARN
        for evaluation in evaluations:
            for forbidden in evaluation.evaluation.forbidden_target_found:
                findings.append(
                    Finding(
                        chunk_id=chunk_id,
                        severity=severity,
                        rule="forbidden_term_used",
                        message=f"{entry.source} must not be translated as {forbidden}",
                        record_id=source_record.id,
                    )
                )
        if entry.require_target:
            approved = target_terms(entry)
            missing = [
                item
                for item in evaluations
                if not (
                    item.evaluation.required_target_found
                    or item.evaluation.allowed_target_found
                )
            ]
            if approved and missing:
                matched = missing[0].matched_span
                if matched is not None:
                    phrase_excerpt = source_phrase_window(
                        source_record.source, matched.start, matched.end
                    )
                    message = format_glossary_missing_message(
                        entry=entry,
                        approved=approved,
                        matched=matched,
                        phrase_excerpt=phrase_excerpt,
                        source=source_record.source,
                        target=target_text,
                        glossary=context.glossary,
                    )
                else:
                    message = (
                        f"{entry.source} must be translated using an approved target "
                        f"({' / '.join(approved)})"
                    )
                findings.append(
                    Finding(
                        chunk_id=chunk_id,
                        severity=severity,
                        rule="glossary_target_missing",
                        message=message,
                        record_id=source_record.id,
                        source=source_record.source,
                        target=target_text,
                    )
                )
    return findings


def _validate_task_profile(project: Project, task: JudgeTask) -> None:
    selected = project.profile or ""
    if task.profile and task.profile != selected:
        raise _err(
            "judge_task_profile_mismatch",
            f"judge task {task.judge_task_id} belongs to profile {task.profile}, "
            f"but selected profile is {selected or '<none>'}",
        )
    if project.profile_config is None or project.profile_config.kind != "selection":
        raise _err("judge_profile_kind", "judge workflows require a selection profile")


def _validate_task_evidence(project: Project, task: JudgeTask) -> None:
    if task.source_sha256 and task.source_sha256 != current_source_sha256(project):
        raise _err(
            "judge_source_drift",
            f"project source changed since judge task {task.judge_task_id} was created",
        )
    if task.profile_config_sha256 is not None and project.profile_config is not None:
        actual = canonical_json_sha256(project.profile_config.model_dump(mode="json"))
        if actual != task.profile_config_sha256:
            raise _err(
                "judge_profile_config_drift",
                f"profile config changed since judge task {task.judge_task_id} "
                "was created",
            )
    if task.source_config_sha256 is not None:
        actual = canonical_json_sha256(project.source_config.model_dump(mode="json"))
        if actual != task.source_config_sha256:
            raise _err(
                "judge_source_config_drift",
                f"source config changed since judge task {task.judge_task_id} "
                "was created",
            )
    if task.applicable_termbase_sha256 is not None:
        record_sources = {record.id: record.source for record in task.records}
        if (
            applicable_termbase_sha256_for_record_sources(project, record_sources)
            != task.applicable_termbase_sha256
        ):
            raise _err(
                "task_context_policy_stale",
                "judge task context predates applicable termbase changes; "
                "recreate the task",
            )


def _candidate_for_label(
    task_record: JudgeTaskRecord, label: str
) -> JudgeTaskCandidate | None:
    for candidate in task_record.candidates:
        if candidate.label == label:
            return candidate
    return None


def _candidate_evidence(candidate: JudgeTaskCandidate) -> JudgeCandidateEvidence:
    status = "ok"
    if any(f.severity == "error" for f in candidate.validation_findings):
        status = "error"
    elif any(f.severity == "warn" for f in candidate.validation_findings):
        status = "warning"
    return JudgeCandidateEvidence(
        label=candidate.label,
        profile=candidate.profile,
        selected_kind=candidate.selected_kind,
        selected_ref=candidate.selected_ref,
        version_ref=candidate.version_ref,
        review_ref=candidate.review_ref,
        target_sha256=candidate.target_sha256,
        validation_status=status,  # type: ignore[arg-type]
        findings=[
            f"{finding.severity}:{finding.rule}:{finding.message}"
            for finding in candidate.validation_findings
        ],
    )


def _validate_edited_targets_allowed(
    project: Project, item: SubmittedJudgeRecord
) -> None:
    if item.decision_kind != "edited":
        return
    cfg = project.profile_config
    selection_cfg = cfg.selection if cfg is not None else None
    allow_edited = (
        selection_cfg.allow_edited_targets if selection_cfg is not None else True
    )
    if not allow_edited:
        raise _err(
            "judge_edited_disabled",
            f"record {item.id} edited judge targets are disabled for this profile",
        )


def _bind_task_source_access(
    project: Project, task: JudgeTask, *, enforce_snapshot: bool
) -> None:
    """Bind runtime/task source access before candidate processing.

    In profile-root mode (``enforce_snapshot``) only snapshot tasks are
    accepted; live/legacy tasks are rejected before any sibling profile is
    loaded. Snapshot tasks must carry complete evidence, an uncorrupted
    candidate payload, and a current manifest hash; live tasks keep the
    existing sibling drift checks (applied per record below).
    """
    from booktx.config import JUDGE_SOURCES_SNAPSHOT_MANIFEST_REL
    from booktx.judge_sources import (
        judge_sources_manifest_sha256,
        judge_task_candidates_sha256,
    )

    if enforce_snapshot and task.source_access != "snapshot":
        raise _err(
            "judge_snapshot_task_required",
            "profile-root judge insert requires a snapshot task; "
            "recreate the task with `booktx judge next .` from the profile root",
        )
    if task.source_access != "snapshot":
        return

    problems: list[str] = []
    if not task.source_snapshot_sha256:
        problems.append("source_snapshot_sha256")
    if task.source_snapshot_path != JUDGE_SOURCES_SNAPSHOT_MANIFEST_REL:
        problems.append("source_snapshot_path")
    if not task.source_candidates_sha256:
        problems.append("source_candidates_sha256")
    if problems:
        raise _err(
            "judge_snapshot_evidence_incomplete",
            "snapshot judge task is missing or has invalid evidence "
            f"({', '.join(problems)}); recreate the task",
        )
    if judge_task_candidates_sha256(task.records) != task.source_candidates_sha256:
        raise _err(
            "judge_task_candidate_corrupt",
            f"judge task {task.judge_task_id} candidate payload hash does not match; "
            "the task artifact was corrupted or edited",
        )
    try:
        current_manifest_sha = judge_sources_manifest_sha256(project)
    except BooktxError:
        current_manifest_sha = ""
    if current_manifest_sha != task.source_snapshot_sha256:
        raise _err(
            "judge_source_snapshot_drift",
            "judge source snapshot changed since this task was created; recreate the task",
        )


def _validate_live_candidate_has_not_drifted(
    project: Project,
    task: JudgeTask,
    item: SubmittedJudgeRecord,
    selected_candidate: JudgeTaskCandidate,
) -> None:
    """Collaborative-mode drift check: reload the live source profile.

    Used only for ``source_access == "live"`` tasks. Snapshot tasks trust
    their immutable payload and never call this.
    """
    source_project = load_profile_project(project.root, selected_candidate.profile)
    source_stored = load_translation_store(source_project).records.get(item.id)
    if source_stored is None:
        raise _err(
            "judge_candidate_missing",
            f"source profile {selected_candidate.profile} "
            f"no longer has record {item.id}",
        )
    selection = effective_candidate_selection(source_stored, strict_active_review=True)
    if isinstance(selection, EffectiveCandidateError) or selection is None:
        raise _err(
            "judge_candidate_drift",
            f"source profile {selected_candidate.profile} no longer has "
            f"the selected effective candidate for record {item.id}",
        )
    if selection.selected_ref != selected_candidate.selected_ref:
        raise _err(
            "judge_candidate_drift",
            f"record {item.id} selected candidate ref changed from "
            f"{selected_candidate.selected_ref} to {selection.selected_ref}",
        )
    live_target_sha = sha256_text(selection.candidate.target)
    if live_target_sha != selected_candidate.target_sha256:
        raise _err(
            "judge_candidate_hash_drift",
            f"record {item.id} selected candidate content changed "
            f"since judge task {task.judge_task_id} was created",
        )


def _validate_and_resolve_target(
    item: SubmittedJudgeRecord,
    project: Project,
    task: JudgeTask,
    task_records: dict[str, JudgeTaskRecord],
    seen_ids: set[str],
    source_by_id: dict[str, SourceRecordView],
    source_chunks: dict[str, Chunk],
    validation_context: TranslationContext,
    input_format: str,
) -> tuple[str, list[Finding]]:
    """Validate one judge decision and resolve the target text."""
    if item.id in seen_ids:
        raise _err("duplicate_record_id", f"duplicate judge record id: {item.id}")
    seen_ids.add(item.id)
    task_record = task_records.get(item.id)
    if task_record is None:
        raise _err(
            "record_not_in_task",
            f"record {item.id} is not part of judge task {task.judge_task_id}",
        )
    if item.decision_kind not in {"copy", "edited"}:
        raise _err(
            "judge_decision_kind",
            f"record {item.id} decision_kind must be copy or edited",
        )
    _validate_edited_targets_allowed(project, item)
    selected_candidate: JudgeTaskCandidate | None = None
    if item.selected and item.selected != "edited":
        selected_candidate = _candidate_for_label(task_record, item.selected)
        if selected_candidate is None:
            raise _err(
                "judge_selected_label",
                f"record {item.id} selected label {item.selected!r} ",
                f"is not present in judge task {task.judge_task_id}",
            )
    elif item.decision_kind == "copy":
        raise _err(
            "judge_selected_label",
            f"record {item.id} copy decisions require a candidate label",
        )
    if _BOUNDARY_CORRUPTION_RE.search(item.target):
        raise _err(
            "judge_block_boundary_corrupt",
            f"record {item.id} TARGET appears to contain the next record header; ",
            "reset the ingest file with ",
            f"`booktx judge reset-ingest . --judge-task-id {task.judge_task_id} ",
            f"--format {input_format} --write`",
        )
    raw_target = item.target.strip("\n")

    if selected_candidate is not None:
        candidate_blocks_copy = any(
            finding.rule in {"glossary_target_missing", "forbidden_term_used"}
            or (finding.severity == "error" and finding.rule.startswith("termbase."))
            for finding in selected_candidate.validation_findings
        )
        if item.decision_kind == "copy" and candidate_blocks_copy:
            raise _err(
                "judge_candidate_validation",
                f"record {item.id} selected candidate {selected_candidate.label} ",
                "violates the selection profile glossary or termbase policy ",
                "and cannot be copied unchanged",
            )
        # Self-check: the candidate target hash must match its own payload.
        # This detects a corrupted/edited task artifact for any access mode.
        if sha256_text(selected_candidate.target) != selected_candidate.target_sha256:
            raise _err(
                "judge_task_candidate_corrupt",
                f"record {item.id} candidate {selected_candidate.label} hash ",
                "does not match the task payload",
            )
        # Live tasks re-check the sibling source profile for drift. Snapshot
        # tasks trust their immutable, hash-verified payload and never read
        # sibling profiles.
        if task.source_access == "live":
            _validate_live_candidate_has_not_drifted(
                project, task, item, selected_candidate
            )
    if item.decision_kind == "copy":
        if selected_candidate is None:
            raise _err(
                "judge_selected_label",
                f"record {item.id} copy decisions require a candidate label",
            )
        if not raw_target.strip():
            target_text = selected_candidate.target
        else:
            target_text = raw_target
            if _normalize_newlines(target_text) != _normalize_newlines(
                selected_candidate.target
            ):
                raise _err(
                    "judge_copy_target_mismatch",
                    f"record {item.id} copy target must exactly match ",
                    f"selected candidate {selected_candidate.label}",
                )
    else:
        target_text = raw_target
        if not target_text.strip():
            raise _err(
                "judge_empty_target",
                f"record {item.id} edited target must not be empty",
            )

    if item.id not in source_by_id:
        raise _err("unknown_record_id", f"unknown source record id: {item.id}")
    source_view = source_by_id[item.id]
    source_chunk = source_chunks[source_view.chunk_id]
    source_record = next(
        record for record in source_chunk.records if record.id == item.id
    )
    findings: list[Finding] = list(
        validate_record_pair(
            source_record,
            TranslatedRecord(id=item.id, target=target_text),
            source_chunk.chunk_id,
            validation_context,
        )
    )
    findings.extend(
        _binding_glossary_findings(
            source_record,
            target_text=target_text,
            chunk_id=source_chunk.chunk_id,
            context=validation_context,
        )
    )
    if task_record.applicable_termbase:
        findings.extend(
            validate_termbase_record_pair(
                source_text=source_record.source,
                target_text=target_text,
                snapshots=task_record.applicable_termbase,
                chunk_id=source_chunk.chunk_id,
                record_id=source_record.id,
            )
        )
    if (
        task.selection_purpose == "revise"
        and task.revision_focus == "grammar"
        and selected_candidate is not None
    ):
        _validate_grammar_scope(item, selected_candidate.target, target_text)
    return target_text, findings


def lint_judge_submission(
    project: Project,
    task: JudgeTask,
    submitted: list[SubmittedJudgeRecord],
    *,
    bundle: StatusBundle,
    enforce_snapshot: bool = False,
    input_format: str = "decisions",
) -> JudgeInsertResult:
    """Run insert-equivalent validation without mutating store or ledger."""
    if not submitted:
        raise _err("empty_submission", "no judge decisions to lint")
    _validate_task_profile(project, task)
    _validate_task_evidence(project, task)
    _bind_task_source_access(project, task, enforce_snapshot=enforce_snapshot)
    if selection_purpose(project) == "revise" and task.selection_purpose != "revise":
        raise _err(
            "judge_revision_task_purpose",
            "revision profiles require a revision judge task",
        )
    if selection_purpose(project) == "revise":
        missing = sorted(
            {record.id for record in task.records} - {item.id for item in submitted}
        )
        if missing:
            raise _err(
                "judge_revision_incomplete_task",
                "revision judge tasks require a decision for every record; missing: "
                + ", ".join(missing[:10]),
            )
    task_records = {record.id: record for record in task.records}
    validation_context = load_validation_context(
        project, context_view_path=task.context_view_path
    )
    if validation_context is None:
        raise _err("judge_context_missing", "judge task validation context is missing")
    findings: list[Finding] = []
    seen_ids: set[str] = set()
    for item in submitted:
        _target, item_findings = _validate_and_resolve_target(
            item=item,
            project=project,
            task=task,
            task_records=task_records,
            seen_ids=seen_ids,
            source_by_id=bundle.index.source_by_id,
            source_chunks=bundle.index.source_chunks,
            validation_context=validation_context,
            input_format=input_format,
        )
        findings.extend(item_findings)
    errors = _error_findings(findings)
    if errors:
        raise SubmissionValidationError(errors)
    return JudgeInsertResult(
        accepted_records=len(submitted),
        version_refs=[],
        record_findings=findings,
    )


def accept_judge_submission(
    project: Project,
    task: JudgeTask,
    submitted: list[SubmittedJudgeRecord],
    *,
    bundle: StatusBundle,
    enforce_snapshot: bool = False,
    input_format: str = "block",
) -> JudgeInsertResult:
    if not submitted:
        raise _err("empty_submission", "no judge decisions to accept")

    _validate_task_profile(project, task)
    if (
        selection_purpose(project) == "revise"
        and task.selection_purpose == "revise"
        and task.revision_focus != revision_focus(project)
    ):
        raise _err(
            "judge_revision_focus_mismatch",
            "revision focus changed after task creation; recreate the task",
        )
    _validate_task_evidence(project, task)
    _bind_task_source_access(project, task, enforce_snapshot=enforce_snapshot)

    _validate_task_profile(project, task)
    _validate_task_evidence(project, task)

    # Revise profiles require a revise-purpose task and a decision for every
    # record. Completeness is checked before any store or ledger write so a
    # failed submission is non-mutating.
    if selection_purpose(project) == "revise":
        if task.selection_purpose != "revise":
            raise _err(
                "judge_revision_task_purpose",
                "revision profiles require a revision judge task; recreate the task",
            )
        submitted_ids = {item.id for item in submitted}
        task_ids = {record.id for record in task.records}
        missing = sorted(task_ids - submitted_ids)
        if missing:
            preview = ", ".join(missing[:10])
            if len(missing) > 10:
                preview += " ..."
            raise _err(
                "judge_revision_incomplete_task",
                "revision judge tasks require a decision for every record; missing: ",
                preview,
            )

    task_records = {record.id: record for record in task.records}
    source_by_id = bundle.index.source_by_id
    source_chunks = bundle.index.source_chunks
    validation_context = load_validation_context(
        project,
        context_view_path=task.context_view_path,
    )
    if validation_context is None:
        raise _err(
            "judge_context_missing",
            "judge task validation context is missing; recreate the task",
        )

    findings: list[Finding] = []
    ledger = load_translation_selection_ledger(project)
    version_ledger = load_translation_version_ledger(project)
    from booktx.io_utils import utc_timestamp
    from booktx.store import StoreFormat, open_translation_store

    timestamp = utc_timestamp()
    accepted_versions: list[str] = []
    seen_ids: set[str] = set()
    resolved_target_by_id: dict[str, str] = {}

    for item in submitted:
        target_text, item_findings = _validate_and_resolve_target(
            item=item,
            project=project,
            task=task,
            task_records=task_records,
            seen_ids=seen_ids,
            source_by_id=source_by_id,
            source_chunks=source_chunks,
            validation_context=validation_context,
            input_format=input_format,
        )
        resolved_target_by_id[item.id] = target_text
        if task.selection_purpose == "revise" and task.revision_focus == "grammar":
            item_findings.extend(
                _grammar_revision_warning_findings(
                    item=item,
                    task_record=task_records[item.id],
                    target_text=target_text,
                )
            )
        findings.extend(item_findings)

    errors = _error_findings(findings)
    if errors:
        raise SubmissionValidationError(errors)
    non_blocking_findings = [f for f in findings if f not in errors]

    repo = open_translation_store(project, default_format=StoreFormat.V2)

    def _mutate(store: TranslationStoreV2) -> None:
        store.source_sha256 = bundle.snapshot.source.source_sha256
        for item in submitted:
            task_record = task_records[item.id]
            source_view = source_by_id[item.id]
            target_text = resolved_target_by_id[item.id]
            ensure_store_record(
                store,
                item.id,
                source=source_view.source,
                source_sha256=source_view.source_sha256,
            )
            _track, subversion = lookup_version(
                version_ledger, task_record.output_version_ref
            )
            upsert_translation_version(
                store.records[item.id],
                task_record.output_version_ref,
                target_text,
                updated_at=timestamp,
                activate=True,
                baseline_ref=task_record.output_version_ref,
                baseline_sha256=subversion.baseline_sha256,
                context_view_sha256=task.context_view_sha256,
                context_view_path=task.context_view_path,
            )
            accepted_versions.append(task_record.output_version_ref)

            selected_candidate = (
                _candidate_for_label(task_record, item.selected)
                if item.selected and item.selected != "edited"
                else None
            )
            candidate_evidence = [
                _candidate_evidence(candidate) for candidate in task_record.candidates
            ]
            ledger.records[item.id] = JudgeDecision(
                record_id=item.id,
                output_version_ref=task_record.output_version_ref,
                output_target_sha256=sha256_text(target_text)
                if task.selection_purpose == "revise"
                else None,
                decision_kind=item.decision_kind,  # type: ignore[arg-type]
                selected_profile=selected_candidate.profile
                if selected_candidate is not None
                else None,
                selected_kind=selected_candidate.selected_kind
                if selected_candidate is not None
                else None,
                selected_ref=selected_candidate.selected_ref
                if selected_candidate is not None
                else None,
                selected_target_sha256=selected_candidate.target_sha256
                if selected_candidate is not None
                else None,
                judge_task_id=task.judge_task_id,
                judge_model=resolve_identity(project).model,
                reason=item.reason,
                candidate_evidence_sha256=canonical_json_sha256(
                    [entry.model_dump(mode="json") for entry in candidate_evidence]
                ),
                candidate_evidence=candidate_evidence,
                created_at=timestamp,
                updated_at=timestamp,
            )

    repo.edit_records(
        [item.id for item in submitted],
        _mutate,
        summary="accept judge submission",
        source_sha256=bundle.snapshot.source.source_sha256,
    )
    ledger.profile = project.profile or ledger.profile
    ledger.source_sha256 = bundle.snapshot.source.source_sha256
    ledger.source_profiles = list(task.source_profiles)
    write_translation_selection_ledger(project, ledger)
    return JudgeInsertResult(
        accepted_records=len(submitted),
        version_refs=accepted_versions,
        record_findings=non_blocking_findings,
    )
