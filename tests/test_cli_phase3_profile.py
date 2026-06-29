"""Phase 3 slice 4 behavioral tests: version + profile commands and workflows.

Covers the extracted ``booktx/commands/version.py`` (current / list / select /
set-label / fork-context / show), ``booktx/commands/profile.py`` (create /
list / select / show / compare / migrate-current / create-pass-through), and
the matching workflow modules. The command-tree snapshot + boundary guard
live in test_cli.py / test_cli_command_boundary.py; profile-root isolation is
covered in test_cli_isolation.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from booktx.cli import app
from booktx.errors import BooktxError
from booktx.workflows.profile import (
    build_profile_detail_payload,
    compare_profile_record,
)
from booktx.workflows.version import version_current_payload, version_show_payload

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


def _add_profile(project_dir: Path, name: str = "de_test") -> None:
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


# --- version workflow success / error paths --------------------------------


def test_version_current_payload_empty_ledger(tmp_path: Path) -> None:
    project_dir = _make_source_project(tmp_path)
    _add_profile(project_dir)
    from booktx.config import load_profile_project

    payload = version_current_payload(load_profile_project(project_dir, "de_test"))
    assert payload["active_version"] is None
    assert payload["track_count"] == 0


def test_version_show_unknown_track_raises(tmp_path: Path) -> None:
    project_dir = _make_source_project(tmp_path)
    _add_profile(project_dir)
    from booktx.config import load_profile_project

    with pytest.raises(BooktxError, match="not found"):
        version_show_payload(load_profile_project(project_dir, "de_test"), "999")


# --- profile workflow success / error paths --------------------------------


def test_build_profile_detail_payload(tmp_path: Path) -> None:
    project_dir = _make_source_project(tmp_path)
    _add_profile(project_dir)
    payload = build_profile_detail_payload(project_dir, "de_test")
    assert payload["profile"] == "de_test"
    assert payload["target_language"] == "de"
    assert payload["kind"] == "translation"


def test_compare_profile_record_requires_two_profiles(tmp_path: Path) -> None:
    project_dir = _make_source_project(tmp_path)
    _add_profile(project_dir, "de_a")
    with pytest.raises(BooktxError, match="at least two"):
        compare_profile_record(project_dir, "de_a", "0001-000001")


# --- Typer command paths (CliRunner) ---------------------------------------


def test_version_current_command(tmp_path: Path) -> None:
    project_dir = _make_source_project(tmp_path)
    _add_profile(project_dir)
    res = runner.invoke(
        app, ["version", "current", str(project_dir), "--json"]
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["track_count"] == 0
    assert payload["active_version"] is None


def test_version_show_unknown_track_command_errors(tmp_path: Path) -> None:
    project_dir = _make_source_project(tmp_path)
    _add_profile(project_dir)
    res = runner.invoke(
        app, ["version", "show", str(project_dir), "999"]
    )
    assert res.exit_code != 0
    assert "not found" in res.output


def test_profile_create_then_list_command(tmp_path: Path) -> None:
    project_dir = _make_source_project(tmp_path)
    res = runner.invoke(
        app,
        [
            "profile",
            "create",
            str(project_dir),
            "de_one",
            "--target",
            "de",
            "--select",
        ],
    )
    assert res.exit_code == 0, res.output
    res = runner.invoke(app, ["profile", "list", str(project_dir), "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    names = {item["profile"] for item in payload["profiles"]}
    assert "de_one" in names


def test_profile_show_command_json(tmp_path: Path) -> None:
    project_dir = _make_source_project(tmp_path)
    _add_profile(project_dir, "de_show")
    res = runner.invoke(
        app,
        ["profile", "show", str(project_dir), "de_show", "--json"],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["profile"] == "de_show"
    assert payload["kind"] == "translation"


def test_profile_compare_errors_with_one_profile(tmp_path: Path) -> None:
    project_dir = _make_source_project(tmp_path)
    _add_profile(project_dir, "de_one")
    res = runner.invoke(
        app,
        [
            "profile",
            "compare",
            str(project_dir),
            "--profiles",
            "de_one",
            "--record",
            "1@1",
        ],
    )
    assert res.exit_code != 0
    assert "at least two" in res.output
