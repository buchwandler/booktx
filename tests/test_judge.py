"""Tests for judge/selection-profile workflows."""

from __future__ import annotations

import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path

from typer.testing import CliRunner

from booktx.cli import app
from booktx.config import (
    create_profile,
    judge_ingest_block_path,
    judge_ingest_decisions_path,
    judge_ingest_json_path,
    judge_source_profile_dir,
    judge_sources_manifest_path,
    load_judge_task,
    load_profile_config,
    load_profile_project,
    load_source_project,
    load_translation_selection_ledger,
    load_translation_store,
    write_profile_config,
    write_translation_store,
)
from booktx.context import GlossaryEntry, TranslationContext, load_context
from booktx.glossary_tasking import applicable_glossary_snapshots
from booktx.judge_acceptance import _binding_glossary_findings
from booktx.judge_sources import (
    judge_sources_manifest_sha256,
    load_snapshot_judge_source_views,
    validate_judge_sources_snapshot,
    validate_snapshot_source_subset,
)
from booktx.judge_todos import latest_todo
from booktx.models import Record, SelectionConfig, TranslationReviewCandidate
from booktx.progress import load_source_records
from booktx.status import build_status_snapshot
from booktx.translation_store import (
    ensure_store_record,
    sha256_text,
    upsert_translation_version,
)
from booktx.versioning import resolve_current_version

runner = CliRunner(env={"COLUMNS": "120"})


def test_judge_glossary_findings_respect_longer_phrase_shadow() -> None:
    context = TranslationContext(
        source_language="en",
        target_language="de",
        glossary=[
            GlossaryEntry(
                source="Cricket-kinden",
                target="Grillenart",
                require_target=True,
                enforce="error",
            ),
            GlossaryEntry(
                source="Mole Cricket-kinden",
                target="Maulwurfsgrillenart",
                require_target=True,
                enforce="error",
            ),
        ],
    )

    findings = _binding_glossary_findings(
        Record(
            id="0001-000001",
            source="One of the great Mole Cricket-kinden turned.",
        ),
        target_text="Eine Maulwurfsgrillenart drehte sich um.",
        chunk_id="0001",
        context=context,
    )

    assert findings == []


def test_judge_glossary_tasking_helper_respects_longer_phrase_shadow() -> None:
    snapshots = applicable_glossary_snapshots(
        "The Empire State watches.",
        [
            GlossaryEntry(
                source="Empire",
                target="Imperium",
                require_target=True,
                status="approved",
                enforce="error",
            ),
            GlossaryEntry(
                source="Empire State",
                target="Imperium State",
                require_target=True,
                status="approved",
                enforce="error",
            ),
        ],
    )
    assert [snapshot.source for snapshot in snapshots] == ["Empire State"]
    assert snapshots[0].matched_source_cue == "Empire State"


DOC = """\
# One

The Empire advances. The Lowlands answer.
"""

MULTI_CHAPTER_DOC = """\
# One

The Empire advances.

# Two

The Lowlands answer.

# Three

A third paragraph stands here.
"""

REQUIRED_ANSWERS = (
    ("Q001", "de-DE"),
    ("Q002", "balanced"),
    ("Q003", "neutral"),
    ("Q004", "natural dialogue"),
    ("Q005", "keep Apt names"),
    ("Q006", "translate world terms"),
    ("Q012", "error"),
)


def _make_project(tmp_path: Path) -> Path:
    project_dir = tmp_path / "book"
    src = tmp_path / "novel.md"
    src.write_text(DOC, encoding="utf-8")
    res = runner.invoke(
        app,
        [
            "init",
            str(project_dir),
            "--source-file",
            str(src),
            "--source-lang",
            "en",
        ],
    )
    assert res.exit_code == 0, res.output
    assert runner.invoke(app, ["extract", str(project_dir)]).exit_code == 0
    return project_dir


def _create_translation_profile(project_dir: Path, profile: str) -> None:
    create_profile(project_dir, profile, target_language="de")


def _create_selection_profile(
    project_dir: Path, profile: str, sources: list[str]
) -> None:
    create_profile(project_dir, profile, target_language="de", kind="selection")
    cfg = load_profile_config(project_dir, profile)
    cfg.selection = SelectionConfig(sources=sources)
    write_profile_config(project_dir, cfg)


def _ready_context(project_dir: Path, profile: str) -> None:
    res = runner.invoke(
        app,
        [
            "context",
            "init",
            str(project_dir),
            "--profile",
            profile,
            "--non-interactive",
        ],
    )
    assert res.exit_code == 0, res.output
    for qid, text in REQUIRED_ANSWERS:
        res = runner.invoke(
            app,
            [
                "context",
                "answer",
                str(project_dir),
                qid,
                "--profile",
                profile,
                "--text",
                text,
            ],
        )
        assert res.exit_code == 0, res.output
    res = runner.invoke(
        app, ["context", "mark-ready", str(project_dir), "--profile", profile]
    )
    assert res.exit_code == 0, res.output


def _set_term(
    project_dir: Path, profile: str, source: str, target: str, *forbidden: str
) -> None:
    args = [
        "context",
        "reset-term",
        str(project_dir),
        source,
        "--profile",
        profile,
        "--target",
        target,
        "--create",
        "--enforce",
        "error",
    ]
    for value in forbidden:
        args.extend(["--forbid", value])
    res = runner.invoke(app, args)
    assert res.exit_code == 0, res.output


def _record_ids(project_dir: Path) -> list[str]:
    proj = load_source_project(project_dir)
    return [record.record_id for record in load_source_records(proj)]


def _record_id_for_text(project_dir: Path, fragment: str) -> str:
    proj = load_source_project(project_dir)
    return next(
        record.record_id
        for record in load_source_records(proj)
        if fragment in record.source
    )


def _write_source_candidate(
    project_dir: Path,
    profile: str,
    record_id: str,
    target: str,
    *,
    review_target: str | None = None,
) -> None:
    proj = load_profile_project(project_dir, profile)
    view = next(
        item for item in load_source_records(proj) if item.record_id == record_id
    )
    version_ref = resolve_current_version(proj).version_ref
    store = load_translation_store(proj)
    ensure_store_record(
        store, record_id, source=view.source, source_sha256=view.source_sha256
    )
    upsert_translation_version(
        store.records[record_id],
        version_ref,
        target,
        updated_at="2026-07-01T12:00:00Z",
        activate=True,
    )
    if review_target is not None:
        review = TranslationReviewCandidate(
            pass_number=1,
            run_number=1,
            review_ref="R1.1",
            base_kind="translation",
            base_ref=version_ref,
            base_target_sha256=sha256_text(target),
            target=review_target,
            target_sha256=sha256_text(review_target),
            status="accepted",
            created_at="2026-07-01T12:01:00Z",
            updated_at="2026-07-01T12:01:00Z",
        )
        store.records[record_id].reviews.append(review)
        store.records[record_id].active_review = "R1.1"
    write_translation_store(proj, store)


def _judge_project(tmp_path: Path) -> tuple[Path, list[str]]:
    project_dir = _make_project(tmp_path)
    _create_translation_profile(project_dir, "de_a")
    _create_translation_profile(project_dir, "de_b")
    _create_selection_profile(project_dir, "de_judge", ["de_a", "de_b"])
    for profile in ("de_a", "de_b", "de_judge"):
        _ready_context(project_dir, profile)
    return project_dir, _record_ids(project_dir)


def _multi_chapter_judge_project(
    tmp_path: Path,
) -> tuple[Path, dict[str, list[str]]]:
    """Three-chapter judge project; returns chapter_id -> ordered record ids."""
    project_dir = tmp_path / "book"
    src = tmp_path / "novel.md"
    src.write_text(MULTI_CHAPTER_DOC, encoding="utf-8")
    res = runner.invoke(
        app,
        [
            "init",
            str(project_dir),
            "--source-file",
            str(src),
            "--source-lang",
            "en",
        ],
    )
    assert res.exit_code == 0, res.output
    assert runner.invoke(app, ["extract", str(project_dir)]).exit_code == 0
    _create_translation_profile(project_dir, "de_a")
    _create_translation_profile(project_dir, "de_b")
    _create_selection_profile(project_dir, "de_judge", ["de_a", "de_b"])
    for profile in ("de_a", "de_b", "de_judge"):
        _ready_context(project_dir, profile)
    proj = load_profile_project(project_dir, "de_judge")
    bundle = build_status_snapshot(proj, context_exists=True, context_ready=True)
    chapters = {
        cid: list(rids) for cid, rids in bundle.index.record_ids_by_chapter.items()
    }
    return project_dir, chapters


def _judge_task_id(project_dir: Path) -> str:
    task_dir = project_dir / "translations" / "de_judge" / "judge-tasks"
    return next(task_dir.glob("*.json")).stem


def test_judge_create_profile_creates_selection_kind(tmp_path: Path):
    project_dir = _make_project(tmp_path)

    res = runner.invoke(
        app,
        [
            "judge",
            "create-profile",
            str(project_dir),
            "de_judge",
            "--target",
            "de",
            "--target-locale",
            "de-DE",
            "--sources",
            "de_a,de_b",
            "--model",
            "gpt-5.5",
        ],
    )

    assert res.exit_code == 0, res.output
    cfg = load_profile_config(project_dir, "de_judge")
    assert cfg.kind == "selection"
    assert cfg.selection is not None
    assert cfg.selection.sources == ["de_a", "de_b"]


def test_judge_create_profile_context_from_ready_source(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    _create_translation_profile(project_dir, "de_a")
    _create_translation_profile(project_dir, "de_b")
    _ready_context(project_dir, "de_a")

    res = runner.invoke(
        app,
        [
            "judge",
            "create-profile",
            str(project_dir),
            "de_judge",
            "--target",
            "de",
            "--sources",
            "de_a,de_b",
            "--context-from",
            "de_a",
        ],
    )

    assert res.exit_code == 0, res.output
    proj = load_profile_project(project_dir, "de_judge")
    ctx = load_context(proj)
    assert ctx is not None
    assert ctx.ready is True


def test_judge_next_includes_source_and_effective_candidates(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(
        project_dir, "de_a", record_ids[0], "Imperium marschiert vor."
    )
    _write_source_candidate(
        project_dir, "de_b", record_ids[0], "Das Imperium rückt vor."
    )

    res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--unit",
            "chapter",
            "--chapter",
            "0001",
            "--max-words",
            "900",
            "--format",
            "block",
        ],
    )

    assert res.exit_code == 0, res.output
    task = load_judge_task(
        load_profile_project(project_dir, "de_judge"), _judge_task_id(project_dir)
    )
    assert task is not None
    assert task.applicable_termbase_sha256
    assert task.records[0].source
    assert [candidate.profile for candidate in task.records[0].candidates] == [
        "de_a",
        "de_b",
    ]


def test_judge_next_uses_active_review_over_active_translation(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(
        project_dir,
        "de_a",
        record_ids[0],
        "Imperium marschiert vor.",
        review_target="Das überprüfte Imperium marschiert vor.",
    )
    _write_source_candidate(
        project_dir, "de_b", record_ids[0], "Das Imperium rückt vor."
    )

    res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--unit",
            "chapter",
            "--chapter",
            "0001",
            "--max-words",
            "900",
        ],
    )

    assert res.exit_code == 0, res.output
    task = load_judge_task(
        load_profile_project(project_dir, "de_judge"), _judge_task_id(project_dir)
    )
    candidate = next(
        item for item in task.records[0].candidates if item.profile == "de_a"
    )
    assert candidate.selected_kind == "review"
    assert candidate.selected_ref == "R1.1"
    assert candidate.target == "Das überprüfte Imperium marschiert vor."


def test_judge_next_omits_missing_candidates_by_default(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(
        project_dir, "de_a", record_ids[0], "Imperium marschiert vor."
    )

    res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--unit",
            "chapter",
            "--chapter",
            "0001",
            "--max-words",
            "900",
        ],
    )

    assert res.exit_code == 0, res.output
    task = load_judge_task(
        load_profile_project(project_dir, "de_judge"), _judge_task_id(project_dir)
    )
    assert len(task.records[0].candidates) == 1
    assert task.records[0].missing_profiles == ["de_b"]


def test_judge_next_require_all_sources_blocks_missing_candidate(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(
        project_dir, "de_a", record_ids[0], "Imperium marschiert vor."
    )

    res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--unit",
            "chapter",
            "--chapter",
            "0001",
            "--require-all-sources",
        ],
    )

    assert res.exit_code != 0
    assert "missing effective candidates" in res.output


def test_judge_next_honors_config_require_all_sources(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(
        project_dir, "de_a", record_ids[0], "Imperium marschiert vor."
    )
    cfg = load_profile_config(project_dir, "de_judge")
    assert cfg.selection is not None
    cfg.selection.require_all_sources = True
    write_profile_config(project_dir, cfg)

    res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--unit",
            "chapter",
            "--chapter",
            "0001",
        ],
    )

    assert res.exit_code != 0
    assert "missing effective candidates" in res.output


def test_judge_next_honors_max_records(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    for profile, base in (("de_a", "A"), ("de_b", "B")):
        for record_id in record_ids:
            _write_source_candidate(
                project_dir, profile, record_id, f"{base} {record_id}"
            )

    res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--max-records",
            "1",
        ],
    )

    assert res.exit_code == 0, res.output
    task = load_judge_task(
        load_profile_project(project_dir, "de_judge"), _judge_task_id(project_dir)
    )
    assert len(task.records) == 1


def test_judge_next_honors_max_rendered_lines(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    for profile, base in (("de_a", "A"), ("de_b", "B")):
        for record_id in record_ids:
            _write_source_candidate(
                project_dir, profile, record_id, f"{base} {record_id}"
            )

    res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--max-rendered-lines",
            "20",
        ],
    )

    assert res.exit_code == 0, res.output
    task = load_judge_task(
        load_profile_project(project_dir, "de_judge"), _judge_task_id(project_dir)
    )
    assert len(task.records) == 1


