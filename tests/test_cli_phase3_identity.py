"""Behavioral tests for the consolidated identity surface and workflows."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from booktx.cli import app
from booktx.config import identity_path, load_profile_project
from booktx.workflows.identity import (
    clear_identity_field,
    resolve_identity_view,
    set_identity_defaults,
)

runner = CliRunner()

DOC = """\
# Demo

Alice met Bob. They were happy.
"""


def _make_source_project(tmp_path: Path) -> Path:
    src = tmp_path / "book.md"
    src.write_text(DOC, encoding="utf-8")
    project_dir = tmp_path / "book"
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
    return project_dir


def _make_profile(project_dir: Path, name: str = "de_review") -> Path:
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
    return project_dir


# --- workflow function success paths ---------------------------------------


def test_set_identity_defaults_workflow_persists_and_returns(tmp_path: Path) -> None:
    project_dir = _make_source_project(tmp_path)
    _make_profile(project_dir)
    proj = load_profile_project(project_dir, "de_review")

    identity = set_identity_defaults(proj, actor="agent:codex")

    assert identity.actor == "agent:codex"
    # The resolved identity is written to the on-disk identity file.
    assert identity_path(proj).is_file()
    reloaded = resolve_identity_view(proj)
    assert reloaded.actor == "agent:codex"


def test_clear_identity_field_workflow_removes_file_when_all_default(
    tmp_path: Path,
) -> None:
    project_dir = _make_source_project(tmp_path)
    _make_profile(project_dir)
    proj = load_profile_project(project_dir, "de_review")

    set_identity_defaults(proj, actor="agent:codex", harness="pi")
    assert identity_path(proj).is_file()

    cleared = clear_identity_field(proj, "actor")
    assert cleared.actor != "agent:codex"
    # Clearing the remaining non-default field drops the file entirely.
    clear_identity_field(proj, "harness")
    assert not identity_path(proj).is_file()


# --- Typer command success paths (CliRunner) -------------------------------


def test_identity_set_updates_multiple_fields_and_root_whoami(tmp_path: Path) -> None:
    project_dir = _make_source_project(tmp_path)
    _make_profile(project_dir)

    set_res = runner.invoke(
        app,
        [
            "identity",
            "set",
            str(project_dir),
            "--profile",
            "de_review",
            "--actor",
            "agent:codex",
            "--harness",
            "pi",
        ],
    )
    assert set_res.exit_code == 0, set_res.output
    assert "agent:codex" in set_res.output
    assert "pi" in set_res.output

    who = runner.invoke(
        app, ["whoami", str(project_dir), "--profile", "de_review", "--json"]
    )
    assert who.exit_code == 0, who.output
    assert '"actor":"agent:codex"' in who.output.replace(" ", "")
    assert '"harness":"pi"' in who.output.replace(" ", "")


def test_identity_clear_without_flags_clears_all_fields(tmp_path: Path) -> None:
    project_dir = _make_source_project(tmp_path)
    _make_profile(project_dir)
    runner.invoke(
        app,
        [
            "identity",
            "set",
            str(project_dir),
            "--profile",
            "de_review",
            "--actor",
            "agent:codex",
            "--model",
            "codex-openai/gpt-5.5@low",
        ],
    )
    clear_res = runner.invoke(
        app,
        ["identity", "clear", str(project_dir), "--profile", "de_review"],
    )
    assert clear_res.exit_code == 0, clear_res.output
    proj = load_profile_project(project_dir, "de_review")
    assert not identity_path(proj).is_file()


# --- BooktxError error path -------------------------------------------------


def test_identity_set_command_errors_on_non_project(tmp_path: Path) -> None:
    # A directory with no .booktx/ layout cannot resolve a profile, so the
    # shared loader maps BooktxError to a non-zero exit.
    bogus = tmp_path / "not-a-project"
    bogus.mkdir()
    res = runner.invoke(app, ["identity", "set", str(bogus), "--actor", "agent:codex"])
    assert res.exit_code != 0
    assert "error:" in res.output


def test_identity_help_exposes_only_set_and_clear(tmp_path: Path) -> None:
    project_dir = _make_source_project(tmp_path)
    _make_profile(project_dir)
    res = runner.invoke(app, ["identity", "--help"])
    assert res.exit_code == 0, res.output
    assert "set" in res.output
    assert "clear" in res.output
    assert "whoami" not in res.output
