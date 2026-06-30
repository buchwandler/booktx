from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from booktx.cli import app
from booktx.config import init_project
from booktx.workflows.root import mode_cmd

runner = CliRunner()


def test_root_workflow_mode_success(tmp_path: Path, capsys) -> None:
    project_dir = tmp_path / "book"
    init_project(project_dir, target_language="de")

    mode_cmd(project_dir, profile=None, as_json=False)

    out = capsys.readouterr().out
    assert "mode:" in out
    assert "profiles visible:" in out


def test_root_mode_command_success(tmp_path: Path) -> None:
    project_dir = tmp_path / "book"
    init_project(project_dir, target_language="de")

    res = runner.invoke(app, ["mode", str(project_dir)])

    assert res.exit_code == 0
    assert "mode:" in res.output


def test_root_mode_command_maps_error_to_nonzero(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    res = runner.invoke(app, ["mode", str(missing)])

    assert res.exit_code != 0
    assert "not a booktx project" in res.output or "Error" in res.output
