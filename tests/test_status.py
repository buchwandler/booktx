"""Direct unit tests for the booktx.status service.

The status service was extracted out of booktx.cli. These tests exercise the
typed models and ``build_status_snapshot`` without going through Typer, and
they pin the public ``status --json`` v1 shape (no ``_private`` keys, nested
``record_range``).
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from booktx.cli import app
from booktx.config import load_project
from booktx.status import (
    ChapterProgress,
    RecordRange,
    StatusBundle,
    StatusRuntimeIndex,
    StatusSnapshot,
    StatusTotals,
    build_status_snapshot,
    coverage_status,
    selected_chapter,
)

runner = CliRunner()

DOC = """\
# Chapter One

Alice met Bob. They were happy.

# Chapter Two

Bob left. Alice stayed.
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
            "2",
        ],
    )
    assert res.exit_code == 0, res.output
    ext = runner.invoke(app, ["extract", str(project_dir)])
    assert ext.exit_code == 0, ext.output
    return project_dir


def test_coverage_status_labels():
    assert coverage_status(total=3, translated=0, has_error=False) == "pending"
    assert coverage_status(total=3, translated=2, has_error=False) == "in_progress"
    assert coverage_status(total=3, translated=3, has_error=False) == "complete"
    assert coverage_status(total=3, translated=0, has_error=True) == "invalid"
    # error wins over complete
    assert coverage_status(total=3, translated=3, has_error=True) == "invalid"


def test_build_status_snapshot_returns_typed_bundle(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    proj = load_project(project_dir)

    bundle = build_status_snapshot(proj, context_exists=False, context_ready=False)

    assert isinstance(bundle, StatusBundle)
    assert isinstance(bundle.snapshot, StatusSnapshot)
    assert isinstance(bundle.index, StatusRuntimeIndex)
    assert isinstance(bundle.snapshot.totals, StatusTotals)

    # Runtime index carries the live lookup maps.
    assert bundle.index.source_chunks
    assert bundle.index.source_by_id
    assert bundle.index.record_to_chapter
    assert bundle.index.chunk_summaries


def test_snapshot_serializes_to_v1_shape_without_private_keys(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    proj = load_project(project_dir)

    bundle = build_status_snapshot(proj, context_exists=True, context_ready=True)
    dumped = bundle.snapshot.model_dump(mode="json")

    # Exactly the v1 public keys, nothing private leaks.
    assert set(dumped.keys()) == {
        "version",
        "project",
        "source",
        "context",
        "totals",
        "next",
        "chapters",
    }
    # Chapters use the nested record_range shape (v1 contract).
    nxt = dumped["next"]
    assert nxt is not None
    assert set(nxt["record_range"].keys()) == {"start", "end"}
    # The CLI JSON path must agree with this dump.
    res = runner.invoke(app, ["status", str(project_dir), "--json"])
    assert res.exit_code == 0, res.output
    cli_dumped = json.loads(res.output)
    assert cli_dumped["totals"] == dumped["totals"]
    assert cli_dumped["source"] == dumped["source"]


def test_selected_chapter_returns_next_for_none(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    proj = load_project(project_dir)

    bundle = build_status_snapshot(proj, context_exists=False, context_ready=False)
    first = selected_chapter(bundle, None)
    assert isinstance(first, ChapterProgress)
    assert isinstance(first.record_range, RecordRange)
    assert first.records_remaining > 0

    # Unknown id resolves to None; the CLI wrapper owns the die-on-unknown UX.
    assert selected_chapter(bundle, "does-not-exist") is None