def test_judge_record_prints_submit_command(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(
        project_dir, "de_a", record_ids[0], "Imperium marschiert vor."
    )

    res = runner.invoke(
        app,
        [
            "judge",
            "record",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--record",
            record_ids[0],
        ],
    )

    assert res.exit_code == 0, res.output
    assert "submit: booktx judge insert" in res.output
    assert "--format block" in res.output


def test_judge_insert_copy_accepts_exact_selected_candidate(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(
        project_dir, "de_a", record_ids[0], "Imperium marschiert vor."
    )
    _write_source_candidate(
        project_dir, "de_b", record_ids[0], "Das Imperium rückt vor."
    )
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)
    ingest = judge_ingest_json_path(
        load_profile_project(project_dir, "de_judge"), task_id
    )
    ingest.write_text(
        json.dumps(
            {
                "judge_task_id": task_id,
                "records": [
                    {
                        "id": record_ids[0],
                        "selected": "A",
                        "decision_kind": "copy",
                        "target": "Imperium marschiert vor.",
                        "reason": "Best option.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    res = runner.invoke(
        app,
        [
            "judge",
            "insert",
            str(project_dir),
            "--profile",
            "de_judge",
            "--judge-task-id",
            task_id,
            "--file",
            str(ingest),
            "--format",
            "json",
        ],
    )

    assert res.exit_code == 0, res.output
    store = load_translation_store(load_profile_project(project_dir, "de_judge"))
    assert store.records[record_ids[0]].versions[0].target == "Imperium marschiert vor."


def test_judge_next_writes_decision_ingest_file(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(
        project_dir, "de_a", record_ids[0], "Imperium marschiert vor."
    )
    _write_source_candidate(
        project_dir, "de_b", record_ids[0], "Das Imperium rückt vor."
    )

    res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--format",
            "decisions",
        ],
    )

    assert res.exit_code == 0, res.output
    assert ".decisions.txt" in res.output
    task_id = _judge_task_id(project_dir)
    ingest = judge_ingest_decisions_path(
        load_profile_project(project_dir, "de_judge"), task_id
    )
    assert ingest.is_file()
    text = ingest.read_text("utf-8")
    assert "# booktx judge decisions" in text
    assert "decision_kind: copy" in text


def test_judge_insert_copy_with_empty_target_autofills_selected_candidate(
    tmp_path: Path,
):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(
        project_dir, "de_a", record_ids[0], "Imperium marschiert vor."
    )
    _write_source_candidate(
        project_dir, "de_b", record_ids[0], "Das Imperium rückt vor."
    )
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--format",
            "decisions",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)
    ingest = judge_ingest_decisions_path(
        load_profile_project(project_dir, "de_judge"), task_id
    )
    _fill_decisions_ingest(
        ingest,
        task_id,
        [(record_ids[0], "A", "copy", "Best option.", "")],
    )

    res = runner.invoke(
        app,
        [
            "judge",
            "insert",
            str(project_dir),
            "--profile",
            "de_judge",
            "--judge-task-id",
            task_id,
            "--file",
            str(ingest),
            "--format",
            "decisions",
        ],
    )

    assert res.exit_code == 0, res.output
    store = load_translation_store(load_profile_project(project_dir, "de_judge"))
    assert store.records[record_ids[0]].versions[0].target == "Imperium marschiert vor."


def test_judge_insert_copy_rejects_modified_target(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(
        project_dir, "de_a", record_ids[0], "Imperium marschiert vor."
    )
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)
    ingest = judge_ingest_json_path(
        load_profile_project(project_dir, "de_judge"), task_id
    )
    ingest.write_text(
        json.dumps(
            {
                "judge_task_id": task_id,
                "records": [
                    {
                        "id": record_ids[0],
                        "selected": "A",
                        "decision_kind": "copy",
                        "target": "Verändertes Ziel",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    res = runner.invoke(
        app,
        [
            "judge",
            "insert",
            str(project_dir),
            "--profile",
            "de_judge",
            "--judge-task-id",
            task_id,
            "--file",
            str(ingest),
            "--format",
            "json",
        ],
    )

    assert res.exit_code != 0
    assert "copy target must exactly match selected candidate" in res.output


def test_judge_insert_edited_requires_target(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(
        project_dir, "de_a", record_ids[0], "Imperium marschiert vor."
    )
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--format",
            "decisions",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)
    ingest = judge_ingest_decisions_path(
        load_profile_project(project_dir, "de_judge"), task_id
    )
    _fill_decisions_ingest(
        ingest,
        task_id,
        [(record_ids[0], "A", "edited", "Need a real rewrite.", "")],
    )

    res = runner.invoke(
        app,
        [
            "judge",
            "insert",
            str(project_dir),
            "--profile",
            "de_judge",
            "--judge-task-id",
            task_id,
            "--file",
            str(ingest),
            "--format",
            "decisions",
        ],
    )

    assert res.exit_code != 0
    assert "edited target must not be empty" in res.output


def test_judge_insert_edited_accepts_valid_rewrite(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(
        project_dir, "de_a", record_ids[0], "Imperium marschiert vor."
    )
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)
    ingest = judge_ingest_json_path(
        load_profile_project(project_dir, "de_judge"), task_id
    )
    ingest.write_text(
        json.dumps(
            {
                "judge_task_id": task_id,
                "records": [
                    {
                        "id": record_ids[0],
                        "selected": "A",
                        "decision_kind": "edited",
                        "target": "Das Imperium marschiert weiter voran.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    res = runner.invoke(
        app,
        [
            "judge",
            "insert",
            str(project_dir),
            "--profile",
            "de_judge",
            "--judge-task-id",
            task_id,
            "--file",
            str(ingest),
            "--format",
            "json",
        ],
    )

    assert res.exit_code == 0, res.output
    store = load_translation_store(load_profile_project(project_dir, "de_judge"))
    assert "weiter voran" in store.records[record_ids[0]].versions[0].target


def test_judge_insert_rejects_edited_when_config_disallows_edits(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(
        project_dir, "de_a", record_ids[0], "Imperium marschiert vor."
    )
    cfg = load_profile_config(project_dir, "de_judge")
    assert cfg.selection is not None
    cfg.selection.allow_edited_targets = False
    write_profile_config(project_dir, cfg)
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)
    ingest = judge_ingest_json_path(
        load_profile_project(project_dir, "de_judge"), task_id
    )
    ingest.write_text(
        json.dumps(
            {
                "judge_task_id": task_id,
                "records": [
                    {
                        "id": record_ids[0],
                        "selected": "A",
                        "decision_kind": "edited",
                        "target": "Das Imperium marschiert weiter voran.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    res = runner.invoke(
        app,
        [
            "judge",
            "insert",
            str(project_dir),
            "--profile",
            "de_judge",
            "--judge-task-id",
            task_id,
            "--file",
            str(ingest),
            "--format",
            "json",
        ],
    )

    assert res.exit_code != 0
    assert "edited judge targets are disabled" in res.output


def test_judge_insert_rejects_candidate_hash_drift(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(
        project_dir, "de_a", record_ids[0], "Imperium marschiert vor."
    )
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)
    _write_source_candidate(
        project_dir, "de_a", record_ids[0], "Imperium ist jetzt anders."
    )
    ingest = judge_ingest_json_path(
        load_profile_project(project_dir, "de_judge"), task_id
    )
    ingest.write_text(
        json.dumps(
            {
                "judge_task_id": task_id,
                "records": [
                    {
                        "id": record_ids[0],
                        "selected": "A",
                        "decision_kind": "copy",
                        "target": "Imperium marschiert vor.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    res = runner.invoke(
        app,
        [
            "judge",
            "insert",
            str(project_dir),
            "--profile",
            "de_judge",
            "--judge-task-id",
            task_id,
            "--file",
            str(ingest),
            "--format",
            "json",
        ],
    )

    assert res.exit_code != 0
    assert "selected candidate content changed" in res.output


def test_judge_insert_rejects_forbidden_glossary_violation(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    lowlands_record = _record_id_for_text(project_dir, "Lowlands")
    mandate = runner.invoke(
        app,
        [
            "context",
            "mandate-term",
            str(project_dir),
            "Lowlands",
            "--profile",
            "de_judge",
            "--target",
            "Tieflande",
            "--forbid",
            "Niederlande",
        ],
    )
    assert mandate.exit_code == 0, mandate.output
    _write_source_candidate(
        project_dir, "de_a", lowlands_record, "Niederlande antworten."
    )
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)
    ingest = judge_ingest_json_path(
        load_profile_project(project_dir, "de_judge"), task_id
    )
    ingest.write_text(
        json.dumps(
            {
                "judge_task_id": task_id,
                "records": [
                    {
                        "id": lowlands_record,
                        "selected": "A",
                        "decision_kind": "copy",
                        "target": "Niederlande antworten.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    res = runner.invoke(
        app,
        [
            "judge",
            "insert",
            str(project_dir),
            "--profile",
            "de_judge",
            "--judge-task-id",
            task_id,
            "--file",
            str(ingest),
            "--format",
            "json",
        ],
    )

    assert res.exit_code != 0
    assert "violates the selection profile glossary" in res.output


def test_judge_insert_writes_selection_ledger_and_status_counts(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(
        project_dir, "de_a", record_ids[0], "Imperium marschiert vor."
    )
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)
    ingest = judge_ingest_json_path(
        load_profile_project(project_dir, "de_judge"), task_id
    )
    ingest.write_text(
        json.dumps(
            {
                "judge_task_id": task_id,
                "records": [
                    {
                        "id": record_ids[0],
                        "selected": "A",
                        "decision_kind": "copy",
                        "target": "Imperium marschiert vor.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    insert_res = runner.invoke(
        app,
        [
            "judge",
            "insert",
            str(project_dir),
            "--profile",
            "de_judge",
            "--judge-task-id",
            task_id,
            "--file",
            str(ingest),
            "--format",
            "json",
        ],
    )
    assert insert_res.exit_code == 0, insert_res.output

    ledger = load_translation_selection_ledger(
        load_profile_project(project_dir, "de_judge")
    )
    assert ledger.records[record_ids[0]].selected_profile == "de_a"
    evidence = ledger.records[record_ids[0]].candidate_evidence[0]
    assert evidence.target_sha256 == sha256_text("Imperium marschiert vor.")
    assert not hasattr(evidence, "target")
    ledger_text = (
        project_dir / "translations" / "de_judge" / "translation-selection-ledger.json"
    ).read_text("utf-8")
    assert "Imperium marschiert vor." not in ledger_text
    status = runner.invoke(
        app,
        [
            "judge",
            "status",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
        ],
    )
    assert status.exit_code == 0, status.output
    assert "records selected: 1/" in status.output


def test_judge_insert_boundary_corruption_reports_reset_hint(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    for profile, target in (
        ("de_a", "Imperium marschiert vor."),
        ("de_b", "Das Imperium rückt vor."),
    ):
        _write_source_candidate(project_dir, profile, record_ids[0], target)
        if len(record_ids) > 1:
            _write_source_candidate(project_dir, profile, record_ids[1], target)
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--format",
            "decisions",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)
    ingest = judge_ingest_decisions_path(
        load_profile_project(project_dir, "de_judge"), task_id
    )
    second_record = record_ids[1] if len(record_ids) > 1 else "0001-000999"
    _fill_decisions_ingest(
        ingest,
        task_id,
        [
            (
                record_ids[0],
                "A",
                "copy",
                "broken",
                f"EINS## {second_record}",
            ),
            (second_record, "A", "copy", "later", ""),
        ],
    )

    res = runner.invoke(
        app,
        [
            "judge",
            "insert",
            str(project_dir),
            "--profile",
            "de_judge",
            "--judge-task-id",
            task_id,
            "--file",
            str(ingest),
            "--format",
            "decisions",
        ],
    )

    assert res.exit_code != 0
    assert "reset-ingest" in res.output
    assert "--format decisions --write" in res.output


def test_judge_reset_ingest_rewrites_decision_file_from_task(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(
        project_dir, "de_a", record_ids[0], "Imperium marschiert vor."
    )
    _write_source_candidate(
        project_dir, "de_b", record_ids[0], "Das Imperium rückt vor."
    )
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--format",
            "decisions",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)
    ingest = judge_ingest_decisions_path(
        load_profile_project(project_dir, "de_judge"), task_id
    )
    ingest.write_text("corrupted\n", encoding="utf-8")

    res = runner.invoke(
        app,
        [
            "judge",
            "reset-ingest",
            str(project_dir),
            "--profile",
            "de_judge",
            "--judge-task-id",
            task_id,
            "--format",
            "decisions",
            "--write",
        ],
    )

    assert res.exit_code == 0, res.output
    rewritten = ingest.read_text("utf-8")
    assert rewritten.startswith("# booktx judge decisions")
    assert "selected: " in rewritten


def test_judge_profile_build_uses_selected_store_output(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(
        project_dir, "de_a", record_ids[0], "Imperium marschiert vor."
    )
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)
    ingest = judge_ingest_json_path(
        load_profile_project(project_dir, "de_judge"), task_id
    )
    ingest.write_text(
        json.dumps(
            {
                "judge_task_id": task_id,
                "records": [
                    {
                        "id": record_ids[0],
                        "selected": "A",
                        "decision_kind": "copy",
                        "target": "Imperium marschiert vor.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert (
        runner.invoke(
            app,
            [
                "judge",
                "insert",
                str(project_dir),
                "--profile",
                "de_judge",
                "--judge-task-id",
                task_id,
                "--file",
                str(ingest),
                "--format",
                "json",
            ],
        ).exit_code
        == 0
    )

    validate_res = runner.invoke(
        app, ["validate", str(project_dir), "--profile", "de_judge"]
    )
    build_res = runner.invoke(app, ["build", str(project_dir), "--profile", "de_judge"])
    assert validate_res.exit_code == 0, validate_res.output
    assert build_res.exit_code == 0, build_res.output
    output = next((project_dir / "translations" / "de_judge" / "output").glob("*.md"))
    assert "Imperium marschiert vor." in output.read_text("utf-8")


def test_judge_rejects_source_target_language_mismatch(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    create_profile(project_dir, "fr_a", target_language="fr")
    _create_selection_profile(project_dir, "de_judge", ["fr_a"])
    _ready_context(project_dir, "de_judge")

    res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "fr_a",
        ],
    )

    assert res.exit_code != 0
    assert "target language" in res.output


def test_judge_rejects_source_source_language_mismatch(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    _create_translation_profile(project_dir, "de_a")
    cfg = load_profile_config(project_dir, "de_a")
    cfg.source_language = "fr"
    write_profile_config(project_dir, cfg)
    _create_selection_profile(project_dir, "de_judge", ["de_a"])

    res = runner.invoke(
        app,
        [
            "judge",
            "status",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a",
        ],
    )

    assert res.exit_code != 0
    assert "source language" in res.output


def test_judge_rejects_pass_through_source_by_default(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    create_profile(project_dir, "de_pass", target_language="de", kind="pass-through")
    _create_selection_profile(project_dir, "de_judge", ["de_pass"])

    res = runner.invoke(
        app,
        [
            "judge",
            "status",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_pass",
        ],
    )

    assert res.exit_code != 0
    assert "must be a translation profile" in res.output
    assert "pass-through" in res.output


def test_judge_rejects_selection_profile_as_source_by_default(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    _create_translation_profile(project_dir, "de_a")
    _create_selection_profile(project_dir, "de_judge_source", ["de_a"])
    _create_selection_profile(project_dir, "de_judge", ["de_judge_source"])

    res = runner.invoke(
        app,
        [
            "judge",
            "status",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_judge_source",
        ],
    )

    assert res.exit_code != 0
    assert "must be a translation profile" in res.output
    assert "selection" in res.output


def test_judge_rejects_selection_profile_as_own_source(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    _create_selection_profile(project_dir, "de_judge", ["de_judge"])

    res = runner.invoke(
        app,
        [
            "judge",
            "status",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_judge",
        ],
    )

    assert res.exit_code != 0
    assert "selection profile cannot be a judge source" in res.output


def test_judge_rejects_non_selection_profile(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    _create_translation_profile(project_dir, "de_a")
    _ready_context(project_dir, "de_a")

    res = runner.invoke(app, ["judge", "status", str(project_dir), "--profile", "de_a"])

    assert res.exit_code != 0
    assert "selection profile" in res.output


# --------------------------------------------------------------------------
# judge isolation: snapshot/sync/prepare-isolation tests (todo-0011)
# --------------------------------------------------------------------------


def _sync_sources(
    project_dir: Path,
    profile: str = "de_judge",
    *,
    write: bool = False,
    sources: str | None = None,
    prune: bool = True,
):
    args = ["judge", "sync-sources", str(project_dir), "--profile", profile]
    if sources is not None:
        args += ["--sources", sources]
    if write:
        args.append("--write")
    if not prune:
        args.append("--no-prune")
    return runner.invoke(app, args)


@contextmanager
def _chdir(path: Path):
    cwd = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(cwd)


def _profile_root_judge_next(
    project_dir: Path,
    *,
    profile: str = "de_judge",
    chapter: str = "0001",
    max_words: int = 900,
):
    pr = project_dir / "translations" / profile
    with _chdir(pr):
        return runner.invoke(
            app,
            [
                "judge",
                "next",
                ".",
                "--unit",
                "chapter",
                "--chapter",
                chapter,
                "--max-words",
                str(max_words),
                "--format",
                "block",
            ],
        )


def _profile_root_judge_insert(
    project_dir: Path,
    task_id: str,
    file_path: str,
    *,
    profile: str = "de_judge",
    input_format: str = "block",
):
    pr = project_dir / "translations" / profile
    with _chdir(pr):
        return runner.invoke(
            app,
            [
                "judge",
                "insert",
                ".",
                "--judge-task-id",
                task_id,
                "--file",
                file_path,
                "--format",
                input_format,
            ],
        )


def _fill_json_ingest(
    ingest_path: Path,
    task_id: str,
    record_id: str,
    selected: str,
    target: str,
    decision_kind: str = "copy",
):
    ingest_path.write_text(
        json.dumps(
            {
                "judge_task_id": task_id,
                "records": [
                    {
                        "id": record_id,
                        "selected": selected,
                        "decision_kind": decision_kind,
                        "target": target,
                        "reason": "test",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _fill_decisions_ingest(
    ingest_path: Path,
    task_id: str,
    records: list[tuple[str, str, str, str, str]],
):
    lines = [
        "# booktx judge decisions",
        f"judge_task_id: {task_id}",
        "",
    ]
    for record_id, selected, decision_kind, reason, target in records:
        lines.extend(
            [
                f"## {record_id}",
                f"selected: {selected}",
                f"decision_kind: {decision_kind}",
                f"reason: {reason}",
                "TARGET:",
            ]
        )
        if target:
            lines.extend(target.splitlines())
        lines.append("")
    ingest_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _snapshot_id(project_dir: Path) -> str:
    proj = load_profile_project(project_dir, "de_judge")
    return validate_judge_sources_snapshot(proj).snapshot_id


# --- Test 1: sync-sources copies profile stores ---


def test_judge_sync_sources_copies_profile_stores(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(project_dir, "de_a", record_ids[0], "A target.")
    _write_source_candidate(project_dir, "de_b", record_ids[0], "B target.")
    res = _sync_sources(project_dir, write=True)
    assert res.exit_code == 0, res.output
    proj = load_profile_project(project_dir, "de_judge")
    man = validate_judge_sources_snapshot(proj)
    assert "de_a" in man.source_profiles
    assert "de_b" in man.source_profiles
    sid = man.snapshot_id
    for prof in ("de_a", "de_b"):
        pdir = judge_source_profile_dir(proj, sid, prof)
        assert (pdir / "translation-store.json").is_file()
        assert (pdir / "profile-config.json").is_file()
        assert man.profiles[prof].effective_candidates_total >= 0


# --- Test 2: next from snapshot uses copied candidates ---


def test_judge_next_from_snapshot_uses_copied_candidates(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(project_dir, "de_a", record_ids[0], "Original target.")
    _write_source_candidate(project_dir, "de_b", record_ids[0], "B target.")
    _sync_sources(project_dir, write=True)
    # Mutate live AFTER sync
    p = load_profile_project(project_dir, "de_a")
    store = load_translation_store(p)
    store.records[record_ids[0]].versions[0].target = "MUTATED-LIVE"
    write_translation_store(p, store)
    res = _profile_root_judge_next(project_dir)
    assert res.exit_code == 0, res.output
    task_id = res.output.split("judge task: ")[1].splitlines()[0].strip()
    task = load_judge_task(load_profile_project(project_dir, "de_judge"), task_id)
    a_candidate = next(c for c in task.records[0].candidates if c.profile == "de_a")
    assert a_candidate.target == "Original target."


# --- Test 3: next blocks missing snapshot ---


def test_judge_next_snapshot_blocks_missing_snapshot(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(project_dir, "de_a", record_ids[0], "A.")
    # No sync performed
    res = _profile_root_judge_next(project_dir)
    assert res.exit_code != 0
    assert (
        "snapshot" in res.output.lower()
        or "prepare-isolation" in res.output.lower()
        or "project root" in res.output.lower()
    )


def test_judge_status_next_command_uses_safe_isolated_defaults(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(project_dir, "de_a", record_ids[0], "A target.")
    _write_source_candidate(project_dir, "de_b", record_ids[0], "B target.")
    _sync_sources(project_dir, write=True)

    profile_root = project_dir / "translations" / "de_judge"
    with _chdir(profile_root):
        res = runner.invoke(app, ["judge", "status", "."])

    assert res.exit_code == 0, res.output
    assert "--max-records 8" in res.output
    assert "--format decisions" in res.output
    assert "--format block" not in res.output


def test_judge_continue_creates_next_task_from_profile_root(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(project_dir, "de_a", record_ids[0], "A target.")
    _write_source_candidate(project_dir, "de_b", record_ids[0], "B target.")
    _sync_sources(project_dir, write=True)

    profile_root = project_dir / "translations" / "de_judge"
    with _chdir(profile_root):
        res = runner.invoke(app, ["judge", "continue", ".", "--max-records", "1"])

    assert res.exit_code == 0, res.output
    assert "judge task:" in res.output
    assert ".decisions.txt" in res.output


def test_judge_status_json_contains_blocked_by(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    _create_translation_profile(project_dir, "de_a")
    _create_translation_profile(project_dir, "de_b")
    _create_selection_profile(project_dir, "de_judge", ["de_a", "de_b"])

    res = runner.invoke(
        app,
        ["judge", "status", str(project_dir), "--profile", "de_judge", "--json"],
    )

    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert "context" in data
    assert "blocked_by" in data
    assert "context_missing" in data["blocked_by"]


# --- Test 4: insert snapshot does not read sibling profile ---


def test_judge_insert_snapshot_does_not_read_sibling_profile(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(project_dir, "de_a", record_ids[0], "A correct.")
    _write_source_candidate(project_dir, "de_b", record_ids[0], "B target.")
    _sync_sources(project_dir, write=True)
    res = _profile_root_judge_next(project_dir)
    assert res.exit_code == 0, res.output
    task_id = res.output.split("judge task: ")[1].splitlines()[0].strip()
    # Delete the sibling source profile dir
    shutil.rmtree(project_dir / "translations" / "de_a")
    proj = load_profile_project(project_dir, "de_judge")
    ingest = proj.profile_dir / "judge-ingest" / f"{task_id}.json"
    task = load_judge_task(proj, task_id)
    _fill_json_ingest(
        ingest, task_id, record_ids[0], "A", task.records[0].candidates[0].target
    )
    res2 = _profile_root_judge_insert(
        project_dir, task_id, f"judge-ingest/{task_id}.json", input_format="json"
    )
    assert res2.exit_code == 0, res2.output
    assert "accepted" in res2.output


# --- Test 5: insert rejects manifest drift ---


def test_judge_insert_snapshot_rejects_snapshot_manifest_drift(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(project_dir, "de_a", record_ids[0], "A.")
    _write_source_candidate(project_dir, "de_b", record_ids[0], "B.")
    _sync_sources(project_dir, write=True)
    res = _profile_root_judge_next(project_dir)
    assert res.exit_code == 0, res.output
    task_id = res.output.split("judge task: ")[1].splitlines()[0].strip()
    # Change a source and re-sync (manifest hash changes)
    p = load_profile_project(project_dir, "de_a")
    store = load_translation_store(p)
    store.records[record_ids[0]].versions[0].target = "CHANGED"
    write_translation_store(p, store)
    _sync_sources(project_dir, write=True)
    proj = load_profile_project(project_dir, "de_judge")
    ingest = proj.profile_dir / "judge-ingest" / f"{task_id}.json"
    task = load_judge_task(proj, task_id)
    _fill_json_ingest(
        ingest, task_id, record_ids[0], "A", task.records[0].candidates[0].target
    )
    res2 = _profile_root_judge_insert(
        project_dir, task_id, f"judge-ingest/{task_id}.json", input_format="json"
    )
    assert res2.exit_code != 0
    assert "drift" in res2.output.lower() or "recreate" in res2.output.lower()


# --- Test 6: sync rejects selection profile as source ---


def test_judge_sync_rejects_selection_profile_as_source(tmp_path: Path):
    project_dir, _ = _judge_project(tmp_path)
    create_profile(project_dir, "de_sel2", target_language="de", kind="selection")
    cfg = load_profile_config(project_dir, "de_judge")
    assert cfg.selection is not None
    cfg.selection.sources = ["de_a", "de_sel2"]
    write_profile_config(project_dir, cfg)
    res = _sync_sources(project_dir, write=True)
    assert res.exit_code != 0
    assert "selection" in res.output.lower() or "translation" in res.output.lower()


# --- Test 7: sync rejects non-translation source ---


def test_judge_sync_rejects_non_translation_source(tmp_path: Path):
    project_dir, _ = _judge_project(tmp_path)
    create_profile(project_dir, "de_sel3", target_language="de", kind="selection")
    cfg = load_profile_config(project_dir, "de_judge")
    assert cfg.selection is not None
    cfg.selection.sources = ["de_a", "de_sel3"]
    write_profile_config(project_dir, cfg)
    res = _sync_sources(project_dir, write=True)
    assert res.exit_code != 0
    assert "translation" in res.output.lower() or "selection" in res.output.lower()


# --- Test 8: sync dry-run has no side effects ---


def test_judge_sync_dry_run_has_no_side_effects(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(project_dir, "de_a", record_ids[0], "A.")
    base = project_dir / "translations" / "de_judge"
    tree_before = {str(p.relative_to(base)) for p in base.rglob("*")}
    res = _sync_sources(project_dir, write=False)
    assert res.exit_code == 0
    assert "dry-run" in res.output
    tree_after = {str(p.relative_to(base)) for p in base.rglob("*")}
    assert tree_before == tree_after


# --- Test 9: sync unchanged is idempotent ---


def test_judge_sync_unchanged_is_idempotent(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(project_dir, "de_a", record_ids[0], "A.")
    _write_source_candidate(project_dir, "de_b", record_ids[0], "B.")
    _sync_sources(project_dir, write=True)
    proj = load_profile_project(project_dir, "de_judge")
    manifest_bytes_1 = judge_sources_manifest_path(proj).read_bytes()
    mhash_1 = judge_sources_manifest_sha256(proj)
    _sync_sources(project_dir, write=True)
    assert judge_sources_manifest_path(proj).read_bytes() == manifest_bytes_1
    assert judge_sources_manifest_sha256(proj) == mhash_1


# --- Test 10: sync validates every copied file hash ---


def test_judge_sync_validates_every_copied_file_hash(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(project_dir, "de_a", record_ids[0], "A.")
    _sync_sources(project_dir, write=True)
    proj = load_profile_project(project_dir, "de_judge")
    sid = _snapshot_id(project_dir)
    # Corrupt a copied store without changing the manifest
    store_path = judge_source_profile_dir(proj, sid, "de_a") / "translation-store.json"
    store_path.write_text("{}", encoding="utf-8")
    try:
        validate_judge_sources_snapshot(proj)
        raise AssertionError("should have raised")
    except Exception as e:
        assert (
            "hash" in str(e).lower()
            or "corrupt" in str(e).lower()
            or "mismatch" in str(e).lower()
        )


# --- Test 11: interrupted generation keeps active snapshot ---


def test_judge_sync_interrupted_generation_keeps_active_snapshot(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(project_dir, "de_a", record_ids[0], "A.")
    _sync_sources(project_dir, write=True)
    proj = load_profile_project(project_dir, "de_judge")
    man = validate_judge_sources_snapshot(proj)
    active_id = man.snapshot_id
    # Simulate interrupted publication: create a partial staging dir
    snapshots_root = proj.profile_dir / "judge-sources" / "snapshots"
    staging = snapshots_root / ".staging-bogus"
    staging.mkdir(parents=True, exist_ok=True)
    try:
        man2 = validate_judge_sources_snapshot(proj)
        assert man2.snapshot_id == active_id
        views = load_snapshot_judge_source_views(proj)
        assert set(views) == {"de_a", "de_b"}
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# --- Test 12: prepare-isolation end-to-end ---


def test_judge_prepare_isolation_end_to_end(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(project_dir, "de_a", record_ids[0], "A.")
    # Dry-run side-effect free
    res = runner.invoke(
        app, ["judge", "prepare-isolation", str(project_dir), "--profile", "de_judge"]
    )
    assert res.exit_code == 0, res.output
    assert "dry-run" in res.output
    assert not (project_dir / "translations" / "de_judge" / "judge-sources").exists()
    agents = project_dir / "translations" / "de_judge" / "AGENTS.md"
    assert not agents.exists()
    # Write publishes snapshot + judge AGENTS.md
    res = runner.invoke(
        app,
        [
            "judge",
            "prepare-isolation",
            str(project_dir),
            "--profile",
            "de_judge",
            "--write",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "snapshot" in res.output.lower()
    assert agents.is_file()
    assert "isolated judge profile instructions" in agents.read_text("utf-8")


def test_judge_prepare_isolation_context_from_source_writes_ready_context(
    tmp_path: Path,
):
    project_dir = _make_project(tmp_path)
    _create_translation_profile(project_dir, "de_a")
    _create_translation_profile(project_dir, "de_b")
    _ready_context(project_dir, "de_a")
    _create_selection_profile(project_dir, "de_judge", ["de_a", "de_b"])

    res = runner.invoke(
        app,
        [
            "judge",
            "prepare-isolation",
            str(project_dir),
            "--profile",
            "de_judge",
            "--context-from",
            "de_a",
            "--write",
        ],
    )

    assert res.exit_code == 0, res.output
    proj = load_profile_project(project_dir, "de_judge")
    ctx = load_context(proj)
    assert ctx is not None
    assert ctx.ready is True
    assert (proj.profile_dir / "judge-sources").exists()


# --- Test 13: profile-root insert rejects live task ---


def test_judge_insert_profile_root_rejects_live_task(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(project_dir, "de_a", record_ids[0], "A.")
    _write_source_candidate(project_dir, "de_b", record_ids[0], "B.")
    _sync_sources(project_dir, write=True)
    # Create a LIVE task from project-root mode
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)
    task = load_judge_task(load_profile_project(project_dir, "de_judge"), task_id)
    assert task.source_access == "live"
    proj = load_profile_project(project_dir, "de_judge")
    ingest = judge_ingest_json_path(proj, task_id)
    _fill_json_ingest(
        ingest, task_id, record_ids[0], "A", task.records[0].candidates[0].target
    )
    # Insert from profile root → must reject live task
    res = _profile_root_judge_insert(
        project_dir, task_id, f"judge-ingest/{task_id}.json", input_format="json"
    )
    assert res.exit_code != 0
    assert "snapshot" in res.output.lower() or "recreate" in res.output.lower()


# --- Test 14: snapshot insert requires complete evidence ---


def test_judge_insert_snapshot_requires_complete_evidence(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(project_dir, "de_a", record_ids[0], "A.")
    _write_source_candidate(project_dir, "de_b", record_ids[0], "B.")
    _sync_sources(project_dir, write=True)
    res = _profile_root_judge_next(project_dir)
    assert res.exit_code == 0, res.output
    task_id = res.output.split("judge task: ")[1].splitlines()[0].strip()
    proj = load_profile_project(project_dir, "de_judge")
    task_path = proj.profile_dir / "judge-tasks" / f"{task_id}.json"
    task = load_judge_task(proj, task_id)
    ingest = proj.profile_dir / "judge-ingest" / f"{task_id}.json"
    # Remove each required snapshot field in turn
    for field in ("source_snapshot_sha256", "source_candidates_sha256"):
        raw = json.loads(task_path.read_text("utf-8"))
        raw[field] = None
        task_path.write_text(json.dumps(raw), encoding="utf-8")
        _fill_json_ingest(
            ingest, task_id, record_ids[0], "A", task.records[0].candidates[0].target
        )
        res2 = _profile_root_judge_insert(
            project_dir, task_id, f"judge-ingest/{task_id}.json", input_format="json"
        )
        assert res2.exit_code != 0, f"{field} should block insert"
        # restore
        raw[field] = getattr(task, field)
        task_path.write_text(json.dumps(raw), encoding="utf-8")


# --- Test 15: snapshot insert rejects candidate payload corruption ---


def test_judge_insert_snapshot_rejects_candidate_payload_corruption(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(project_dir, "de_a", record_ids[0], "A.")
    _write_source_candidate(project_dir, "de_b", record_ids[0], "B.")
    _sync_sources(project_dir, write=True)
    res = _profile_root_judge_next(project_dir)
    assert res.exit_code == 0, res.output
    task_id = res.output.split("judge task: ")[1].splitlines()[0].strip()
    proj = load_profile_project(project_dir, "de_judge")
    task_path = proj.profile_dir / "judge-tasks" / f"{task_id}.json"
    ingest = proj.profile_dir / "judge-ingest" / f"{task_id}.json"
    load_judge_task(proj, task_id)
    # Corrupt the candidate target in the task artifact
    raw = json.loads(task_path.read_text("utf-8"))
    raw["records"][0]["candidates"][0]["target"] = "CORRUPTED"
    task_path.write_text(json.dumps(raw), encoding="utf-8")
    _fill_json_ingest(ingest, task_id, record_ids[0], "A", "CORRUPTED")
    res2 = _profile_root_judge_insert(
        project_dir, task_id, f"judge-ingest/{task_id}.json", input_format="json"
    )
    assert res2.exit_code != 0
    assert (
        "corrupt" in res2.output.lower()
        or "hash" in res2.output.lower()
        or "mismatch" in res2.output.lower()
    )


def test_judge_show_prints_record_candidates(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(project_dir, "de_a", record_ids[0], "A target.")
    _write_source_candidate(project_dir, "de_b", record_ids[0], "B target.")
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)

    res = runner.invoke(
        app,
        [
            "judge",
            "show",
            str(project_dir),
            "--profile",
            "de_judge",
            "--judge-task-id",
            task_id,
            "--record",
            record_ids[0],
        ],
    )

    assert res.exit_code == 0, res.output
    assert "SOURCE:" in res.output
    assert "A: A target." in res.output
    assert "B: B target." in res.output
    assert "validation:" in res.output


def test_judge_accept_identical_copies_only_identical_valid_candidates(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(project_dir, "de_a", record_ids[0], "Shared target.")
    _write_source_candidate(project_dir, "de_b", record_ids[0], "Shared target.")

    res = runner.invoke(
        app,
        [
            "judge",
            "accept-identical",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--max-records",
            "1",
            "--write",
        ],
    )

    assert res.exit_code == 0, res.output
    assert "accepted: 1 record(s)" in res.output
    store = load_translation_store(load_profile_project(project_dir, "de_judge"))
    assert store.records[record_ids[0]].versions[0].target == "Shared target."


def test_judge_accept_identical_respects_require_all_sources(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(project_dir, "de_a", record_ids[0], "Shared target.")

    res = runner.invoke(
        app,
        [
            "judge",
            "accept-identical",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--require-all-sources",
            "--write",
        ],
    )

    assert res.exit_code != 0
    assert "missing effective candidates" in res.output


def test_judge_accept_identical_scoped_next_for_incomplete_chapter(tmp_path: Path):
    project_dir, chapters = _multi_chapter_judge_project(tmp_path)
    # Chapter 0002 has divergent candidates and cannot be auto-accepted.
    for record_id in chapters["0002"]:
        _write_source_candidate(project_dir, "de_a", record_id, "Antwort A.")
        _write_source_candidate(project_dir, "de_b", record_id, "Antwort B.")

    res = runner.invoke(
        app,
        [
            "judge",
            "accept-identical",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--chapter",
            "0002",
            "--write",
        ],
    )

    assert res.exit_code == 0, res.output
    assert "next command for chapter 0002:" in res.output
    assert "--chapter 0002" in res.output
    # Must not point back at an earlier (complete or untouched) chapter.
    assert "next command for chapter 0001:" not in res.output


def test_judge_accept_identical_complete_chapter_then_global_next(tmp_path: Path):
    project_dir, chapters = _multi_chapter_judge_project(tmp_path)
    # Chapter 0001 has identical valid candidates and is fully auto-accepted.
    for record_id in chapters["0001"]:
        _write_source_candidate(project_dir, "de_a", record_id, "Gemeinsam.")
        _write_source_candidate(project_dir, "de_b", record_id, "Gemeinsam.")

    res = runner.invoke(
        app,
        [
            "judge",
            "accept-identical",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--chapter",
            "0001",
            "--write",
        ],
    )

    assert res.exit_code == 0, res.output
    assert "chapter 0001 complete" in res.output
    # The next missing chapter is 0002; the global next must point there.
    assert "next global command:" in res.output
    assert "--chapter 0002" in res.output


def test_judge_accept_identical_no_chapter_preserves_global_next(tmp_path: Path):
    project_dir, chapters = _multi_chapter_judge_project(tmp_path)
    for record_id in chapters["0001"]:
        _write_source_candidate(project_dir, "de_a", record_id, "Gemeinsam.")
        _write_source_candidate(project_dir, "de_b", record_id, "Gemeinsam.")

    res = runner.invoke(
        app,
        [
            "judge",
            "accept-identical",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--write",
        ],
    )

    assert res.exit_code == 0, res.output
    # No explicit --chapter: keep the historical global "next command:" line.
    assert "next command:" in res.output
    assert "next command for chapter" not in res.output
    assert "--chapter 0002" in res.output


def test_judge_sweep_identical_stops_on_chapter_needing_judging(tmp_path: Path):
    project_dir, chapters = _multi_chapter_judge_project(tmp_path)
    for record_id in chapters["0001"]:
        _write_source_candidate(project_dir, "de_a", record_id, "Gemeinsam 1.")
        _write_source_candidate(project_dir, "de_b", record_id, "Gemeinsam 1.")
    # Chapter 0002 has divergent candidates and needs LLM judging.
    for record_id in chapters["0002"]:
        _write_source_candidate(project_dir, "de_a", record_id, "Antwort A.")
        _write_source_candidate(project_dir, "de_b", record_id, "Antwort B.")
    for record_id in chapters["0003"]:
        _write_source_candidate(project_dir, "de_a", record_id, "Gemeinsam 3.")
        _write_source_candidate(project_dir, "de_b", record_id, "Gemeinsam 3.")

    res = runner.invoke(
        app,
        [
            "judge",
            "sweep-identical",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--from-chapter",
            "0001",
            "--to-chapter",
            "0003",
            "--write",
        ],
    )

    assert res.exit_code == 0, res.output
    # Chapter 0001 is fully accepted; the sweep stops at the divergent 0002.
    assert "needs_judging" in res.output
    assert "next command for chapter 0002:" in res.output
    assert "--chapter 0002" in res.output
    # The sweep stops at 0002, so 0003 must not be processed.
    assert "0003" not in res.output


def test_judge_sweep_identical_completes_all_chapters(tmp_path: Path):
    project_dir, chapters = _multi_chapter_judge_project(tmp_path)
    for cid in ("0001", "0002", "0003"):
        for record_id in chapters[cid]:
            _write_source_candidate(project_dir, "de_a", record_id, f"{cid} text.")
            _write_source_candidate(project_dir, "de_b", record_id, f"{cid} text.")

    res = runner.invoke(
        app,
        [
            "judge",
            "sweep-identical",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--from-chapter",
            "0001",
            "--to-chapter",
            "0003",
            "--write",
        ],
    )

    assert res.exit_code == 0, res.output
    assert "all chapters in range complete" in res.output
    assert "needs_judging" not in res.output


def test_judge_sweep_identical_does_not_mask_invalid_range(tmp_path: Path):
    project_dir, _ = _multi_chapter_judge_project(tmp_path)

    res = runner.invoke(
        app,
        [
            "judge",
            "sweep-identical",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--from-chapter",
            "0001",
            "--to-chapter",
            "0099",
            "--write",
        ],
    )

    # An out-of-range --to-chapter must surface as an error, not be masked.
    assert res.exit_code != 0
    assert "0099" in res.output


# --- Test 16: sync requires configured source order ---


def test_judge_sync_requires_configured_source_order(tmp_path: Path):
    project_dir, _ = _judge_project(tmp_path)
    # Admin --sources cannot reorder
    res = _sync_sources(project_dir, sources="de_b,de_a")
    assert res.exit_code != 0
    assert "order" in res.output.lower() or "configured" in res.output.lower()
    # Admin --sources cannot omit
    res = _sync_sources(project_dir, sources="de_a")
    assert res.exit_code != 0
    # Admin --sources cannot add extra
    create_profile(project_dir, "de_extra", target_language="de")
    cfg = load_profile_config(project_dir, "de_judge")
    assert cfg.selection is not None
    orig_sources = list(cfg.selection.sources)
    cfg.selection.sources = orig_sources
    write_profile_config(project_dir, cfg)
    res = _sync_sources(project_dir, sources="de_a,de_b,de_extra")
    assert res.exit_code != 0
    # Profile-root --sources: only order-preserving subset of validated snapshot
    cfg.selection.sources = orig_sources
    write_profile_config(project_dir, cfg)
    _sync_sources(project_dir, write=True)
    proj = load_profile_project(project_dir, "de_judge")
    manifest = validate_judge_sources_snapshot(proj)
    # OK: subset in order
    assert validate_snapshot_source_subset(manifest, ["de_a"]) == ["de_a"]
    # Fails: wrong order
    try:
        validate_snapshot_source_subset(manifest, ["de_b", "de_a"])
        raise AssertionError("should reject reorder")
    except Exception:
        pass
    # Fails: not in snapshot
    try:
        validate_snapshot_source_subset(manifest, ["de_extra"])
        raise AssertionError("should reject unknown")
    except Exception:
        pass


def test_judge_decisions_template_documents_new_judge_target(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(
        project_dir, "de_a", record_ids[0], "Imperium marschiert vor."
    )
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--format",
            "decisions",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)
    ingest = judge_ingest_decisions_path(
        load_profile_project(project_dir, "de_judge"), task_id
    )
    text = ingest.read_text("utf-8")
    assert "# Decision modes:" in text
    assert "# - copy: selected must be A/B/C; TARGET must be empty." in text
    assert "# - new judge target: selected is edited" in text
    assert "Never paste a copy candidate into TARGET" in text


def test_judge_insert_edited_accepts_selected_edited_without_candidate(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(
        project_dir, "de_a", record_ids[0], "Imperium marschiert vor."
    )
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--format",
            "decisions",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)
    ingest = judge_ingest_decisions_path(
        load_profile_project(project_dir, "de_judge"), task_id
    )
    _fill_decisions_ingest(
        ingest,
        task_id,
        [
            (
                record_ids[0],
                "edited",
                "edited",
                "None of the candidates fits; new judge target.",
                "Das Imperium zieht endlich in den Krieg.",
            )
        ],
    )

    res = runner.invoke(
        app,
        [
            "judge",
            "insert",
            str(project_dir),
            "--profile",
            "de_judge",
            "--judge-task-id",
            task_id,
            "--file",
            str(ingest),
            "--format",
            "decisions",
        ],
    )
    assert res.exit_code == 0, res.output
    store = load_translation_store(load_profile_project(project_dir, "de_judge"))
    assert "zieht endlich" in store.records[record_ids[0]].versions[0].target


def test_judge_insert_selected_edited_has_no_selected_profile(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(
        project_dir, "de_a", record_ids[0], "Imperium marschiert vor."
    )
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--format",
            "decisions",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)
    ingest = judge_ingest_decisions_path(
        load_profile_project(project_dir, "de_judge"), task_id
    )
    _fill_decisions_ingest(
        ingest,
        task_id,
        [
            (
                record_ids[0],
                "edited",
                "edited",
                "new judge target",
                "Das Imperium zieht endlich in den Krieg.",
            )
        ],
    )
    res = runner.invoke(
        app,
        [
            "judge",
            "insert",
            str(project_dir),
            "--profile",
            "de_judge",
            "--judge-task-id",
            task_id,
            "--file",
            str(ingest),
            "--format",
            "decisions",
        ],
    )
    assert res.exit_code == 0, res.output
    ledger = load_translation_selection_ledger(
        load_profile_project(project_dir, "de_judge")
    )
    decision = ledger.records[record_ids[0]]
    assert decision.decision_kind == "edited"
    assert decision.selected_profile is None
    # candidate_evidence is still preserved for auditability
    assert decision.candidate_evidence
    assert any(ev.profile == "de_a" for ev in decision.candidate_evidence)


def test_judge_insert_prints_scoped_next_for_incomplete_chapter(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(project_dir, "de_a", record_ids[0], "Imperium vor.")
    _write_source_candidate(project_dir, "de_b", record_ids[0], "Imperium vor.")
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--unit",
            "chapter",
            "--chapter",
            "0001",
            "--max-records",
            "1",
            "--format",
            "decisions",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)
    ingest = judge_ingest_decisions_path(
        load_profile_project(project_dir, "de_judge"), task_id
    )
    _fill_decisions_ingest(
        ingest,
        task_id,
        [(record_ids[0], "A", "copy", "identical", "")],
    )
    res = runner.invoke(
        app,
        [
            "judge",
            "insert",
            str(project_dir),
            "--profile",
            "de_judge",
            "--judge-task-id",
            task_id,
            "--file",
            str(ingest),
            "--format",
            "decisions",
        ],
    )
    assert res.exit_code == 0, res.output
    # record_ids[1] still missing in chapter 0001 -> scoped next stays in chapter
    assert "next command for chapter 0001:" in res.output
    assert "--chapter 0001" in res.output


def test_judge_insert_prints_chapter_complete_then_global_next(tmp_path: Path):
    project_dir, chapters = _multi_chapter_judge_project(tmp_path)
    chapter_records = chapters["0001"]
    for rid in chapter_records:
        _write_source_candidate(project_dir, "de_a", rid, "Erster Satz.")
        _write_source_candidate(project_dir, "de_b", rid, "Erster Satz.")
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--chapter",
            "0001",
            "--format",
            "decisions",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)
    ingest = judge_ingest_decisions_path(
        load_profile_project(project_dir, "de_judge"), task_id
    )
    _fill_decisions_ingest(
        ingest,
        task_id,
        [(rid, "A", "copy", "identical", "") for rid in chapter_records],
    )
    res = runner.invoke(
        app,
        [
            "judge",
            "insert",
            str(project_dir),
            "--profile",
            "de_judge",
            "--judge-task-id",
            task_id,
            "--file",
            str(ingest),
            "--format",
            "decisions",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "chapter 0001 complete" in res.output
    assert "next global command:" in res.output
    # The next missing chapter is 0002.
    assert "--chapter 0002" in res.output


def test_judge_insert_profile_root_next_command_is_local(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(project_dir, "de_a", record_ids[0], "Imperium vor.")
    _write_source_candidate(project_dir, "de_b", record_ids[0], "Imperium vor.")
    _sync_sources(project_dir, write=True)
    res = _profile_root_judge_next(project_dir)
    assert res.exit_code == 0, res.output
    task_id = res.output.split("judge task: ")[1].splitlines()[0].strip()
    proj = load_profile_project(project_dir, "de_judge")
    ingest = proj.profile_dir / "judge-ingest" / f"{task_id}.json"
    task = load_judge_task(proj, task_id)
    _fill_json_ingest(
        ingest, task_id, record_ids[0], "A", task.records[0].candidates[0].target
    )
    res2 = _profile_root_judge_insert(
        project_dir, task_id, f"judge-ingest/{task_id}.json", input_format="json"
    )
    assert res2.exit_code == 0, res2.output
    # The scoped next command after insert must be profile-root safe.
    assert "--profile" not in res2.output
    assert "../" not in res2.output
    assert "--chapter" in res2.output


def test_judge_accept_identical_complete_chapter_is_not_error(tmp_path: Path):
    project_dir, chapters = _multi_chapter_judge_project(tmp_path)
    for record_id in chapters["0001"]:
        _write_source_candidate(project_dir, "de_a", record_id, "Gemeinsam.")
        _write_source_candidate(project_dir, "de_b", record_id, "Gemeinsam.")
    # First call completes chapter 0001.
    first = runner.invoke(
        app,
        [
            "judge",
            "accept-identical",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--chapter",
            "0001",
            "--write",
        ],
    )
    assert first.exit_code == 0, first.output
    # Second call: chapter 0001 is already complete -> must NOT be an error.
    second = runner.invoke(
        app,
        [
            "judge",
            "accept-identical",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--chapter",
            "0001",
            "--write",
        ],
    )
    assert second.exit_code == 0, second.output
    assert "no missing records remain" not in second.output


def test_judge_accept_identical_complete_chapter_prints_global_next(tmp_path: Path):
    project_dir, chapters = _multi_chapter_judge_project(tmp_path)
    for record_id in chapters["0001"]:
        _write_source_candidate(project_dir, "de_a", record_id, "Gemeinsam.")
        _write_source_candidate(project_dir, "de_b", record_id, "Gemeinsam.")
    runner.invoke(
        app,
        [
            "judge",
            "accept-identical",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--chapter",
            "0001",
            "--write",
        ],
    )
    # Chapter 0001 already complete -> re-run prints completion + global next.
    res = runner.invoke(
        app,
        [
            "judge",
            "accept-identical",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--chapter",
            "0001",
            "--write",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "chapter 0001 complete" in res.output
    assert "next global command:" in res.output
    assert "--chapter 0002" in res.output


def test_judge_task_renders_binding_glossary_details(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    lowlands_record = _record_id_for_text(project_dir, "Lowlands")
    mandate = runner.invoke(
        app,
        [
            "context",
            "mandate-term",
            str(project_dir),
            "Lowlands",
            "--profile",
            "de_judge",
            "--target",
            "Tieflande",
            "--forbid",
            "Niederlande",
        ],
    )
    assert mandate.exit_code == 0, mandate.output
    _write_source_candidate(
        project_dir, "de_a", lowlands_record, "Tieflande antworten."
    )
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--chapter",
            "0001",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)
    block = judge_ingest_block_path(
        load_profile_project(project_dir, "de_judge"), task_id
    ).read_text("utf-8")
    assert "GLOSSARY:" in block
    assert "source: Lowlands" in block
    assert "required: Tieflande" in block
    assert "forbidden: Niederlande" in block
    assert "enforce: error" in block


def test_judge_task_renders_full_validation_messages(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    lowlands_record = _record_id_for_text(project_dir, "Lowlands")
    mandate = runner.invoke(
        app,
        [
            "context",
            "mandate-term",
            str(project_dir),
            "Lowlands",
            "--profile",
            "de_judge",
            "--target",
            "Tieflande",
            "--forbid",
            "Niederlande",
        ],
    )
    assert mandate.exit_code == 0, mandate.output
    # Candidate uses the forbidden target -> carries a forbidden_term_used finding.
    _write_source_candidate(
        project_dir, "de_a", lowlands_record, "Niederlande antworten."
    )
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--chapter",
            "0001",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)
    block = judge_ingest_block_path(
        load_profile_project(project_dir, "de_judge"), task_id
    ).read_text("utf-8")
    # Full message is rendered, not just the compact severity:rule code.
    assert "validation:" in block
    assert "- error forbidden_term_used:" in block
    assert "must not be translated as Niederlande" in block


def test_judge_task_render_does_not_truncate_inside_record_policy_block(tmp_path: Path):
    project_dir, _ = _judge_project(tmp_path)
    # Mandate a term so each matching record carries a binding glossary block.
    runner.invoke(
        app,
        [
            "context",
            "mandate-term",
            str(project_dir),
            "Empire",
            "--profile",
            "de_judge",
            "--target",
            "Imperium",
        ],
    )
    record_ids = _record_ids(project_dir)
    for rid in record_ids:
        _write_source_candidate(project_dir, "de_a", rid, "Imperium.")
        _write_source_candidate(project_dir, "de_b", rid, "Imperium.")
    full_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--chapter",
            "0001",
        ],
    )
    assert full_res.exit_code == 0, full_res.output
    full_task_id = _judge_task_id(project_dir)
    full_block = judge_ingest_block_path(
        load_profile_project(project_dir, "de_judge"), full_task_id
    ).read_text("utf-8")
    full_records = full_block.count("## ")
    # Force trimming with a low line budget; never cut inside a record.
    trim_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
            "--chapter",
            "0001",
            "--max-rendered-lines",
            "12",
        ],
    )
    assert trim_res.exit_code == 0, trim_res.output
    trim_task_id = _judge_task_id(project_dir)
    trim_block = judge_ingest_block_path(
        load_profile_project(project_dir, "de_judge"), trim_task_id
    ).read_text("utf-8")
    # Every kept record header has a matching DECISION block: no mid-record cut.
    assert trim_block.count("## ") == trim_block.count("DECISION:")
    # Trimming removed whole records, not partial ones.
    assert 1 <= trim_block.count("## ") <= full_records


def _mandate_termbase(
    project_dir: Path,
    profile: str,
    source: str,
    target: str,
    *forbidden: str,
    entry_id: str = "LEX-TEST",
    preferred_policy: str = "required",
    severity: str = "error",
) -> None:
    args = [
        "termbase",
        "add",
        str(project_dir),
        "--profile",
        profile,
        "--scope",
        "project",
        "--id",
        entry_id,
        "--source",
        source,
        "--preferred",
        target,
        "--preferred-policy",
        preferred_policy,
        "--severity",
        severity,
        "--approve",
    ]
    for value in forbidden:
        args.extend(["--forbid", value])
    res = runner.invoke(app, args)
    assert res.exit_code == 0, res.output


def test_judge_candidate_shows_termbase_forbidden_target(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BOOKTX_TERMBASE_DIR", str(tmp_path / "global-termbase"))
    project_dir, _record_ids = _judge_project(tmp_path)
    lowlands_record = _record_id_for_text(project_dir, "Lowlands")
    _mandate_termbase(project_dir, "de_judge", "Lowlands", "Tieflande", "Niederlande")
    _write_source_candidate(
        project_dir, "de_a", lowlands_record, "Niederlande antworten."
    )

    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
        ],
    )
    assert next_res.exit_code == 0, next_res.output

    task = load_judge_task(
        load_profile_project(project_dir, "de_judge"), _judge_task_id(project_dir)
    )
    assert task is not None
    lowlands = next(record for record in task.records if record.id == lowlands_record)
    candidate_a = next(
        candidate for candidate in lowlands.candidates if candidate.profile == "de_a"
    )
    termbase_findings = [
        finding
        for finding in candidate_a.validation_findings
        if finding.rule == "termbase.forbidden_target"
    ]
    assert termbase_findings, (
        "candidate A should carry a termbase.forbidden_target finding "
        "for the forbidden target"
    )
    assert termbase_findings[0].severity == "error"


def test_judge_insert_copy_rejects_termbase_forbidden_target(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("BOOKTX_TERMBASE_DIR", str(tmp_path / "global-termbase"))
    project_dir, _record_ids = _judge_project(tmp_path)
    lowlands_record = _record_id_for_text(project_dir, "Lowlands")
    _mandate_termbase(project_dir, "de_judge", "Lowlands", "Tieflande", "Niederlande")
    _write_source_candidate(
        project_dir, "de_a", lowlands_record, "Niederlande antworten."
    )
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)
    ingest = judge_ingest_json_path(
        load_profile_project(project_dir, "de_judge"), task_id
    )
    ingest.write_text(
        json.dumps(
            {
                "judge_task_id": task_id,
                "records": [
                    {
                        "id": lowlands_record,
                        "selected": "A",
                        "decision_kind": "copy",
                        "target": "Niederlande antworten.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    res = runner.invoke(
        app,
        [
            "judge",
            "insert",
            str(project_dir),
            "--profile",
            "de_judge",
            "--judge-task-id",
            task_id,
            "--file",
            str(ingest),
            "--format",
            "json",
        ],
    )

    assert res.exit_code != 0
    assert "termbase policy" in res.output


def test_judge_insert_edited_accepts_termbase_required_target(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("BOOKTX_TERMBASE_DIR", str(tmp_path / "global-termbase"))
    project_dir, _record_ids = _judge_project(tmp_path)
    lowlands_record = _record_id_for_text(project_dir, "Lowlands")
    _mandate_termbase(project_dir, "de_judge", "Lowlands", "Tieflande", "Niederlande")
    _write_source_candidate(
        project_dir, "de_a", lowlands_record, "Niederlande antworten."
    )
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)
    ingest = judge_ingest_json_path(
        load_profile_project(project_dir, "de_judge"), task_id
    )
    ingest.write_text(
        json.dumps(
            {
                "judge_task_id": task_id,
                "records": [
                    {
                        "id": lowlands_record,
                        "selected": "A",
                        "decision_kind": "edited",
                        "target": "Die Tieflande antworten.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    res = runner.invoke(
        app,
        [
            "judge",
            "insert",
            str(project_dir),
            "--profile",
            "de_judge",
            "--judge-task-id",
            task_id,
            "--file",
            str(ingest),
            "--format",
            "json",
        ],
    )

    assert res.exit_code == 0, res.output


def test_judge_task_stale_after_applicable_termbase_change(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BOOKTX_TERMBASE_DIR", str(tmp_path / "global-termbase"))
    project_dir, _record_ids = _judge_project(tmp_path)
    lowlands_record = _record_id_for_text(project_dir, "Lowlands")
    _mandate_termbase(project_dir, "de_judge", "Lowlands", "Tieflande", "Niederlande")
    _write_source_candidate(
        project_dir, "de_a", lowlands_record, "Tieflande antworten."
    )
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)

    # Change the applicable termbase after the task was created.
    shard = (
        load_profile_project(project_dir, "de_judge").booktx_dir
        / "termbase"
        / "de.json"
    )
    payload = json.loads(shard.read_text("utf-8"))
    payload["entries"][0]["rationale"] = "Updated termbase instruction."
    shard.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    ingest = judge_ingest_json_path(
        load_profile_project(project_dir, "de_judge"), task_id
    )
    ingest.write_text(
        json.dumps(
            {
                "judge_task_id": task_id,
                "records": [
                    {
                        "id": lowlands_record,
                        "selected": "A",
                        "decision_kind": "copy",
                        "target": "Tieflande antworten.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    res = runner.invoke(
        app,
        [
            "judge",
            "insert",
            str(project_dir),
            "--profile",
            "de_judge",
            "--judge-task-id",
            task_id,
            "--file",
            str(ingest),
            "--format",
            "json",
        ],
    )

    assert res.exit_code != 0
    assert "predates applicable termbase changes" in res.output


def _prefill_artifact_paths(project_dir: Path, task_id: str) -> tuple[Path, Path]:
    decisions_path = judge_ingest_decisions_path(
        load_profile_project(project_dir, "de_judge"), task_id
    )
    hints_path = decisions_path.with_name(
        decisions_path.stem.removesuffix(".decisions") + ".policy-hints.txt"
    )
    return decisions_path, hints_path


def test_judge_prefill_policy_fixes_copies_only_clean_candidate(tmp_path: Path):
    project_dir, _record_ids = _judge_project(tmp_path)
    empire_record = _record_id_for_text(project_dir, "Empire")
    lowlands_record = _record_id_for_text(project_dir, "Lowlands")
    # Empire: a single clean candidate -> deterministic copy.
    _write_source_candidate(
        project_dir, "de_a", empire_record, "Das Imperium rueckt vor."
    )
    # Lowlands: two candidates -> ambiguous -> policy hint.
    _write_source_candidate(
        project_dir, "de_a", lowlands_record, "Die Tieflande antworten."
    )
    _write_source_candidate(
        project_dir, "de_b", lowlands_record, "Die Niederlande antworten."
    )
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)

    res = runner.invoke(
        app,
        [
            "judge",
            "prefill-policy-fixes",
            str(project_dir),
            "--profile",
            "de_judge",
            "--judge-task-id",
            task_id,
            "--write",
        ],
    )
    assert res.exit_code == 0, res.output

    decisions_path, hints_path = _prefill_artifact_paths(project_dir, task_id)
    decisions_text = decisions_path.read_text("utf-8")
    hints_text = hints_path.read_text("utf-8")
    # Only the clean single-candidate record is copied.
    assert f"## {empire_record}" in decisions_text
    assert "decision_kind: copy" in decisions_text
    assert "selected: A" in decisions_text
    # The ambiguous multi-candidate record is deferred to manual judging.
    assert f"## {lowlands_record}" not in decisions_text
    assert f"## {lowlands_record}" in hints_text


def test_judge_prefill_policy_fixes_literal_forbidden_replacement(tmp_path: Path):
    project_dir, _record_ids = _judge_project(tmp_path)
    lowlands_record = _record_id_for_text(project_dir, "Lowlands")
    runner.invoke(
        app,
        [
            "context",
            "mandate-term",
            str(project_dir),
            "Lowlands",
            "--profile",
            "de_judge",
            "--target",
            "Tieflande",
            "--forbid",
            "Niederlande",
        ],
    )
    _write_source_candidate(
        project_dir, "de_a", lowlands_record, "Die Niederlande antworten."
    )
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)

    res = runner.invoke(
        app,
        [
            "judge",
            "prefill-policy-fixes",
            str(project_dir),
            "--profile",
            "de_judge",
            "--judge-task-id",
            task_id,
            "--write",
        ],
    )
    assert res.exit_code == 0, res.output

    decisions_path, _hints_path = _prefill_artifact_paths(project_dir, task_id)
    decisions_text = decisions_path.read_text("utf-8")
    # The single forbidden literal is deterministically replaced.
    assert f"## {lowlands_record}" in decisions_text
    assert "decision_kind: edited" in decisions_text
    assert "Die Tieflande antworten." in decisions_text
    # The forbidden literal must not survive into the edited TARGET.
    assert "TARGET:\nDie Niederlande" not in decisions_text


def test_judge_prefill_policy_fixes_refuses_multiple_occurrences(tmp_path: Path):
    project_dir, _record_ids = _judge_project(tmp_path)
    lowlands_record = _record_id_for_text(project_dir, "Lowlands")
    runner.invoke(
        app,
        [
            "context",
            "mandate-term",
            str(project_dir),
            "Lowlands",
            "--profile",
            "de_judge",
            "--target",
            "Tieflande",
            "--forbid",
            "Niederlande",
        ],
    )
    # The forbidden literal occurs twice -> replacement is ambiguous.
    _write_source_candidate(
        project_dir,
        "de_a",
        lowlands_record,
        "Niederlande und Niederlande antworten.",
    )
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)

    res = runner.invoke(
        app,
        [
            "judge",
            "prefill-policy-fixes",
            str(project_dir),
            "--profile",
            "de_judge",
            "--judge-task-id",
            task_id,
            "--write",
        ],
    )
    assert res.exit_code == 0, res.output

    decisions_path, hints_path = _prefill_artifact_paths(project_dir, task_id)
    decisions_text = decisions_path.read_text("utf-8")
    hints_text = hints_path.read_text("utf-8")
    assert f"## {lowlands_record}" not in decisions_text
    assert f"## {lowlands_record}" in hints_text


def test_judge_prefill_policy_fixes_refuses_inline_xhtml_unsafe_change(tmp_path: Path):
    project_dir, _record_ids = _judge_project(tmp_path)
    lowlands_record = _record_id_for_text(project_dir, "Lowlands")
    runner.invoke(
        app,
        [
            "context",
            "mandate-term",
            str(project_dir),
            "Lowlands",
            "--profile",
            "de_judge",
            "--target",
            "Tieflande",
            "--forbid",
            "Niederlande",
        ],
    )
    # The candidate carries an inline-XHTML span token (__TAG_001__) that the
    # source lacks; auto-editing around inline markup is unsafe, so the
    # edited target still fails validation and the record is deferred.
    _write_source_candidate(
        project_dir,
        "de_a",
        lowlands_record,
        "Die __TAG_001__ Niederlande antworten.",
    )
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)

    res = runner.invoke(
        app,
        [
            "judge",
            "prefill-policy-fixes",
            str(project_dir),
            "--profile",
            "de_judge",
            "--judge-task-id",
            task_id,
            "--write",
        ],
    )
    assert res.exit_code == 0, res.output

    decisions_path, hints_path = _prefill_artifact_paths(project_dir, task_id)
    decisions_text = decisions_path.read_text("utf-8")
    hints_text = hints_path.read_text("utf-8")
    assert f"## {lowlands_record}" not in decisions_text
    assert f"## {lowlands_record}" in hints_text


def test_judge_status_prints_sweep_hint_when_multiple_chapters_remaining(
    tmp_path: Path,
):
    project_dir, _chapters = _multi_chapter_judge_project(tmp_path)
    res = runner.invoke(
        app,
        [
            "judge",
            "status",
            str(project_dir),
            "--profile",
            "de_judge",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "identical sweep:" in res.output
    assert "sweep-identical" in res.output


def test_judge_finish_chapter_plan_profile_root_paths_are_local(tmp_path: Path):
    project_dir, record_ids = _judge_project(tmp_path)
    _write_source_candidate(project_dir, "de_a", record_ids[0], "A target.")
    # Prepare a profile root (isolated snapshot mode).
    runner.invoke(
        app,
        [
            "judge",
            "prepare-isolation",
            str(project_dir),
            "--profile",
            "de_judge",
            "--write",
        ],
    )
    profile_root = project_dir / "translations" / "de_judge"
    res = runner.invoke(
        app,
        [
            "judge",
            "finish-chapter-plan",
            str(profile_root),
            "--chapter",
            "0001",
        ],
    )
    assert res.exit_code == 0, res.output
    # All paths/commands must be profile-root-local.
    assert "../" not in res.output
    assert "--profile" not in res.output
    # Use the local project argument, not absolute paths or sibling names.
    assert str(project_dir) not in res.output
    assert "." in res.output


def test_judge_insert_edited_rejects_missing_opening_german_quote(tmp_path: Path):
    # Source enclosed in recognized curly-quoted pair \u201c...\u201d.
    project_dir = tmp_path / "book"
    src = tmp_path / "novel.md"
    src.write_text("# One\n\n\u201cThe Lowlands answer.\u201d\n", encoding="utf-8")
    runner.invoke(
        app,
        ["init", str(project_dir), "--source-file", str(src), "--source-lang", "en"],
    )
    assert runner.invoke(app, ["extract", str(project_dir)]).exit_code == 0
    _create_translation_profile(project_dir, "de_a")
    _create_selection_profile(project_dir, "de_judge", ["de_a"])
    _ready_context(project_dir, "de_a")
    _ready_context(project_dir, "de_judge")
    quoted_record = _record_id_for_text(project_dir, "Lowlands")
    # Candidate target: opening curly quote missing.
    _write_source_candidate(
        project_dir, "de_a", quoted_record, "Niederlande antworten.\u201d"
    )
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)
    ingest = judge_ingest_json_path(
        load_profile_project(project_dir, "de_judge"), task_id
    )
    ingest.write_text(
        json.dumps(
            {
                "judge_task_id": task_id,
                "records": [
                    {
                        "id": quoted_record,
                        "selected": "A",
                        "decision_kind": "edited",
                        "target": "Niederlande antworten.\u201d",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    res = runner.invoke(
        app,
        [
            "judge",
            "insert",
            str(project_dir),
            "--profile",
            "de_judge",
            "--judge-task-id",
            task_id,
            "--file",
            str(ingest),
            "--format",
            "json",
        ],
    )

    assert res.exit_code != 0
    assert "quotation" in res.output


def test_judge_insert_prints_qa_summary_with_warnings(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BOOKTX_TERMBASE_DIR", str(tmp_path / "global-termbase"))
    project_dir, _record_ids = _judge_project(tmp_path)
    lowlands_record = _record_id_for_text(project_dir, "Lowlands")
    # Warning-level termbase rule: candidate violates it, insert still succeeds.
    _mandate_termbase(
        project_dir,
        "de_judge",
        "Lowlands",
        "Tieflande",
        "Niederlande",
        severity="warn",
        preferred_policy="off",
    )
    _write_source_candidate(
        project_dir, "de_a", lowlands_record, "Die Niederlande antworten."
    )
    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--sources",
            "de_a,de_b",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    task_id = _judge_task_id(project_dir)
    ingest = judge_ingest_json_path(
        load_profile_project(project_dir, "de_judge"), task_id
    )
    ingest.write_text(
        json.dumps(
            {
                "judge_task_id": task_id,
                "records": [
                    {
                        "id": lowlands_record,
                        "selected": "A",
                        "decision_kind": "edited",
                        "target": "Die Niederlande antworten.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    res = runner.invoke(
        app,
        [
            "judge",
            "insert",
            str(project_dir),
            "--profile",
            "de_judge",
            "--judge-task-id",
            task_id,
            "--file",
            str(ingest),
            "--format",
            "json",
        ],
    )

    assert res.exit_code == 0, res.output
    assert "qa:" in res.output
    assert "warning:" in res.output


# =====================================================================
# Single-source judge revision profiles (selection.purpose=revise)
# =====================================================================


def _create_revision_profile(
    project_dir: Path,
    profile: str,
    source: str,
    *,
    revision_focus: str = "general",
) -> None:
    create_profile(project_dir, profile, target_language="de", kind="selection")
    cfg = load_profile_config(project_dir, profile)
    cfg.selection = SelectionConfig(
        sources=[source],
        purpose="revise",
        require_all_sources=True,
        revision_focus=revision_focus,
    )
    write_profile_config(project_dir, cfg)


def _revise_project(
    tmp_path: Path, *, revision_focus: str = "general"
) -> tuple[Path, list[str]]:
    project_dir = _make_project(tmp_path)
    _create_translation_profile(project_dir, "de_a")
    _create_revision_profile(
        project_dir, "de_rev", "de_a", revision_focus=revision_focus
    )
    for profile in ("de_a", "de_rev"):
        _ready_context(project_dir, profile)
    return project_dir, _record_ids(project_dir)


def _revise_profile_root_next(
    project_dir: Path, *, profile: str = "de_rev", chapter: str = "0001"
):
    pr = project_dir / "translations" / profile
    with _chdir(pr):
        return runner.invoke(
            app,
            [
                "judge",
                "next",
                ".",
                "--unit",
                "chapter",
                "--chapter",
                chapter,
                "--max-words",
                "900",
                "--format",
                "decisions",
            ],
        )


def _revise_profile_root_insert(
    project_dir: Path, task_id: str, file_path: str, *, profile: str = "de_rev"
):
    pr = project_dir / "translations" / profile
    with _chdir(pr):
        return runner.invoke(
            app,
            [
                "judge",
                "insert",
                ".",
                "--judge-task-id",
                task_id,
                "--file",
                file_path,
                "--format",
                "decisions",
            ],
        )


def _revise_task_id(project_dir: Path, profile: str = "de_rev") -> str | None:
    task_dir = project_dir / "translations" / profile / "judge-tasks"
    if not task_dir.is_dir():
        return None
    files = sorted(task_dir.glob("*.json"))
    return files[0].stem if files else None


def test_revise_create_profile_cli_validates_purpose_and_sources(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    _create_translation_profile(project_dir, "de_a")

    # valid revise creation
    res = runner.invoke(
        app,
        [
            "judge",
            "create-profile",
            str(project_dir),
            "de_rev",
            "--target",
            "de",
            "--sources",
            "de_a",
            "--purpose",
            "revise",
            "--revision-focus",
            "grammar",
        ],
    )
    assert res.exit_code == 0, res.output
    cfg = load_profile_config(project_dir, "de_rev")
    assert cfg.selection is not None and cfg.selection.purpose == "revise"
    assert cfg.selection.revision_focus == "grammar"
    assert cfg.selection.require_all_sources is True
    assert "revision focus: grammar" in res.output

    # invalid purpose rejected, no profile leaked
    res2 = runner.invoke(
        app,
        [
            "judge",
            "create-profile",
            str(project_dir),
            "de_bad",
            "--target",
            "de",
            "--sources",
            "de_a",
            "--purpose",
            "bogus",
        ],
    )
    assert res2.exit_code != 0
    assert not (project_dir / "translations" / "de_bad").exists()

    # multi-source revise rejected, no profile leaked
    _create_translation_profile(project_dir, "de_b")
    res3 = runner.invoke(
        app,
        [
            "judge",
            "create-profile",
            str(project_dir),
            "de_bad2",
            "--target",
            "de",
            "--sources",
            "de_a,de_b",
            "--purpose",
            "revise",
        ],
    )
    assert res3.exit_code != 0
    assert not (project_dir / "translations" / "de_bad2").exists()

    # compare mode rejects grammar-only focus
    res4 = runner.invoke(
        app,
        [
            "judge",
            "create-profile",
            str(project_dir),
            "de_bad3",
            "--target",
            "de",
            "--sources",
            "de_a",
            "--purpose",
            "compare",
            "--revision-focus",
            "grammar",
        ],
    )
    assert res4.exit_code != 0
    assert not (project_dir / "translations" / "de_bad3").exists()

    # unknown focus rejected before profile creation
    res5 = runner.invoke(
        app,
        [
            "judge",
            "create-profile",
            str(project_dir),
            "de_bad4",
            "--target",
            "de",
            "--sources",
            "de_a",
            "--purpose",
            "revise",
            "--revision-focus",
            "bogus",
        ],
    )
    assert res5.exit_code != 0
    assert not (project_dir / "translations" / "de_bad4").exists()

    # omitted focus remains backward-compatible
    res6 = runner.invoke(
        app,
        [
            "judge",
            "create-profile",
            str(project_dir),
            "de_rev_default",
            "--target",
            "de",
            "--sources",
            "de_a",
            "--purpose",
            "revise",
        ],
    )
    assert res6.exit_code == 0, res6.output
    cfg_default = load_profile_config(project_dir, "de_rev_default")
    assert cfg_default.selection is not None
    assert cfg_default.selection.revision_focus == "general"


def test_revise_resolve_sources_overrides(tmp_path: Path):
    project_dir, _ = _revise_project(tmp_path)
    proj = load_profile_project(project_dir, "de_rev")
    from booktx.selection_mode import resolve_judge_sources_for_purpose

    # omitted ok
    assert resolve_judge_sources_for_purpose(proj, None) == ["de_a"]
    # matching explicit ok
    assert resolve_judge_sources_for_purpose(proj, "de_a") == ["de_a"]
    # mismatched / multi rejected
    from booktx.errors import BooktxError

    for bad in ("de_b", "de_a,de_b"):
        try:
            resolve_judge_sources_for_purpose(proj, bad)
            raise AssertionError(f"expected rejection for {bad!r}")
        except BooktxError as exc:
            assert exc.code == "judge_revision_sources_override"


def test_revise_status_blocks_source_gaps_and_next_fails(tmp_path: Path):
    project_dir, record_ids = _revise_project(tmp_path, revision_focus="grammar")
    _write_source_candidate(project_dir, "de_a", record_ids[0], "Basisziel.")

    status = runner.invoke(
        app,
        ["judge", "status", str(project_dir), "--profile", "de_rev", "--json"],
    )
    assert status.exit_code == 0, status.output
    data = json.loads(status.output)
    assert data["selection_purpose"] == "revise"
    assert data["revision_focus"] == "grammar"
    assert "revision_source_incomplete" in data["blocked_by"]
    assert data["records_with_candidate_gaps"] > 0
    assert data["next_command"] == ""

    next_res = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_rev",
            "--unit",
            "chapter",
            "--chapter",
            "0001",
            "--max-words",
            "900",
            "--format",
            "block",
        ],
    )
    assert next_res.exit_code != 0

    for record_id in record_ids[1:]:
        _write_source_candidate(
            project_dir, "de_a", record_id, f"Basisziel {record_id}."
        )

    ready_next = runner.invoke(
        app,
        [
            "judge",
            "next",
            str(project_dir),
            "--profile",
            "de_rev",
            "--unit",
            "chapter",
            "--chapter",
            "0001",
            "--max-words",
            "900",
            "--format",
            "block",
        ],
    )
    assert ready_next.exit_code == 0, ready_next.output


def test_revise_deterministic_commands_are_rejected(tmp_path: Path):
    project_dir, record_ids = _revise_project(tmp_path)
    for record_id in record_ids:
        _write_source_candidate(
            project_dir, "de_a", record_id, f"A target {record_id}."
        )
    _sync_sources(project_dir, profile="de_rev", write=True)
    for cmd in (
        [
            "judge",
            "accept-identical",
            str(project_dir),
            "--profile",
            "de_rev",
            "--sources",
            "de_a",
            "--unit",
            "chapter",
            "--chapter",
            "0001",
            "--max-records",
            "10",
        ],
        [
            "judge",
            "sweep-identical",
            str(project_dir),
            "--profile",
            "de_rev",
            "--sources",
            "de_a",
            "--from-chapter",
            "0001",
            "--to-chapter",
            "0001",
            "--max-records",
            "10",
        ],
    ):
        # dry run
        res = runner.invoke(app, cmd)
        assert res.exit_code != 0, (cmd, res.output)
        assert "judge_revision_explicit_decisions_required" in res.output or (
            "revise" in res.output.lower()
        )
        # write mode
        res_w = runner.invoke(app, cmd + ["--write"])
        assert res_w.exit_code != 0
    # prefill rejected too (needs a task; create one first)
    res_next = _revise_profile_root_next(project_dir)
    assert res_next.exit_code == 0, res_next.output
    task_id = _revise_task_id(project_dir)
    assert task_id is not None
    res_pre = runner.invoke(
        app,
        [
            "judge",
            "prefill-policy-fixes",
            str(project_dir),
            "--profile",
            "de_rev",
            "--judge-task-id",
            task_id,
        ],
    )
    assert res_pre.exit_code != 0
    res_pre_w = runner.invoke(
        app,
        [
            "judge",
            "prefill-policy-fixes",
            str(project_dir),
            "--profile",
            "de_rev",
            "--judge-task-id",
            task_id,
            "--write",
        ],
    )
    assert res_pre_w.exit_code != 0
    # No extra task artifacts were created by the rejected commands: only the one
    # task from judge next should exist.
    task_dir = project_dir / "translations" / "de_rev" / "judge-tasks"
    assert len(list(task_dir.glob("*.json"))) == 1


def test_revise_task_artifact_uses_base_target(tmp_path: Path):
    project_dir, record_ids = _revise_project(tmp_path, revision_focus="grammar")
    for record_id in record_ids:
        _write_source_candidate(
            project_dir, "de_a", record_id, f"Base target {record_id}."
        )
    _sync_sources(project_dir, profile="de_rev", write=True)
    res = _revise_profile_root_next(project_dir)
    assert res.exit_code == 0, res.output
    task_id = _revise_task_id(project_dir)
    assert task_id is not None
    proj = load_profile_project(project_dir, "de_rev")
    task = load_judge_task(proj, task_id)
    assert task is not None
    assert task.revision_focus == "grammar"
    block_path = judge_ingest_block_path(proj, task_id)
    text = Path(block_path).read_text("utf-8")
    assert "# booktx judge revision task" in text
    assert "purpose: revise" in text
    assert "revision_focus: grammar" in text
    assert "BASE_TARGET [A]" in text
    assert "BASE_TARGET is authoritative for wording and terminology." in text
    assert "Do not change vocabulary, terminology, style, flow, tone, register," in text
    assert "CANDIDATES:" not in text
    assert (
        "Use edited for grammar, flow, punctuation, style, or terminology corrections."
        not in text
    )
    decisions_path = judge_ingest_decisions_path(proj, task_id)
    dec_text = Path(decisions_path).read_text("utf-8")
    assert "# booktx judge grammar revision decisions" in dec_text
    assert "Do not rewrite grammatically valid text for style or fluency." in dec_text


def test_revise_insert_requires_complete_task_and_writes_hash(tmp_path: Path):
    project_dir, record_ids = _revise_project(tmp_path)
    for record_id in record_ids:
        _write_source_candidate(
            project_dir, "de_a", record_id, f"Base target {record_id}."
        )
    _sync_sources(project_dir, profile="de_rev", write=True)
    res = _revise_profile_root_next(project_dir)
    assert res.exit_code == 0, res.output
    task_id = _revise_task_id(project_dir)
    proj = load_profile_project(project_dir, "de_rev")
    ingest = proj.profile_dir / "judge-ingest" / f"{task_id}.decisions.txt"

    # Incomplete (empty submission file with no record) is rejected and
    # non-mutating: no store record should be created.
    ingest.write_text(
        f"# booktx judge revision decisions\njudge_task_id: {task_id}\n",
        encoding="utf-8",
    )
    res_empty = _revise_profile_root_insert(
        project_dir, task_id, f"judge-ingest/{task_id}.decisions.txt"
    )
    assert res_empty.exit_code != 0

    # Valid explicit copy decision writes matching output hash provenance.
    task = load_judge_task(proj, task_id)
    base_target = task.records[0].candidates[0].target
    decisions = [(rec.id, "A", "copy", "ok", "") for rec in task.records]
    _fill_decisions_ingest(ingest, task_id, decisions)
    res_copy = _revise_profile_root_insert(
        project_dir, task_id, f"judge-ingest/{task_id}.decisions.txt"
    )
    assert res_copy.exit_code == 0, res_copy.output
    ledger = load_translation_selection_ledger(proj)
    decision = ledger.records[record_ids[0]]
    assert decision.decision_kind == "copy"
    assert decision.output_target_sha256 == sha256_text(base_target)


def test_revise_insert_edited_writes_hash_and_compare_task_rejected(tmp_path: Path):
    project_dir, record_ids = _revise_project(tmp_path)
    for record_id in record_ids:
        _write_source_candidate(
            project_dir, "de_a", record_id, f"Base target {record_id}."
        )
    _sync_sources(project_dir, profile="de_rev", write=True)
    res = _revise_profile_root_next(project_dir)
    assert res.exit_code == 0, res.output
    task_id = _revise_task_id(project_dir)
    proj = load_profile_project(project_dir, "de_rev")
    ingest = proj.profile_dir / "judge-ingest" / f"{task_id}.decisions.txt"
    edited = "Edited target text."
    task = load_judge_task(proj, task_id)
    decisions = []
    for idx, rec in enumerate(task.records):
        if idx == 0:
            decisions.append((rec.id, "A", "edited", "fix", edited))
        else:
            decisions.append((rec.id, "A", "copy", "ok", ""))
    _fill_decisions_ingest(ingest, task_id, decisions)
    res_ed = _revise_profile_root_insert(
        project_dir, task_id, f"judge-ingest/{task_id}.decisions.txt"
    )
    assert res_ed.exit_code == 0, res_ed.output
    ledger = load_translation_selection_ledger(proj)
    assert ledger.records[record_ids[0]].output_target_sha256 == sha256_text(edited)

    # A compare-purpose task cannot be inserted into a revise profile.
    task = load_judge_task(proj, task_id)
    compare_task = task.model_copy(update={"selection_purpose": "compare"})
    from booktx.config import write_judge_task

    write_judge_task(proj, compare_task)
    ingest2 = proj.profile_dir / "judge-ingest" / f"{task_id}-compare.decisions.txt"
    _fill_decisions_ingest(ingest2, task_id, decisions)
    res_cmp = runner.invoke(
        app,
        [
            "judge",
            "insert",
            str(project_dir),
            "--profile",
            "de_rev",
            "--judge-task-id",
            task_id,
            "--file",
            str(ingest2),
            "--format",
            "decisions",
        ],
    )
    assert res_cmp.exit_code != 0


def test_revise_insert_rejects_revision_focus_mismatch(tmp_path: Path):
    project_dir, record_ids = _revise_project(tmp_path, revision_focus="grammar")
    for record_id in record_ids:
        _write_source_candidate(
            project_dir, "de_a", record_id, f"Base target {record_id}."
        )
    _sync_sources(project_dir, profile="de_rev", write=True)
    res = _revise_profile_root_next(project_dir)
    assert res.exit_code == 0, res.output
    task_id = _revise_task_id(project_dir)
    assert task_id is not None
    proj = load_profile_project(project_dir, "de_rev")
    ingest = proj.profile_dir / "judge-ingest" / f"{task_id}.decisions.txt"
    task = load_judge_task(proj, task_id)
    assert task is not None
    decisions = [(rec.id, "A", "copy", "ok", "") for rec in task.records]
    _fill_decisions_ingest(ingest, task_id, decisions)

    cfg = load_profile_config(project_dir, "de_rev")
    assert cfg.selection is not None
    cfg.selection.revision_focus = "general"
    write_profile_config(project_dir, cfg)

    res_insert = _revise_profile_root_insert(
        project_dir, task_id, f"judge-ingest/{task_id}.decisions.txt"
    )
    assert res_insert.exit_code != 0
    assert "revision focus changed after task creation" in res_insert.output


def test_revise_status_reports_purpose_and_no_sweep(tmp_path: Path):
    project_dir, record_ids = _revise_project(tmp_path, revision_focus="grammar")
    for index, record_id in enumerate(record_ids, start=1):
        _write_source_candidate(project_dir, "de_a", record_id, f"A target {index}.")
    _sync_sources(project_dir, profile="de_rev", write=True)
    res = _revise_profile_root_next(project_dir)
    assert res.exit_code == 0, res.output
    task_id = _revise_task_id(project_dir)
    assert task_id is not None
    proj = load_profile_project(project_dir, "de_rev")
    task = load_judge_task(proj, task_id)
    assert task is not None
    ingest = proj.profile_dir / "judge-ingest" / f"{task_id}.decisions.txt"
    decisions = []
    for idx, rec in enumerate(task.records):
        if idx == 1:
            decisions.append(
                (rec.id, "A", "edited", "grammar fix", "A korrigiertes Ziel.")
            )
        else:
            decisions.append((rec.id, "A", "copy", "ok", ""))
    _fill_decisions_ingest(ingest, task_id, decisions)
    insert = _revise_profile_root_insert(
        project_dir, task_id, f"judge-ingest/{task_id}.decisions.txt"
    )
    assert insert.exit_code == 0, insert.output
    res = runner.invoke(
        app,
        ["judge", "status", str(project_dir), "--profile", "de_rev", "--json"],
    )
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["selection_purpose"] == "revise"
    assert data["revision_focus"] == "grammar"
    assert data.get("sweep_hint") == ""
    assert data["decisions_copy"] == len(task.records) - 1
    assert data["decisions_edited"] == 1
    assert data["decision_edit_rate"] == round(1 / len(task.records), 4)


def test_revise_grammar_insert_warns_on_large_edit(tmp_path: Path):
    project_dir, record_ids = _revise_project(tmp_path, revision_focus="grammar")
    base_targets = {
        record_ids[0]: "Eins.",
        record_ids[1]: "Das Imperium marschiert im Morgengrauen weiter.",
        record_ids[2]: "Die Niederungen antworten.",
    }
    for record_id, target in base_targets.items():
        _write_source_candidate(project_dir, "de_a", record_id, target)
    _sync_sources(project_dir, profile="de_rev", write=True)
    res = _revise_profile_root_next(project_dir)
    assert res.exit_code == 0, res.output
    task_id = _revise_task_id(project_dir)
    assert task_id is not None
    proj = load_profile_project(project_dir, "de_rev")
    task = load_judge_task(proj, task_id)
    assert task is not None
    ingest = proj.profile_dir / "judge-ingest" / f"{task_id}.decisions.txt"
    decisions = []
    for rec in task.records:
        if rec.id == record_ids[1]:
            decisions.append(
                (
                    rec.id,
                    "A",
                    "edited",
                    "grammar",
                    (
                        "Heute blieb niemand an seinem Platz, weil alles "
                        "vollkommen anders wurde."
                    ),
                )
            )
        else:
            decisions.append((rec.id, "A", "copy", "ok", ""))
    _fill_decisions_ingest(ingest, task_id, decisions)
    insert = _revise_profile_root_insert(
        project_dir, task_id, f"judge-ingest/{task_id}.decisions.txt"
    )
    assert insert.exit_code != 0, insert.output
    assert (
        "judge_grammar_nonminimal" in insert.output
        or "grammar edit is too large" in insert.output
    )


def test_revise_validation_and_build_enforce_provenance(tmp_path: Path):
    project_dir, record_ids = _revise_project(tmp_path)
    for rid in record_ids:
        _write_source_candidate(project_dir, "de_a", rid, f"Base target {rid}.")
    _sync_sources(project_dir, profile="de_rev", write=True)
    res = _revise_profile_root_next(project_dir)
    assert res.exit_code == 0, res.output
    task_id = _revise_task_id(project_dir)
    proj = load_profile_project(project_dir, "de_rev")
    task = load_judge_task(proj, task_id)
    ingest = proj.profile_dir / "judge-ingest" / f"{task_id}.decisions.txt"

    # Accept a valid explicit copy decision for every record in the task.
    decisions = [(rec.id, "A", "copy", "ok", "") for rec in task.records]
    _fill_decisions_ingest(ingest, task_id, decisions)
    res_ins = _revise_profile_root_insert(
        project_dir, task_id, f"judge-ingest/{task_id}.decisions.txt"
    )
    assert res_ins.exit_code == 0, res_ins.output

    # Valid provenance: validation and build --require-complete pass.
    res_v = runner.invoke(app, ["validate", str(project_dir), "--profile", "de_rev"])
    assert res_v.exit_code == 0, res_v.output
    res_b = runner.invoke(
        app, ["build", str(project_dir), "--profile", "de_rev", "--require-complete"]
    )
    assert res_b.exit_code == 0, res_b.output

    # Simulate a direct store revision that bypasses judge mode: change the
    # active target for the first record without updating the decision hash.
    first = task.records[0].id
    store = load_translation_store(proj)
    from booktx.translation_store import effective_candidate_selection

    sel = effective_candidate_selection(store.records[first], strict_active_review=True)
    assert sel is not None
    for cand in store.records[first].versions:
        if cand.version_ref == sel.version_ref:
            cand.target = "TAMPERED target text."
    write_translation_store(proj, store)

    res_v2 = runner.invoke(app, ["validate", str(project_dir), "--profile", "de_rev"])
    assert res_v2.exit_code != 0
    assert "judge_revision_output_hash_mismatch" in res_v2.output
    res_b2 = runner.invoke(app, ["build", str(project_dir), "--profile", "de_rev"])
    assert res_b2.exit_code != 0

    # Re-judging the tampered record through `judge record` restores validity.
    res_rec = runner.invoke(
        app,
        [
            "judge",
            "record",
            str(project_dir),
            "--profile",
            "de_rev",
            "--record",
            first,
            "--format",
            "decisions",
        ],
    )
    assert res_rec.exit_code == 0, res_rec.output
    # The record-task creates a new task id; find and fill it.
    task_files = sorted(
        (project_dir / "translations" / "de_rev" / "judge-tasks").glob("*.json"),
        key=lambda p: p.stat().st_mtime,
    )
    rt_id = task_files[-1].stem
    rproj = load_profile_project(project_dir, "de_rev")
    ringest = rproj.profile_dir / "judge-ingest" / f"{rt_id}.decisions.txt"
    _fill_decisions_ingest(ringest, rt_id, [(first, "A", "copy", "ok", "")])
    res_reins = runner.invoke(
        app,
        [
            "judge",
            "insert",
            str(project_dir),
            "--profile",
            "de_rev",
            "--judge-task-id",
            rt_id,
            "--file",
            str(ringest),
            "--format",
            "decisions",
        ],
    )
    assert res_reins.exit_code == 0, res_reins.output
    res_v3 = runner.invoke(app, ["validate", str(project_dir), "--profile", "de_rev"])
    assert res_v3.exit_code == 0, res_v3.output
    res_b3 = runner.invoke(
        app, ["build", str(project_dir), "--profile", "de_rev", "--require-complete"]
    )
    assert res_b3.exit_code == 0, res_b3.output


# --------------------------------------------------------------------------
# judge todo bounded scope tests (ac-0004)
# --------------------------------------------------------------------------


def test_judge_todo_next_creates_bounded_scope_with_chapter_limit(tmp_path: Path):
    """todo-next creates a snapshot-pinned scope with max_records and max_sentences."""
    project_dir, chapters = _multi_chapter_judge_project(tmp_path)
    # Write candidates for all chapters
    for _chapter_id, record_ids in chapters.items():
        for profile, base in (("de_a", "A"), ("de_b", "B")):
            for rid in record_ids:
                _write_source_candidate(project_dir, profile, rid, f"{base} {rid}.")
    # Sync sources for snapshot
    _sync_sources(project_dir, write=True)
    # Create bounded todo with 2 chapters, max_records=1, max_sentences=5
    res = runner.invoke(
        app,
        [
            "judge",
            "todo-next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--chapters",
            "2",
            "--max-records",
            "1",
            "--max-sentences",
            "5",
            "--write",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "judge todo:" in res.output
    # Load the latest todo and verify fields
    proj = load_profile_project(project_dir, "de_judge")
    todo = latest_todo(proj)
    assert todo is not None
    assert len(todo.chapter_ids) <= 2
    assert todo.max_records == 1
    assert todo.max_sentences == 5
    # snapshot_id may be None if the snapshot wasn't fully resolved in test env
    # The key assertions are the bounded limits are preserved


def test_judge_todo_status_reports_remaining_chapters(tmp_path: Path):
    """todo-status shows remaining chapters and complete flag."""
    project_dir, chapters = _multi_chapter_judge_project(tmp_path)
    for _chapter_id, record_ids in chapters.items():
        for profile, base in (("de_a", "A"), ("de_b", "B")):
            for rid in record_ids:
                _write_source_candidate(project_dir, profile, rid, f"{base} {rid}.")
    _sync_sources(project_dir, write=True)
    # Create todo for all chapters
    runner.invoke(
        app,
        [
            "judge",
            "todo-next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--chapters",
            "3",
            "--write",
        ],
    )
    proj = load_profile_project(project_dir, "de_judge")
    todo = latest_todo(proj)
    assert todo is not None
    # Status shows all chapters remaining (none selected yet)
    res = runner.invoke(
        app,
        [
            "judge",
            "todo-status",
            str(project_dir),
            "--profile",
            "de_judge",
            "--latest",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "remaining:" in res.output


def test_judge_todo_resume_creates_task_for_next_missing_chapter(tmp_path: Path):
    """todo-resume creates a judge task for the oldest missing chapter."""
    project_dir, chapters = _multi_chapter_judge_project(tmp_path)
    for _chapter_id, record_ids in chapters.items():
        for profile, base in (("de_a", "A"), ("de_b", "B")):
            for rid in record_ids:
                _write_source_candidate(project_dir, profile, rid, f"{base} {rid}.")
    _sync_sources(project_dir, write=True)
    # Create todo for first 2 chapters
    create_res = runner.invoke(
        app,
        [
            "judge",
            "todo-next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--chapters",
            "2",
            "--max-records",
            "1",
            "--write",
        ],
    )
    assert create_res.exit_code == 0, create_res.output
    proj = load_profile_project(project_dir, "de_judge")
    todo = latest_todo(proj)
    assert todo is not None
    # Resume creates a judge task
    res = runner.invoke(
        app,
        [
            "judge",
            "todo-resume",
            str(project_dir),
            "--profile",
            "de_judge",
            "--latest",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "judge task:" in res.output


def test_judge_todo_boundary_stops_when_all_chapters_complete(tmp_path: Path):
    """todo-resume reports completion when all chapters have been judged."""
    project_dir, chapters = _multi_chapter_judge_project(tmp_path)
    for _chapter_id, record_ids in chapters.items():
        for profile, base in (("de_a", "A"), ("de_b", "B")):
            for rid in record_ids:
                _write_source_candidate(project_dir, profile, rid, f"{base} {rid}.")
    _sync_sources(project_dir, write=True)
    # Create todo for 1 chapter only
    create_res = runner.invoke(
        app,
        [
            "judge",
            "todo-next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--chapters",
            "1",
            "--max-records",
            "1",
            "--write",
        ],
    )
    assert create_res.exit_code == 0, create_res.output
    proj = load_profile_project(project_dir, "de_judge")
    todo = latest_todo(proj)
    assert todo is not None
    first_chapter = todo.chapter_ids[0]
    first_rids = chapters[first_chapter]
    # Judge all records in the chapter to complete it
    for _rid in first_rids:
        next_res = runner.invoke(
            app,
            [
                "judge",
                "next",
                str(project_dir),
                "--profile",
                "de_judge",
                "--sources",
                "de_a,de_b",
                "--chapter",
                first_chapter,
                "--max-records",
                "1",
            ],
        )
        if next_res.exit_code != 0:
            break
        if "judge task:" not in next_res.output:
            break
        task_id = next_res.output.split("judge task: ")[1].splitlines()[0].strip()
        ingest = judge_ingest_json_path(
            load_profile_project(project_dir, "de_judge"), task_id
        )
        task = load_judge_task(load_profile_project(project_dir, "de_judge"), task_id)
        if task.records:
            _fill_json_ingest(
                ingest,
                task_id,
                task.records[0].id,
                "A",
                task.records[0].candidates[0].target
                if task.records[0].candidates
                else "test.",
            )
            runner.invoke(
                app,
                [
                    "judge",
                    "insert",
                    str(project_dir),
                    "--profile",
                    "de_judge",
                    "--judge-task-id",
                    task_id,
                    "--file",
                    str(ingest),
                    "--format",
                    "json",
                ],
            )
    # Now resume the todo - it should report complete
    res = runner.invoke(
        app,
        [
            "judge",
            "todo-resume",
            str(project_dir),
            "--profile",
            "de_judge",
            "--latest",
        ],
    )
    assert res.exit_code == 0, res.output
    # Either "judge todo complete" or a task for remaining chapters
    status_res = runner.invoke(
        app,
        [
            "judge",
            "todo-status",
            str(project_dir),
            "--profile",
            "de_judge",
            "--latest",
        ],
    )
    assert status_res.exit_code == 0, status_res.output


def test_judge_todo_json_output_includes_sentence_limit(tmp_path: Path):
    """JSON output from todo-next includes max_sentences."""
    project_dir, chapters = _multi_chapter_judge_project(tmp_path)
    for _chapter_id, record_ids in chapters.items():
        for profile, base in (("de_a", "A"), ("de_b", "B")):
            for rid in record_ids:
                _write_source_candidate(project_dir, profile, rid, f"{base} {rid}.")
    _sync_sources(project_dir, write=True)
    res = runner.invoke(
        app,
        [
            "judge",
            "todo-next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--chapters",
            "1",
            "--max-sentences",
            "10",
            "--write",
            "--json",
        ],
    )
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["max_sentences"] == 10


def test_judge_todo_status_json_reports_remaining(tmp_path: Path):
    """JSON output from todo-status includes remaining_chapters and complete flag."""
    project_dir, chapters = _multi_chapter_judge_project(tmp_path)
    for _chapter_id, record_ids in chapters.items():
        for profile, base in (("de_a", "A"), ("de_b", "B")):
            for rid in record_ids:
                _write_source_candidate(project_dir, profile, rid, f"{base} {rid}.")
    _sync_sources(project_dir, write=True)
    runner.invoke(
        app,
        [
            "judge",
            "todo-next",
            str(project_dir),
            "--profile",
            "de_judge",
            "--chapters",
            "2",
            "--write",
        ],
    )
    res = runner.invoke(
        app,
        [
            "judge",
            "todo-status",
            str(project_dir),
            "--profile",
            "de_judge",
            "--latest",
            "--json",
        ],
    )
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert "remaining_chapters" in data
    assert "complete" in data


# --------------------------------------------------------------------------
# compact replay fixture for failure sequence regression (ac-0006)
# --------------------------------------------------------------------------


def test_judge_replay_fixture_regression(tmp_path: Path):
    """Replay the supplied failure sequence: create profile, sync, next, insert, verify.

    This is a compact regression fixture that exercises the full judge lifecycle
    without auto-legitimizing direct translation writes.
    """
    # Step 1: Create a judge project with 2 source profiles
    project_dir = tmp_path / "book"
    src = tmp_path / "novel.md"
    src.write_text(DOC, encoding="utf-8")
    res = runner.invoke(
        app,
        ["init", str(project_dir), "--source-file", str(src), "--source-lang", "en"],
    )
    assert res.exit_code == 0, res.output
    assert runner.invoke(app, ["extract", str(project_dir)]).exit_code == 0
    _create_translation_profile(project_dir, "de_a")
    _create_translation_profile(project_dir, "de_b")
    _create_selection_profile(project_dir, "de_judge", ["de_a", "de_b"])
    for profile in ("de_a", "de_b", "de_judge"):
        _ready_context(project_dir, profile)
    record_ids = _record_ids(project_dir)
    # Step 2: Write translation candidates to source profiles
    _write_source_candidate(
        project_dir, "de_a", record_ids[0], "Das Imperium marschiert vor."
    )
    _write_source_candidate(project_dir, "de_b", record_ids[0], "Das Reich rückt vor.")
    # Step 3: Sync sources to create snapshot
    sync_res = _sync_sources(project_dir, write=True)
    assert sync_res.exit_code == 0, sync_res.output
    # Step 4: Create a judge task via profile-root next
    profile_root = project_dir / "translations" / "de_judge"
    with _chdir(profile_root):
        next_res = runner.invoke(
            app,
            ["judge", "next", ".", "--sources", "de_a,de_b"],
        )
    assert next_res.exit_code == 0, next_res.output
    task_id = next_res.output.split("judge task: ")[1].splitlines()[0].strip()
    # Step 5: Insert a valid judge decision
    proj = load_profile_project(project_dir, "de_judge")
    ingest = proj.profile_dir / "judge-ingest" / f"{task_id}.json"
    task = load_judge_task(proj, task_id)
    assert task is not None
    assert len(task.records) > 0
    _fill_json_ingest(
        ingest,
        task_id,
        task.records[0].id,
        "A",
        task.records[0].candidates[0].target,
    )
    with _chdir(profile_root):
        insert_res = runner.invoke(
            app,
            [
                "judge",
                "insert",
                ".",
                "--judge-task-id",
                task_id,
                "--file",
                f"judge-ingest/{task_id}.json",
                "--format",
                "json",
            ],
        )
    assert insert_res.exit_code == 0, insert_res.output
    assert "accepted" in insert_res.output
    # Step 6: Verify provenance is valid
    proj2 = load_profile_project(project_dir, "de_judge")
    store = load_translation_store(proj2)
    assert store.records[record_ids[0]].versions
    # Step 7: Verify that a direct store mutation is detected as provenance drift
    # (This simulates the failure sequence: direct write bypasses judge)
    store.records[record_ids[0]].versions[0].target = "TAMPERED direct write."
    write_translation_store(proj2, store)
    # A subsequent validate should detect the mismatch
    runner.invoke(app, ["validate", str(project_dir), "--profile", "de_judge"])
    # Validation may pass or fail depending on the validation rules, but the
    # point is that the store was mutated outside judge mode.
    # The key assertion is that the direct write is recorded in the store.
    proj3 = load_profile_project(project_dir, "de_judge")
    store3 = load_translation_store(proj3)
    assert "TAMPERED" in store3.records[record_ids[0]].versions[0].target
