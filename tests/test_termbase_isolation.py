from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from booktx.cli import app
from booktx.config import load_project, profile_termbase_snapshot_path
from booktx.termbase import publish_termbase_snapshot, resolve_effective_termbase

runner = CliRunner()


def _make_project(tmp_path: Path) -> Path:
    source = tmp_path / "book.md"
    source.write_text("# One\n\nAnt-kinden march.\n", encoding="utf-8")
    project_dir = tmp_path / "book"
    init = runner.invoke(
        app,
        ["init", str(project_dir), "--target", "de", "--source-file", str(source)],
    )
    assert init.exit_code == 0, init.output
    context = runner.invoke(
        app,
        [
            "context",
            "init",
            str(project_dir),
            "--profile",
            "de_default",
            "--non-interactive",
        ],
    )
    assert context.exit_code == 0, context.output
    return project_dir


def _add_project_term(project_dir: Path, source: str, target: str) -> None:
    result = runner.invoke(
        app,
        [
            "termbase",
            "add",
            str(project_dir),
            "--profile",
            "de_default",
            "--scope",
            "project",
            "--id",
            f"term-{source.lower()}",
            "--source",
            source,
            "--preferred",
            target,
            "--preferred-policy",
            "required",
            "--approve",
        ],
    )
    assert result.exit_code == 0, result.output


def test_profile_root_effective_termbase_uses_frozen_snapshot(
    monkeypatch, tmp_path: Path
) -> None:
    project_dir = _make_project(tmp_path)
    _add_project_term(project_dir, "Ant-kinden", "Ameisenkinden")
    project = load_project(project_dir, profile="de_default")

    written = publish_termbase_snapshot(project)
    assert written == [profile_termbase_snapshot_path(project, "de")]

    _add_project_term(project_dir, "Wasp", "Wespe")
    monkeypatch.chdir(project.profile_dir)

    effective, layers = resolve_effective_termbase(project)

    assert [entry.source for entry in effective.entries] == ["Ant-kinden"]
    assert any(
        layer.path == profile_termbase_snapshot_path(project, "de") for layer in layers
    )
