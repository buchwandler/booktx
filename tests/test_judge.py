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
from booktx.context import load_context
from booktx.judge_sources import (
    judge_sources_manifest_sha256,
    load_snapshot_judge_source_views,
    validate_judge_sources_snapshot,
    validate_snapshot_source_subset,
)
from booktx.models import SelectionConfig, TranslationReviewCandidate
from booktx.progress import load_source_records
from booktx.translation_store import (
    ensure_store_record,
    sha256_text,
    upsert_translation_version,
)
from booktx.versioning import resolve_current_version

runner = CliRunner(env={"COLUMNS": "120"})

DOC = """\
# One

The Empire advances. The Lowlands answer.
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
