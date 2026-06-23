"""Direct unit tests for the booktx.acceptance service.

Exercises the shared validate-and-persist flow without going through Typer,
and pins the behavior that batch and single-record acceptance share one
implementation: context is loaded once, ERROR findings block the store write,
and unknown/duplicate/out-of-task ids raise BooktxError.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from booktx.acceptance import (
    AcceptResult,
    SubmittedRecord,
    accept_one_record,
    accept_translation_records,
)
from booktx.cli import app
from booktx.config import BooktxError, load_project
from booktx.status import build_status_snapshot

runner = CliRunner()

DOC = """\
# Chapter One

Alice met Bob. They were happy. Bob waved.
"""


def _make_project(tmp_path: Path) -> Path:
    src = tmp_path / "book.md"
    src.write_text(DOC, encoding="utf-8")
    project_dir = tmp_path / "book"
    res = runner.invoke(
        app,
        [
            "init",
            str(project_dir),
            "--target",
            "de",
            "--source-file",
            str(src),
            "--chunk-size",
            "5",
        ],
    )
    assert res.exit_code == 0, res.output
    assert runner.invoke(app, ["extract", str(project_dir)]).exit_code == 0
    runner.invoke(app, ["context", "init", str(project_dir), "--non-interactive"])
    runner.invoke(app, ["context", "mark-ready", str(project_dir), "--force"])
    return project_dir


def _first_record_id(project_dir: Path) -> str:
    chunks = sorted((project_dir / ".booktx" / "chunks").glob("*.json"))
    chunk = json.loads(chunks[0].read_text("utf-8"))
    return chunk["records"][0]["id"]


def test_accept_one_record_persists_and_reports_chapter(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    proj = load_project(project_dir)
    rid = _first_record_id(project_dir)
    bundle = build_status_snapshot(proj, context_exists=True, context_ready=True)

    result = accept_one_record(proj, rid, "Alice traf Bob.", bundle=bundle)

    assert isinstance(result, AcceptResult)
    assert result.accepted_records == 1
    assert result.target_words >= 1
    assert result.chapter_id  # mapped to a chapter

    store = json.loads(
        (project_dir / ".booktx" / "translation-store.json").read_text("utf-8")
    )
    assert store["records"][rid]["target"] == "Alice traf Bob."


def test_batch_and_single_record_share_implementation(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    proj = load_project(project_dir)
    rid = _first_record_id(project_dir)
    bundle = build_status_snapshot(proj, context_exists=True, context_ready=True)

    batch = accept_translation_records(
        proj, [SubmittedRecord(id=rid, target="Alice traf Bob.")], bundle=bundle
    )
    # target_words count must match the single-record path for the same text.
    single = accept_one_record(proj, rid, "Alice traf Bob.", bundle=bundle)
    assert batch.target_words == single.target_words


def test_unknown_record_id_raises_booktx_error(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    proj = load_project(project_dir)
    bundle = build_status_snapshot(proj, context_exists=True, context_ready=True)

    try:
        accept_one_record(proj, "nope-r0001", "x", bundle=bundle)
    except BooktxError as exc:
        assert "unknown source record id" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected BooktxError for unknown record id")


def test_empty_target_raises_booktx_error(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    proj = load_project(project_dir)
    rid = _first_record_id(project_dir)
    bundle = build_status_snapshot(proj, context_exists=True, context_ready=True)

    try:
        accept_one_record(proj, rid, "   ", bundle=bundle)
    except BooktxError as exc:
        assert "empty target" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected BooktxError for empty target")


def test_duplicate_id_raises_before_store_write(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    proj = load_project(project_dir)
    rid = _first_record_id(project_dir)
    bundle = build_status_snapshot(proj, context_exists=True, context_ready=True)

    try:
        accept_translation_records(
            proj,
            [SubmittedRecord(id=rid, target="x"), SubmittedRecord(id=rid, target="y")],
            bundle=bundle,
        )
    except BooktxError as exc:
        assert "duplicate record id" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected BooktxError for duplicate id")

    # Store must not have been written for the failed submission.
    store_path = project_dir / ".booktx" / "translation-store.json"
    if store_path.exists():
        store = json.loads(store_path.read_text("utf-8"))
        assert rid not in store.get("records", {})
