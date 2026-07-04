"""Phase 3 slice 5 behavioral tests: context commands + workflows.

Covers the extracted ``booktx/commands/context.py`` (init / questions /
status / render / answer / recommend / approve / add-question /
questionnaire / add-term / remove-term / reset-term / mandate-term /
audit-term / mark-ready / export-pack / import-pack / import-md /
chapter-note) and the matching workflow module. The command-tree snapshot
+ boundary guard live in test_cli.py / test_cli_command_boundary.py;
profile-root isolation is covered in test_cli_isolation.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from booktx.cli import app
from booktx.errors import BooktxError
from booktx.workflows.context import (
    add_or_update_term_workflow,
    add_question_workflow,
    load_context_or_die,
    mark_ready_workflow,
    remove_term_workflow,
)

runner = CliRunner()

DOC = """\
# Demo

Alice met Bob. They were happy.
"""


def _make_project(tmp_path: Path) -> Path:
    src = tmp_path / "book.md"
    src.write_text(DOC, encoding="utf-8")
    project_dir = tmp_path / "book"
    res = runner.invoke(
        app,
        ["init", str(project_dir), "--source-file", str(src), "--source-lang", "en"],
    )
    assert res.exit_code == 0, res.output
    return project_dir


def _add_profile(project_dir: Path, name: str = "de_ctx") -> None:
    res = runner.invoke(
        app,
        [
            "profile",
            "create",
            str(project_dir),
            name,
            "--target",
            "de",
            "--model",
            "human",
        ],
    )
    assert res.exit_code == 0, res.output


def _init_context(project_dir: Path) -> None:
    res = runner.invoke(
        app,
        [
            "context",
            "init",
            str(project_dir),
            "--profile",
            "de_ctx",
            "--non-interactive",
        ],
    )
    assert res.exit_code == 0, res.output


def _ready_project(tmp_path: Path) -> Path:
    project_dir = _make_project(tmp_path)
    _add_profile(project_dir)
    _init_context(project_dir)
    return project_dir


def _answer_core(project_dir: Path) -> None:
    answers = [
        ("Q001", "de-DE"),
        ("Q002", "balanced"),
        ("Q003", "neutral"),
        ("Q004", "natural dialogue"),
        ("Q005", "keep Apt names"),
        ("Q006", "translate world terms"),
        ("Q012", "error"),
    ]
    for qid, text in answers:
        res = runner.invoke(
            app,
            [
                "context",
                "answer",
                str(project_dir),
                "--profile",
                "de_ctx",
                qid,
                "--text",
                text,
            ],
        )
        assert res.exit_code == 0, res.output


# --- workflow function success paths ---------------------------------------


def test_load_context_or_die_missing_raises(tmp_path: Path) -> None:
    project_dir = _make_project(tmp_path)
    _add_profile(project_dir)
    from booktx.config import load_project

    with pytest.raises(BooktxError, match="missing"):
        load_context_or_die(load_project(project_dir, profile="de_ctx"))


def test_add_question_workflow_appends_required_question(tmp_path: Path) -> None:
    project_dir = _ready_project(tmp_path)
    from booktx.config import load_project

    proj = load_project(project_dir, profile="de_ctx")
    ctx = load_context_or_die(proj)
    message = add_question_workflow(
        proj,
        ctx,
        topic="tone",
        question="What tone should the dialogue use?",
        required=True,
        origin="agent_review",
        recommendation="natural",
        reason="default register",
        source="seed",
        question_id=None,
        allow_duplicate=False,
    )
    assert message.startswith("added question")
    assert any(q for q in ctx.questions if q.topic == "tone" and q.required)


def test_remove_term_workflow_missing_raises(tmp_path: Path) -> None:
    project_dir = _ready_project(tmp_path)
    from booktx.config import load_project

    proj = load_project(project_dir, profile="de_ctx")
    ctx = load_context_or_die(proj)
    with pytest.raises(BooktxError, match="no glossary entry"):
        remove_term_workflow(proj, ctx, source="MissingTerm", missing_ok=False)


def test_add_or_update_term_workflow_creates_new_entry(tmp_path: Path) -> None:
    project_dir = _ready_project(tmp_path)
    from booktx.config import load_project

    proj = load_project(project_dir, profile="de_ctx")
    ctx = load_context_or_die(proj)
    message = add_or_update_term_workflow(
        proj,
        ctx,
        source="Alice",
        target="Alicia",
        forbid=None,
        append_forbid=None,
        clear_forbidden=False,
        category="character",
        notes="main character",
        enforce="error",
        source_variant=None,
        target_variant=None,
        require_target=True,
        allow_disable_enforcement=False,
    )
    assert message == "updated binding term: Alice"
    entry = next(e for e in ctx.glossary if e.source == "Alice")
    assert entry.target == "Alicia"
    assert entry.enforce == "error"


def test_mark_ready_workflow_blocks_with_open_required(tmp_path: Path) -> None:
    project_dir = _ready_project(tmp_path)
    from booktx.config import load_project

    proj = load_project(project_dir, profile="de_ctx")
    ctx = load_context_or_die(proj)
    # No required questions have been answered yet.
    with pytest.raises(BooktxError, match="unresolved or unapproved"):
        mark_ready_workflow(proj, ctx, force=False, reason="")


# --- Typer command success paths (CliRunner) -------------------------------


def test_context_init_command_creates_files(tmp_path: Path) -> None:
    project_dir = _ready_project(tmp_path)
    # init already happened in _ready_project; verify the files exist.
    from booktx.config import load_project
    from booktx.context import context_markdown_path, context_path

    proj = load_project(project_dir, profile="de_ctx")
    assert context_path(proj).is_file()
    assert context_markdown_path(proj).is_file()
    data = json.loads(context_path(proj).read_text("utf-8"))
    assert data["ready"] is False


def test_context_status_command(tmp_path: Path) -> None:
    project_dir = _ready_project(tmp_path)
    res = runner.invoke(
        app, ["context", "status", str(project_dir), "--profile", "de_ctx"]
    )
    assert res.exit_code == 0, res.output
    assert "Status:" in res.output
    assert "open_required=" in res.output


def test_context_add_term_command_success(tmp_path: Path) -> None:
    project_dir = _ready_project(tmp_path)
    res = runner.invoke(
        app,
        [
            "context",
            "add-term",
            str(project_dir),
            "--profile",
            "de_ctx",
            "Alice",
            "--target",
            "Alicia",
            "--enforce",
            "error",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "updated advisory term: Alice" in res.output


def test_context_questions_command(tmp_path: Path) -> None:
    project_dir = _ready_project(tmp_path)
    res = runner.invoke(
        app, ["context", "questions", str(project_dir), "--profile", "de_ctx"]
    )
    assert res.exit_code == 0, res.output
    assert "Q001" in res.output


# --- BooktxError error paths ------------------------------------------------


def test_context_add_term_enforce_off_refused(tmp_path: Path) -> None:
    project_dir = _ready_project(tmp_path)
    res = runner.invoke(
        app,
        [
            "context",
            "add-term",
            str(project_dir),
            "--profile",
            "de_ctx",
            "Alice",
            "--target",
            "Alicia",
            "--require-target",
            "--enforce",
            "off",
        ],
    )
    assert res.exit_code != 0
    assert "error:" in res.output
    assert "refusing to disable a mandatory glossary rule" in res.output


def test_context_mark_ready_force_requires_reason(tmp_path: Path) -> None:
    project_dir = _ready_project(tmp_path)
    res = runner.invoke(
        app,
        ["context", "mark-ready", str(project_dir), "--profile", "de_ctx", "--force"],
    )
    assert res.exit_code != 0
    assert "error:" in res.output
    assert "--force requires --reason" in res.output


def test_context_add_question_duplicate_rejected(tmp_path: Path) -> None:
    project_dir = _ready_project(tmp_path)
    res = runner.invoke(
        app,
        [
            "context",
            "add-question",
            str(project_dir),
            "--profile",
            "de_ctx",
            "--topic",
            "tone",
            "--question",
            "What tone?",
        ],
    )
    assert res.exit_code == 0, res.output
    # Second identical question should be rejected.
    res2 = runner.invoke(
        app,
        [
            "context",
            "add-question",
            str(project_dir),
            "--profile",
            "de_ctx",
            "--topic",
            "tone",
            "--question",
            "What tone?",
        ],
    )
    assert res2.exit_code != 0
    assert "duplicate question" in res2.output


def test_context_chapter_note_replace_all_conflict(tmp_path: Path) -> None:
    project_dir = _ready_project(tmp_path)
    res = runner.invoke(
        app,
        [
            "context",
            "chapter-note",
            str(project_dir),
            "--profile",
            "de_ctx",
            "0001",
            "--title",
            "First",
            "--replace-all",
            "--replace-decisions",
        ],
    )
    assert res.exit_code != 0
    assert "conflicts with" in res.output
