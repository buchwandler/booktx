"""Snapshot read/write helpers extracted from :mod:`booktx.source_analysis`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from booktx.source_analysis import (
    ANALYSIS_SCHEMA,
    SNAPSHOT_SCHEMA,
    SnapshotRead,
    SnapshotValidationError,
    SourceAnalysisReport,
    SourceAnalysisSnapshot,
    compute_analysis_sha256,
)

if TYPE_CHECKING:
    from booktx.config import Project


def build_snapshot(
    report: SourceAnalysisReport, *, profile: str, generated_at: str
) -> SourceAnalysisSnapshot:
    """Wrap a canonical report in a profile-scoped snapshot envelope."""

    return SourceAnalysisSnapshot(
        schema=SNAPSHOT_SCHEMA,
        generated=True,
        canonical=False,
        profile=profile,
        snapshot_generated_at=generated_at,
        source_sha256=report.source_sha256,
        extracted_input_sha256=report.extracted_input_sha256,
        analysis_sha256=report.analysis_sha256,
        report=report,
    )


def validate_snapshot_payload(payload: dict[str, object]) -> SourceAnalysisSnapshot:
    """Validate a parsed snapshot payload and verify its embedded digest."""

    schema = payload.get("schema") or payload.get("schema_name")
    if schema != SNAPSHOT_SCHEMA:
        raise SnapshotValidationError(
            "source_analysis_bad_snapshot_schema",
            f"source-analysis snapshot has unexpected schema: {schema!r}",
        )
    if payload.get("generated") is not True or payload.get("canonical") is not False:
        raise SnapshotValidationError(
            "source_analysis_bad_snapshot_envelope",
            "source-analysis snapshot envelope flags are invalid",
        )
    snapshot = SourceAnalysisSnapshot.model_validate(payload)
    recomputed = compute_analysis_sha256(snapshot.report)
    if recomputed != snapshot.analysis_sha256:
        raise SnapshotValidationError(
            "source_analysis_snapshot_tampered",
            "source-analysis snapshot analysis_sha256 does not match its "
            "embedded report",
        )
    return snapshot


def read_snapshot(
    path: object, *, expected_analysis_sha256: str | None = None
) -> SnapshotRead:
    """Read and validate a profile snapshot, reporting staleness safely."""

    snapshot_path = Path(path)  # type: ignore[arg-type]
    if not snapshot_path.is_file():
        raise SnapshotValidationError(
            "source_analysis_snapshot_missing",
            "no source-analysis snapshot exists for this profile; "
            "run `booktx source analyze . --write --sync-profiles` from the "
            "project root",
        )
    payload = json.loads(snapshot_path.read_text("utf-8"))
    snapshot = validate_snapshot_payload(payload)
    stale = False
    hint = ""
    if (
        expected_analysis_sha256
        and snapshot.analysis_sha256 != expected_analysis_sha256
    ):
        stale = True
        hint = (
            "source-analysis snapshot is stale relative to the canonical report; "
            "refresh with `booktx source analyze . --write --sync-profiles`"
        )
    return SnapshotRead(snapshot=snapshot, stale=stale, hint=hint)


def read_canonical_report(project: Project) -> SourceAnalysisReport | None:
    """Read the canonical project-root report, or ``None`` when absent."""

    from booktx.config import source_analysis_path

    path = source_analysis_path(project)
    if not path.is_file():
        return None
    payload = json.loads(path.read_text("utf-8"))
    if (payload.get("schema") or payload.get("schema_name")) != ANALYSIS_SCHEMA:
        raise SnapshotValidationError(
            "source_analysis_bad_report_schema",
            "canonical source-analysis report has unexpected schema",
        )
    report = SourceAnalysisReport.model_validate(payload)
    recomputed = compute_analysis_sha256(report)
    if recomputed != report.analysis_sha256:
        raise SnapshotValidationError(
            "source_analysis_report_tampered",
            "canonical source-analysis report analysis_sha256 does not match "
            "its content",
        )
    return report
