from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from booktx.cli import app
from booktx.config import load_profile_project
from booktx.store.status import build_store_status

runner = CliRunner()


def test_store_status_reports_new_profile_v3_and_profile_details(tmp_path: Path):
    source = tmp_path / "book.md"
    source.write_text("# Demo\n\nHello.\n", encoding="utf-8")
    project_dir = tmp_path / "book"
    runner.invoke(
        app,
        ["init", str(project_dir), "--source-file", str(source), "--source-lang", "en"],
    )
    created = runner.invoke(
        app,
        ["profile", "create", str(project_dir), "de", "--target", "de"],
    )
    assert created.exit_code == 0, created.output

    status = build_store_status(load_profile_project(project_dir, "de"))
    assert status["canonical_format"] == "v3"
    assert status["schema_version"] == 3
    assert status["legacy_copy"]["present"] is False

    result = runner.invoke(
        app,
        ["translate", "store-status", str(project_dir), "--profile", "de", "--json"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["canonical_format"] == "v3"

    profile = runner.invoke(
        app, ["profile", "show", str(project_dir), "de", "--json"]
    )
    assert profile.exit_code == 0, profile.output
    assert json.loads(profile.output)["store_format"] == "v3"
