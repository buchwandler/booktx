"""Phase 3 slice 6 behavioral tests: review commands + workflows.

Covers the extracted ``booktx/commands/review.py`` (configure / status /
next / insert / activate / deactivate / revise-record / todo-next /
todo-status / todo-resume) and the matching workflow module. The
review-gap API boundary (workflows call review_status.build_review_gap_index,
commands do not) is enforced by ``tests/test_cli_command_boundary.py``.
The command-tree snapshot lives in ``tests/test_cli.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from booktx.cli import app
from booktx.errors import BooktxError
from booktx.workflows.review import (
    activate_review_workflow,
    create_next_review_task_workflow,
    deactivate_review_workflow,
    require_quality_review_enabled,
)

runner = CliRunner()

SOURCE = "# Chapter One\n\nAlice ran fast.\n"


def _make_project(tmp_path: Path) -> Path:
    src = tmp_path / "book.md"
    src.write_text(SOURCE, encoding="utf-8")
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
        ],
    )
    assert res.exit_code == 0, res.output
    ext = runner.invoke(app, ["extract", str(project_dir)])
    assert ext.exit_code == 0, ext.output
    return project_dir


def _enable_quality_review_command(project_dir: Path) -> None:
    """Enable quality review via the ``review configure`` command."""
    res = runner.invoke(
        app,
        [
            "review",
            "configure",
            str(project_dir),
            "--enable",
            "--pass",
            "1",
            "--name",
            "Flow review",
            "--mode",
            "manual",
            "--enforce",
            "warn",
        ],
    )
    assert res.exit_code == 0, res.output


# --- workflow function success / error paths -------------------------------


def test_require_quality_review_enabled_raises_when_disabled(tmp_path: Path) -> None:
    project_dir = _make_project(tmp_path)
    from booktx.config import load_project

    with pytest.raises(BooktxError, match="not enabled"):
        require_quality_review_enabled(load_project(project_dir))


def test_create_next_review_task_requires_enabled(tmp_path: Path) -> None:
    project_dir = _make_project(tmp_path)
    from booktx.cli_support import _project_status_snapshot
    from booktx.config import load_project
    from booktx.runtime import resolve_runtime

    proj = load_project(project_dir)
    runtime = resolve_runtime(project_dir)
    bundle = _project_status_snapshot(proj)
    with pytest.raises(BooktxError, match="not enabled"):
        create_next_review_task_workflow(
            proj,
            runtime,
            bundle=bundle,
            pass_number=1,
            chapter=None,
            max_words=900,
            selection="missing",
            base=None,
        )


def test_activate_review_unknown_record_raises(tmp_path: Path) -> None:
    project_dir = _make_project(tmp_path)
    from booktx.config import load_project

    proj = load_project(project_dir)
    with pytest.raises(BooktxError, match="has no stored translations"):
        activate_review_workflow(proj, record_ref="9999-999999", review_ref="R1.1")


def test_deactivate_review_no_active_raises(tmp_path: Path) -> None:
    project_dir = _make_project(tmp_path)
    from booktx.cli_support import _project_status_snapshot
    from booktx.config import load_project

    proj = load_project(project_dir)
    bundle = _project_status_snapshot(proj)
    # No store has been written yet, so the record lookup should fail.
    with pytest.raises(BooktxError, match="has no stored translations"):
        deactivate_review_workflow(proj, bundle=bundle, record_ref="1@1")


# --- Typer command success paths (CliRunner) --------------------------------


def test_review_configure_enable_command(tmp_path: Path) -> None:
    project_dir = _make_project(tmp_path)
    res = runner.invoke(
        app,
        [
            "review",
            "configure",
            str(project_dir),
            "--enable",
            "--pass",
            "1",
            "--name",
            "Flow review",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "quality review: enabled" in res.output
    assert "pass 1 Flow review" in res.output


def test_review_status_disabled_command(tmp_path: Path) -> None:
    project_dir = _make_project(tmp_path)
    res = runner.invoke(app, ["review", "status", str(project_dir)])
    assert res.exit_code == 0, res.output
    assert "quality review: disabled" in res.output


def test_review_status_json_command(tmp_path: Path) -> None:
    project_dir = _make_project(tmp_path)
    _enable_quality_review_command(project_dir)
    res = runner.invoke(app, ["review", "status", str(project_dir), "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["enabled"] is True
    assert 1 in payload["active_passes"]


def test_review_configure_show_unconfigured_command(tmp_path: Path) -> None:
    project_dir = _make_project(tmp_path)
    res = runner.invoke(app, ["review", "configure", str(project_dir), "--show"])
    assert res.exit_code == 0, res.output
    assert "quality review: not configured" in res.output


# --- BooktxError error paths ------------------------------------------------


def test_review_configure_rejects_enable_and_disable(tmp_path: Path) -> None:
    project_dir = _make_project(tmp_path)
    res = runner.invoke(
        app,
        [
            "review",
            "configure",
            str(project_dir),
            "--enable",
            "--disable",
        ],
    )
    assert res.exit_code != 0
    assert "error:" in res.output
    assert "use only one of --enable or --disable" in res.output


def test_review_next_rejects_when_not_enabled(tmp_path: Path) -> None:
    project_dir = _make_project(tmp_path)
    res = runner.invoke(app, ["review", "next", str(project_dir), "--pass", "1"])
    assert res.exit_code != 0
    assert "error:" in res.output
    assert "quality review is not enabled" in res.output


def test_review_todo_status_requires_selector(tmp_path: Path) -> None:
    project_dir = _make_project(tmp_path)
    _enable_quality_review_command(project_dir)
    res = runner.invoke(app, ["review", "todo-status", str(project_dir)])
    assert res.exit_code != 0
    assert "error:" in res.output
    assert "pass --review-todo-id or --latest" in res.output
