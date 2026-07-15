from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from booktx.build import build_project
from booktx.cli import app
from booktx.config import (
    Project,
    load_project,
    project_source_sha256,
    translation_store_path,
    translation_store_v3_root,
    write_translation_store,
    write_translation_version_ledger,
)
from booktx.editor_indexes import build_editor_indexes
from booktx.models import (
    StoredTranslationRecordV2,
    TranslationCandidate,
    TranslationReviewCandidate,
    TranslationStoreV2,
    TranslationSubversionLedgerEntry,
    TranslationTrackLedgerEntry,
    TranslationVersionLedger,
)
from booktx.progress import load_source_chunks, source_record_sha256
from booktx.store import StoreFormat, open_translation_store
from booktx.translation_store import effective_candidate_selection, sha256_text

TS = "2026-06-22T12:00:00Z"
RUNNER = CliRunner()

SOURCE_BY_SLUG = {
    "wasp": "The Wasp scout arrived.",
    "mantis": "The Mantis captain answered.",
    "beetle": "The Beetle archivist wrote notes.",
    "cicada": "The Cicada singer waited.",
}

EXPECTED_BUILD_TEXT = """# Chapter One

Die Wespenkundschafterin kam nun schliesslich an.

Die Mantis-Kapitaenin antwortete.

# Chapter Two

Der Kaeferarchivar schrieb neue Notizen.

Die Zikadensaengerin wartete.
"""

DOC = """# Chapter One

The Wasp scout arrived.

The Mantis captain answered.

# Chapter Two

The Beetle archivist wrote notes.

The Cicada singer waited.
"""


@dataclass(slots=True)
class RichStoreFixture:
    project: Project
    store: TranslationStoreV2
    record_ids: dict[str, str]


