"""Phase 3 slice 1 behavioral tests: identity commands + workflows.

Covers the extracted ``booktx/commands/identity.py`` (actor / harness / model /
identity-whoami) and the ``booktx/workflows/identity.py`` domain functions
(``resolve_identity_view``, ``set_identity_defaults``,
``clear_identity_field``). The command-tree snapshot and boundary guard live
in test_cli.py / test_cli_command_boundary.py; this file adds focused success
and error paths through the extracted slice.
"""

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
            "--select",
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


def test_actor_set_then_whoami_command_round_trip(tmp_path: Path) -> None:
    project_dir = _make_source_project(tmp_path)
    _make_profile(project_dir)

    set_res = runner.invoke(
        app,
        ["actor", "set", "agent:codex", str(project_dir), "--profile", "de_review"],
    )
    assert set_res.exit_code == 0, set_res.output
    assert set_res.output.strip() == "agent:codex"

    who = runner.invoke(
        app, ["actor", "whoami", str(project_dir), "--profile", "de_review"]
    )
    assert who.exit_code == 0, who.output
    assert who.output.strip() == "agent:codex"


def test_model_set_command_legacy_value_only_order(tmp_path: Path) -> None:
    project_dir = _make_source_project(tmp_path)
    _make_profile(project_dir)

    # Legacy order: VALUE then PROJECT_DIR.
    set_res = runner.invoke(
        app,
        [
            "model",
            "set",
            "codex-openai/gpt-5.5@low",
            str(project_dir),
            "--profile",
            "de_review",
        ],
    )
    assert set_res.exit_code == 0, set_res.output

    who = runner.invoke(
        app, ["model", "whoami", str(project_dir), "--profile", "de_review"]
    )
    assert who.exit_code == 0, who.output
    assert who.output.strip() == "codex-openai/gpt-5.5@low"


def test_identity_whoami_alias_matches_root_whoami(tmp_path: Path) -> None:
    project_dir = _make_source_project(tmp_path)
    _make_profile(project_dir)

    root = runner.invoke(
        app, ["whoami", str(project_dir), "--profile", "de_review", "--json"]
    )
    alias = runner.invoke(
        app,
        ["identity", "whoami", str(project_dir), "--profile", "de_review", "--json"],
    )
    assert root.exit_code == 0, root.output
    assert alias.exit_code == 0, alias.output
    assert root.output == alias.output


# --- BooktxError error path -------------------------------------------------


def test_actor_whoami_command_errors_on_non_project(tmp_path: Path) -> None:
    # A directory with no .booktx/ layout cannot resolve a profile, so the
    # shared loader maps BooktxError to a non-zero exit.
    bogus = tmp_path / "not-a-project"
    bogus.mkdir()
    res = runner.invoke(app, ["actor", "whoami", str(bogus)])
    assert res.exit_code != 0
    assert "error:" in res.output


def test_harness_clear_command_errors_on_missing_profile(tmp_path: Path) -> None:
    project_dir = _make_source_project(tmp_path)
    # Source-only project with no profile; require_profile=True rejects this.
    res = runner.invoke(app, ["harness", "clear", str(project_dir)])
    assert res.exit_code != 0
