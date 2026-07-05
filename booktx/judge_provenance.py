"""Shared read-only revision-provenance audit.

A revision profile's effective output is valid only while each active target has
matching judge-decision provenance. Status, validation, and build all consume
this one audit so they cannot drift apart.

The provenance invariant for a record (see the implementation brief) requires:

1. the translation store has a valid active effective target;
2. ``translation-selection-ledger.json`` has a ``JudgeDecision`` for the record;
3. the decision references a durable judge task;
4. the decision's ``output_version_ref`` equals the active effective version;
5. the decision's ``output_target_sha256`` equals the SHA-256 of the active
   effective target;
6. the decision was created from a task whose ``selection_purpose`` is
   ``"revise"``.

Historical inactive targets are not audited: only the effective active output is
checked. A record with no active target is simply not provenance-valid (it is
neither selected nor flagged here); the "every source record needs an active
target" requirement is owned by ``build --require-complete``.

This module is dependency-light: it imports only :mod:`booktx.config`,
:mod:`booktx.translation_store`, and the stdlib so that validation and build can
use it without import cycles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from booktx.config import (
    Project,
    load_judge_task,
    load_translation_selection_ledger,
    load_translation_store,
)
from booktx.translation_store import (
    EffectiveCandidateError,
    effective_candidate_selection,
    sha256_text,
)

if TYPE_CHECKING:
    from booktx.models import (
        JudgeDecision,
        JudgeTask,
        TranslationSelectionLedger,
        TranslationStoreV2,
    )

__all__ = [
    "RevisionProvenanceIssue",
    "RevisionProvenanceAudit",
    "audit_revision_provenance",
]


@dataclass(frozen=True, slots=True)
class RevisionProvenanceIssue:
    """One structured provenance problem for a single record."""

    record_id: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class RevisionProvenanceAudit:
    """Result of auditing revision provenance.

    ``valid_record_ids`` is the set of audited records whose active effective
    target has full matching judge-decision provenance. ``issues`` lists every
    record that has an active effective target but fails one invariant check.
    Records with no active effective target appear in neither collection.
    """

    valid_record_ids: set[str] = field(default_factory=set)
    issues: list[RevisionProvenanceIssue] = field(default_factory=list)


def audit_revision_provenance(
    project: Project,
    *,
    record_ids: list[str] | None = None,
    store: TranslationStoreV2 | None = None,
    ledger: TranslationSelectionLedger | None = None,
) -> RevisionProvenanceAudit:
    """Audit revision provenance for the selection profile.

    Parameters
    ----------
    project:
        A selection profile project whose ``selection.purpose`` is ``"revise"``.
        Callers are responsible for guarding the purpose; this function only
        reads state and never mutates.
    record_ids:
        Optional scope. When ``None``, every record in the store is audited.
        Pass an explicit list to scope findings consistently with chapter/task/
        record filters used by validation and build.
    store, ledger:
        Optional pre-loaded state. When omitted, the current on-disk store and
        selection ledger are loaded once. Judge tasks are loaded lazily and
        cached per ``judge_task_id`` within this call.
    """
    if store is None:
        store = load_translation_store(project)
    if ledger is None:
        ledger = load_translation_selection_ledger(project)
    decisions: dict[str, JudgeDecision] = ledger.records

    scope = record_ids if record_ids is not None else list(store.records.keys())

    valid: set[str] = set()
    issues: list[RevisionProvenanceIssue] = []
    task_cache: dict[str, JudgeTask | None] = {}

    def _task(task_id: str) -> JudgeTask | None:
        if task_id not in task_cache:
            task_cache[task_id] = load_judge_task(project, task_id)
        return task_cache[task_id]

    for record_id in scope:
        stored = store.records.get(record_id)
        if stored is None:
            # No store record: out of scope for provenance (no active target).
            continue
        selection = effective_candidate_selection(stored, strict_active_review=True)
        if isinstance(selection, EffectiveCandidateError) or selection is None:
            # No valid active effective target: not provenance-valid, but the
            # missing-target case is owned by build --require-complete, not here.
            continue

        active_version = selection.version_ref
        active_hash = sha256_text(selection.candidate.target)

        decision = decisions.get(record_id)
        if decision is None:
            issues.append(
                RevisionProvenanceIssue(
                    record_id=record_id,
                    code="judge_revision_decision_missing",
                    message=(
                        f"record {record_id} has an active target but no "
                        "judge decision in the selection ledger"
                    ),
                )
            )
            continue
        if decision.output_version_ref != active_version:
            issues.append(
                RevisionProvenanceIssue(
                    record_id=record_id,
                    code="judge_revision_output_version_mismatch",
                    message=(
                        f"record {record_id} decision output_version_ref "
                        f"{decision.output_version_ref!r} does not match the "
                        f"active effective version {active_version!r}"
                    ),
                )
            )
            continue
        if decision.output_target_sha256 is None:
            issues.append(
                RevisionProvenanceIssue(
                    record_id=record_id,
                    code="judge_revision_output_hash_missing",
                    message=(
                        f"record {record_id} judge decision has no "
                        "output_target_sha256; re-accept it through judge mode"
                    ),
                )
            )
            continue
        if decision.output_target_sha256 != active_hash:
            issues.append(
                RevisionProvenanceIssue(
                    record_id=record_id,
                    code="judge_revision_output_hash_mismatch",
                    message=(
                        f"record {record_id} active target hash does not match "
                        "the judge decision output_target_sha256; the target "
                        "was changed outside judge mode"
                    ),
                )
            )
            continue

        task = _task(decision.judge_task_id)
        if task is None:
            issues.append(
                RevisionProvenanceIssue(
                    record_id=record_id,
                    code="judge_revision_task_missing",
                    message=(
                        f"record {record_id} decision references judge task "
                        f"{decision.judge_task_id!r} which no longer exists"
                    ),
                )
            )
            continue
        if task.selection_purpose != "revise":
            issues.append(
                RevisionProvenanceIssue(
                    record_id=record_id,
                    code="judge_revision_task_purpose_mismatch",
                    message=(
                        f"record {record_id} decision references judge task "
                        f"{decision.judge_task_id!r} whose selection_purpose is "
                        f"{task.selection_purpose!r}, not 'revise'"
                    ),
                )
            )
            continue

        valid.add(record_id)

    return RevisionProvenanceAudit(valid_record_ids=valid, issues=issues)