def _init_project(tmp_path: Path, *, chunk_size: int = 2) -> tuple[Project, list[Any]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source_path = tmp_path / "book.md"
    source_path.write_text(DOC, encoding="utf-8")
    project_dir = tmp_path / "book"
    init_res = RUNNER.invoke(
        app,
        [
            "init",
            str(project_dir),
            "--target",
            "de",
            "--source-file",
            str(source_path),
            "--chunk-size",
            str(chunk_size),
        ],
    )
    assert init_res.exit_code == 0, init_res.output
    extract_res = RUNNER.invoke(app, ["extract", str(project_dir)])
    assert extract_res.exit_code == 0, extract_res.output
    RUNNER.invoke(app, ["context", "init", str(project_dir), "--non-interactive"])
    RUNNER.invoke(
        app,
        [
            "context",
            "mark-ready",
            str(project_dir),
            "--force",
            "--reason",
            "store parity fixture setup",
        ],
    )
    project = load_project(project_dir, profile="de_default")
    return project, load_source_chunks(project)


def _record_ids(chunks: list[Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for slug, source_text in SOURCE_BY_SLUG.items():
        for chunk in chunks:
            for record in chunk.records:
                if record.source == source_text:
                    mapping[slug] = record.id
                    break
            if slug in mapping:
                break
    assert set(mapping) == set(SOURCE_BY_SLUG)
    return mapping


def _version(
    target: str,
    *,
    version_ref: str,
    baseline_ref: str | None = None,
    baseline_target: str | None = None,
    context_view_sha256: str | None = None,
    context_view_path: str | None = None,
    context_notes_scope: str | None = None,
    context_target_chapter_id: str | None = None,
    context_notes_through_chapter_id: str | None = None,
    reviewed_at: str | None = None,
    reviewed_by: str | None = None,
    review_note: str | None = None,
) -> TranslationCandidate:
    major, minor = (int(piece) for piece in version_ref.split("."))
    return TranslationCandidate(
        version=major,
        subversion=minor,
        version_ref=version_ref,
        baseline_ref=baseline_ref,
        baseline_sha256=(
            sha256_text(baseline_target) if baseline_target is not None else None
        ),
        context_view_sha256=context_view_sha256,
        context_view_path=context_view_path,
        context_notes_scope=context_notes_scope,
        context_target_chapter_id=context_target_chapter_id,
        context_notes_through_chapter_id=context_notes_through_chapter_id,
        target=target,
        created_at=TS,
        updated_at=TS,
        reviewed_at=reviewed_at,
        reviewed_by=reviewed_by,
        review_note=review_note,
    )


def _review(
    target: str,
    *,
    review_ref: str,
    base_kind: str,
    base_ref: str,
    base_target: str,
    status: str = "accepted",
    context_view_sha256: str | None = None,
    context_view_path: str | None = None,
    review_window_sha256: str | None = None,
    review_policy_sha256: str | None = None,
    reviewed_by: str | None = None,
    review_model: str | None = None,
    review_task_id: str | None = None,
    review_note: str | None = None,
) -> TranslationReviewCandidate:
    pass_number = int(review_ref.split("R", 1)[1].split(".", 1)[0])
    run_number = int(review_ref.split(".", 1)[1])
    return TranslationReviewCandidate(
        pass_number=pass_number,
        run_number=run_number,
        review_ref=review_ref,
        base_kind=base_kind,  # type: ignore[arg-type]
        base_ref=base_ref,
        base_target_sha256=sha256_text(base_target),
        target=target,
        target_sha256=sha256_text(target),
        status=status,  # type: ignore[arg-type]
        created_at=TS,
        updated_at=TS,
        reviewed_by=reviewed_by,
        review_model=review_model,
        review_task_id=review_task_id,
        review_note=review_note,
        context_view_sha256=context_view_sha256,
        context_view_path=context_view_path,
        review_window_sha256=review_window_sha256,
        review_policy_sha256=review_policy_sha256,
    )


def _store_record(
    source_text: str,
    record_id: str,
    *,
    active_version: str | None,
    active_review: str | None = None,
    versions: list[TranslationCandidate],
    reviews: list[TranslationReviewCandidate] | None = None,
) -> StoredTranslationRecordV2:
    return StoredTranslationRecordV2(
        chunk_id=int(record_id.split("-", 1)[0]),
        part_id=int(record_id.split("-", 1)[1]),
        source_sha256=source_record_sha256(source_text),
        source=source_text,
        active_version=active_version,
        active_review=active_review,
        versions=versions,
        reviews=reviews or [],
    )


def _build_store(
    project: Project,
    record_ids: dict[str, str],
    *,
    activate_stale_review: bool,
) -> TranslationStoreV2:
    wasp_v11 = "Die Wespenkundschafterin traf ein."
    wasp_v12 = "Die Wespenkundschafterin kam an."
    wasp_r11 = "Die Wespenkundschafterin kam ruhig an."
    wasp_r12 = "Die Wespenkundschafterin kam nun an."
    wasp_r21 = "Die Wespenkundschafterin kam nun schliesslich an."

    mantis_v11 = "Die Mantis-Kapitaenin antwortete."
    mantis_v12 = "Die Mantis-Kommandantin antwortete."

    beetle_v11 = "Der Kaeferarchivar schrieb neue Notizen."
    beetle_old_base = "Der Kaeferarchivar schrieb alte Notizen."
    beetle_r11 = "Der Kaeferarchivar fuehrte Protokoll."
    beetle_r12 = "Der Kaeferarchivar fuehrte genaue Protokolle."

    cicada_v11 = "Die Zikadensaengerin wartete."
    cicada_r11 = "Die Zikade wartete."
    cicada_r21 = "Die Saengerin harrte aus."

    wasp_source = SOURCE_BY_SLUG["wasp"]
    mantis_source = SOURCE_BY_SLUG["mantis"]
    beetle_source = SOURCE_BY_SLUG["beetle"]
    cicada_source = SOURCE_BY_SLUG["cicada"]

    records = {
        record_ids["wasp"]: _store_record(
            wasp_source,
            record_ids["wasp"],
            active_version="1.2",
            active_review="R2.1",
            versions=[
                _version(target=wasp_v11, version_ref="1.1"),
                _version(
                    target=wasp_v12,
                    version_ref="1.2",
                    baseline_ref="1.1",
                    baseline_target=wasp_v11,
                    context_view_sha256="a" * 64,
                    context_view_path=".booktx/context/views/a/view.md",
                    context_notes_scope="before_target_chapter",
                    context_target_chapter_id="0001",
                    context_notes_through_chapter_id="0001",
                    reviewed_at=TS,
                    reviewed_by="user:reviewer",
                    review_note="Accepted after human spot check.",
                ),
            ],
            reviews=[
                _review(
                    target=wasp_r11,
                    review_ref="R1.1",
                    base_kind="translation",
                    base_ref="1.2",
                    base_target=wasp_v12,
                    context_view_sha256="b" * 64,
                    context_view_path=".booktx/context/views/b/view.md",
                    review_window_sha256="c" * 64,
                    review_policy_sha256="d" * 64,
                    reviewed_by="agent:reviewer",
                    review_model="gpt-5.4",
                    review_task_id="review-task-1",
                    review_note="Pass 1 polish.",
                ),
                _review(
                    target=wasp_r12,
                    review_ref="R1.2",
                    base_kind="review",
                    base_ref="R1.1",
                    base_target=wasp_r11,
                    context_view_sha256="e" * 64,
                    context_view_path=".booktx/context/views/e/view.md",
                    review_window_sha256="f" * 64,
                    review_policy_sha256="g" * 64,
                    reviewed_by="agent:reviewer",
                    review_model="gpt-5.4",
                    review_task_id="review-task-2",
                    review_note="Same-pass rerun.",
                ),
                _review(
                    target=wasp_r21,
                    review_ref="R2.1",
                    base_kind="review",
                    base_ref="R1.2",
                    base_target=wasp_r12,
                    context_view_sha256="h" * 64,
                    context_view_path=".booktx/context/views/h/view.md",
                    review_window_sha256="i" * 64,
                    review_policy_sha256="j" * 64,
                    reviewed_by="agent:reviewer",
                    review_model="gpt-5.4",
                    review_task_id="review-task-3",
                    review_note="Pass 2 final polish.",
                ),
            ],
        ),
        record_ids["mantis"]: _store_record(
            mantis_source,
            record_ids["mantis"],
            active_version="1.1",
            versions=[
                _version(target=mantis_v11, version_ref="1.1"),
                _version(
                    target=mantis_v12,
                    version_ref="1.2",
                    baseline_ref="1.1",
                    baseline_target=mantis_v11,
                    context_view_sha256="k" * 64,
                    context_view_path=".booktx/context/views/k/view.md",
                    reviewed_at=TS,
                    reviewed_by="user:reviewer",
                    review_note="Kept inactive for wording preference.",
                ),
            ],
        ),
        record_ids["beetle"]: _store_record(
            beetle_source,
            record_ids["beetle"],
            active_version="1.1",
            active_review="R1.2" if activate_stale_review else None,
            versions=[
                _version(target=beetle_v11, version_ref="1.1"),
            ],
            reviews=[
                _review(
                    target=beetle_r11,
                    review_ref="R1.1",
                    base_kind="translation",
                    base_ref="1.1",
                    base_target=beetle_old_base,
                    reviewed_by="agent:reviewer",
                    review_model="gpt-5.4",
                    review_task_id="review-task-4",
                    review_note="Recorded before the base changed.",
                ),
                _review(
                    target=beetle_r12,
                    review_ref="R1.2",
                    base_kind="review",
                    base_ref="R1.1",
                    base_target=beetle_r11,
                    reviewed_by="agent:reviewer",
                    review_model="gpt-5.4",
                    review_task_id="review-task-5",
                    review_note="Rerun built on a stale chain.",
                ),
            ],
        ),
        record_ids["cicada"]: _store_record(
            cicada_source,
            record_ids["cicada"],
            active_version="1.1",
            versions=[
                _version(target=cicada_v11, version_ref="1.1"),
            ],
            reviews=[
                _review(
                    target=cicada_r11,
                    review_ref="R1.1",
                    base_kind="translation",
                    base_ref="1.1",
                    base_target=cicada_v11,
                    status="rejected",
                    reviewed_by="agent:reviewer",
                    review_model="gpt-5.4",
                    review_task_id="review-task-6",
                    review_note="Rejected alternative.",
                ),
                _review(
                    target=cicada_r21,
                    review_ref="R2.1",
                    base_kind="translation",
                    base_ref="1.1",
                    base_target=cicada_v11,
                    status="superseded",
                    reviewed_by="agent:reviewer",
                    review_model="gpt-5.4",
                    review_task_id="review-task-7",
                    review_note="Superseded experiment.",
                ),
            ],
        ),
    }
    return TranslationStoreV2(
        source_sha256=project_source_sha256(project),
        records=records,
    )


def _write_version_ledger(project: Project) -> None:
    write_translation_version_ledger(
        project,
        TranslationVersionLedger(
            active_version="1.2",
            tracks={
                "1": TranslationTrackLedgerEntry(
                    version=1,
                    actor="user:test",
                    harness="pi",
                    model="human",
                    created_at=TS,
                    updated_at=TS,
                    subversions={
                        "1": TranslationSubversionLedgerEntry(
                            version=1,
                            subversion=1,
                            version_ref="1.1",
                            context_sha256="1" * 64,
                            created_at=TS,
                            updated_at=TS,
                        ),
                        "2": TranslationSubversionLedgerEntry(
                            version=1,
                            subversion=2,
                            version_ref="1.2",
                            context_sha256="2" * 64,
                            created_at=TS,
                            updated_at=TS,
                        ),
                    },
                )
            },
        ),
    )


def create_rich_store_fixture(
    tmp_path: Path,
    *,
    store_format: StoreFormat,
    activate_stale_review: bool = True,
) -> RichStoreFixture:
    project, chunks = _init_project(tmp_path)
    record_ids = _record_ids(chunks)
    store = _build_store(
        project,
        record_ids,
        activate_stale_review=activate_stale_review,
    )
    _write_version_ledger(project)

    v3_root = translation_store_v3_root(project)
    legacy_path = translation_store_path(project)
    if store_format == StoreFormat.V2:
        if v3_root.exists():
            shutil.rmtree(v3_root)
        write_translation_store(project, store)
    elif store_format == StoreFormat.V3:
        if legacy_path.exists():
            legacy_path.unlink()
        repo = open_translation_store(project, default_format=StoreFormat.V3)
        repo.write_materialized_v2(store)
    else:
        raise ValueError(f"unsupported fixture store format: {store_format.value}")

    return RichStoreFixture(project=project, store=store, record_ids=record_ids)


def normalized_semantic_projection(store: TranslationStoreV2) -> dict[str, Any]:
    def _normalized_records() -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for record_id in sorted(store.records):
            record = store.records[record_id]
            selection = effective_candidate_selection(
                record, strict_active_review=False
            )
            items.append(
                {
                    "record_id": record_id,
                    "chunk_id": record.chunk_id,
                    "part_id": record.part_id,
                    "source_sha256": record.source_sha256,
                    "source": record.source,
                    "active_version": record.active_version,
                    "active_review": record.active_review,
                    "versions": [
                        candidate.model_dump(mode="python")
                        for candidate in sorted(
                            record.versions,
                            key=lambda candidate: (
                                candidate.version,
                                candidate.subversion,
                            ),
                        )
                    ],
                    "reviews": [
                        candidate.model_dump(mode="python")
                        for candidate in sorted(
                            record.reviews,
                            key=lambda candidate: (
                                candidate.pass_number,
                                candidate.run_number,
                                candidate.review_ref,
                            ),
                        )
                    ],
                    "effective": (
                        None
                        if selection is None
                        else {
                            "selected_kind": selection.selected_kind,
                            "selected_ref": selection.selected_ref,
                            "version_ref": selection.version_ref,
                            "review_ref": selection.review_ref,
                            "review_chain": list(selection.review_chain),
                            "target": selection.candidate.target,
                        }
                    ),
                }
            )
        return items

    return {
        "source_sha256": store.source_sha256,
        "record_ids": sorted(store.records),
        "records": _normalized_records(),
    }


def normalized_editor_indexes(project: Project) -> dict[str, Any]:
    source_index, target_index, source_target_index, findings = build_editor_indexes(
        project
    )

    def _normalize_payload(payload: Any) -> dict[str, Any]:
        normalized = payload.model_dump(by_alias=True)
        normalized.pop("generated_at", None)
        return normalized

    return {
        "source": _normalize_payload(source_index),
        "target": _normalize_payload(target_index),
        "source_target": _normalize_payload(source_target_index),
        "findings": [
            {
                "chunk_id": finding.chunk_id,
                "record_id": finding.record_id,
                "rule": finding.rule,
                "severity": getattr(finding.severity, "value", str(finding.severity)),
            }
            for finding in findings
        ],
    }


def build_output_text(project: Project) -> str:
    return build_project(project).output_path.read_text("utf-8")
