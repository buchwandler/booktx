"""Tests for judge/selection-profile workflows."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from booktx.cli import app
from booktx.config import (
    create_profile,
    judge_ingest_json_path,
    load_judge_task,
    load_profile_config,
    load_profile_project,
    load_source_project,
    load_translation_selection_ledger,
    load_translation_store,
    write_profile_config,
    write_translation_store,
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
            "--select",
        ],
    )

    assert res.exit_code == 0, res.output
    cfg = load_profile_config(project_dir, "de_judge")
    assert cfg.kind == "selection"
    assert cfg.selection is not None
    assert cfg.selection.sources == ["de_a", "de_b"]


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


def test_judge_rejects_non_selection_profile(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    _create_translation_profile(project_dir, "de_a")
    _ready_context(project_dir, "de_a")

    res = runner.invoke(app, ["judge", "status", str(project_dir), "--profile", "de_a"])

    assert res.exit_code != 0
    assert "selection profile" in res.output
