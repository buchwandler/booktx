"""Phase 3 slice 2 behavioral tests: source inspection commands + workflows.

Covers the extracted ``booktx/commands/source.py`` (status / record / chapter)
and the ``booktx/workflows/source.py`` domain functions
(``build_source_status_payload``, ``find_source_record``,
``collect_chapter_records``). Command-tree snapshot + boundary guard live in
test_cli.py / test_cli_command_boundary.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from booktx.cli import app
from booktx.errors import BooktxError
from booktx.workflows.source import (
    build_source_status_payload,
    collect_chapter_records,
    find_source_record,
)

runner = CliRunner()

DOC = """\
# Demo

Alice met Bob. They were happy.
"""


def _make_extracted_project(tmp_path: Path) -> Path:
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
    extract = runner.invoke(app, ["extract", str(project_dir)])
    assert extract.exit_code == 0, extract.output
    return project_dir


def _add_profile(project_dir: Path, name: str = "de_src") -> None:
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


def _first_record_id(project_dir: Path) -> str:
    from booktx.config import load_project
    from booktx.progress import load_source_records

    records = load_source_records(load_project(project_dir))
    assert records, "expected at least one source record"
    return records[0].record_id


# --- workflow function success paths ---------------------------------------


def test_build_source_status_payload_reports_counts(tmp_path: Path) -> None:
    project_dir = _make_extracted_project(tmp_path)
    from booktx.config import load_project

    payload = build_source_status_payload(load_project(project_dir))
    assert payload["source"] == "available"
    assert payload["format"] == "markdown"
    assert payload["records"] >= 1
    assert payload["chunks"] >= 1


def test_find_source_record_resolves_id_and_dotted_ref(tmp_path: Path) -> None:
    project_dir = _make_extracted_project(tmp_path)
    from booktx.config import load_project

    proj = load_project(project_dir)
    first_id = _first_record_id(project_dir)
    record = find_source_record(proj, first_id)
    assert record.record_id == first_id
    # A compact record ref "1@1" resolves to the same canonical id.
    assert find_source_record(proj, "1@1").record_id == first_id


def test_find_source_record_raises_on_unknown(tmp_path: Path) -> None:
    project_dir = _make_extracted_project(tmp_path)
    from booktx.config import load_project

    try:
        find_source_record(load_project(project_dir), "9999-999999")
    except BooktxError as exc:
        assert "unknown source record id" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected BooktxError for unknown record")


# --- Typer command success paths (CliRunner) -------------------------------


def test_source_status_command_json(tmp_path: Path) -> None:
    project_dir = _make_extracted_project(tmp_path)
    res = runner.invoke(app, ["source", "status", str(project_dir), "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["source"] == "available"
    assert payload["records"] >= 1


def test_source_record_command_block(tmp_path: Path) -> None:
    project_dir = _make_extracted_project(tmp_path)
    first_id = _first_record_id(project_dir)
    res = runner.invoke(
        app, ["source", "record", str(project_dir), first_id, "--format", "block"]
    )
    assert res.exit_code == 0, res.output
    assert f">>> {first_id}" in res.output


def test_source_chapter_command_json(tmp_path: Path) -> None:
    # The chapter command builds the translation-aware status snapshot, which
    # requires a selected profile (matches the original command's behavior).
    project_dir = _make_extracted_project(tmp_path)
    _add_profile(project_dir)
    from booktx.cli_support import _project_status_snapshot
    from booktx.config import load_project

    bundle = _project_status_snapshot(load_project(project_dir))
    chapter_id = next(iter(bundle.index.chapters_by_id))
    result = collect_chapter_records(bundle, chapter_id)
    assert result.chapter_id == chapter_id
    assert result.records

    res = runner.invoke(
        app,
        ["source", "chapter", str(project_dir), chapter_id, "--format", "json"],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["chapter_id"] == chapter_id
    assert payload["records"]


# --- BooktxError error paths ------------------------------------------------


def test_source_record_command_unknown_record_errors(tmp_path: Path) -> None:
    project_dir = _make_extracted_project(tmp_path)
    res = runner.invoke(app, ["source", "record", str(project_dir), "9999-999999"])
    assert res.exit_code != 0
    assert "error:" in res.output
    assert "unknown source record id" in res.output


def test_source_chapter_command_unknown_chapter_errors(tmp_path: Path) -> None:
    project_dir = _make_extracted_project(tmp_path)
    _add_profile(project_dir)
    res = runner.invoke(app, ["source", "chapter", str(project_dir), "9999"])
    assert res.exit_code != 0
    assert "unknown chapter id" in res.output


def test_source_record_bad_format_errors(tmp_path: Path) -> None:
    project_dir = _make_extracted_project(tmp_path)
    first_id = _first_record_id(project_dir)
    res = runner.invoke(
        app,
        ["source", "record", str(project_dir), first_id, "--format", "yaml"],
    )
    assert res.exit_code != 0
    assert "error:" in res.output
