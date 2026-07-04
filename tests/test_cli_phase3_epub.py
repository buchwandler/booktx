"""Phase 3 slice 3 behavioral tests: EPUB inspection commands + workflows.

Covers the extracted ``booktx/commands/epub.py`` (inspect / grep /
extract-text) and the ``booktx/workflows/epub.py`` domain functions
(``resolve_epub_output_dir``, ``select_xhtml_files``). Existing epub-command
coverage lives in test_epub_io.py / test_cli_isolation.py; this file adds
focused workflow success/error paths through the extracted slice.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from booktx.cli import app
from booktx.config import load_project
from booktx.errors import BooktxError
from booktx.workflows.epub import resolve_epub_output_dir, select_xhtml_files

runner = CliRunner()


def _epub_output_project(tmp_path: Path) -> Path:
    """Markdown profile project with a populated EPUB output directory."""
    src = tmp_path / "book.md"
    src.write_text("# One\n\nAlice ran fast.\n", encoding="utf-8")
    project_dir = tmp_path / "book"
    res = runner.invoke(
        app,
        ["init", str(project_dir), "--target", "de", "--source-file", str(src)],
    )
    assert res.exit_code == 0, res.output
    ext = runner.invoke(app, ["extract", str(project_dir)])
    assert ext.exit_code == 0, ext.output
    proj = load_project(project_dir, profile="de_default")
    assert proj.output_dir is not None
    proj.output_dir.mkdir(parents=True, exist_ok=True)
    (proj.output_dir / "chapter_1.xhtml").write_text(
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        "<body><p>Alice ran fast.</p></body></html>",
        encoding="utf-8",
    )
    (proj.output_dir / "chapter_2.xhtml").write_text(
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        "<body><p>Bob walked slowly.</p></body></html>",
        encoding="utf-8",
    )
    return project_dir


# --- workflow function success paths ---------------------------------------


def test_resolve_epub_output_dir_returns_dir(tmp_path: Path) -> None:
    project_dir = _epub_output_project(tmp_path)
    output_dir = resolve_epub_output_dir(
        load_project(project_dir, profile="de_default")
    )
    assert output_dir.is_dir()
    assert (output_dir / "chapter_1.xhtml").is_file()


def test_select_xhtml_files_lists_all(tmp_path: Path) -> None:
    project_dir = _epub_output_project(tmp_path)
    output_dir = resolve_epub_output_dir(
        load_project(project_dir, profile="de_default")
    )
    files = select_xhtml_files(output_dir)
    names = {f.name for f in files}
    assert {"chapter_1.xhtml", "chapter_2.xhtml"} <= names


def test_select_xhtml_files_filters_by_chapter(tmp_path: Path) -> None:
    project_dir = _epub_output_project(tmp_path)
    output_dir = resolve_epub_output_dir(
        load_project(project_dir, profile="de_default")
    )
    files = select_xhtml_files(output_dir, chapter="1")
    assert len(files) == 1
    assert files[0].name == "chapter_1.xhtml"


# --- workflow BooktxError error paths --------------------------------------


def test_resolve_epub_output_dir_raises_when_missing(tmp_path: Path) -> None:
    # A profile project with no built output directory.
    src = tmp_path / "book.md"
    src.write_text("# One\n\nAlice ran.\n", encoding="utf-8")
    project_dir = tmp_path / "book"
    runner.invoke(
        app,
        ["init", str(project_dir), "--target", "de", "--source-file", str(src)],
    )
    proj = load_project(project_dir, profile="de_default")
    # output_dir may exist as a path but be absent on disk, or be None.
    if proj.output_dir is not None and proj.output_dir.is_dir():
        # Remove it so the workflow must reject it.
        for child in proj.output_dir.iterdir():
            child.unlink() if child.is_file() else None
        proj.output_dir.rmdir()
    with pytest.raises(BooktxError):
        resolve_epub_output_dir(load_project(project_dir, profile="de_default"))


def test_select_xhtml_files_raises_for_unknown_chapter(tmp_path: Path) -> None:
    project_dir = _epub_output_project(tmp_path)
    output_dir = resolve_epub_output_dir(
        load_project(project_dir, profile="de_default")
    )
    with pytest.raises(BooktxError, match="unknown chapter|for chapter"):
        select_xhtml_files(output_dir, chapter="999")


# --- Typer command paths (CliRunner) ---------------------------------------


def test_epub_extract_text_command(tmp_path: Path) -> None:
    project_dir = _epub_output_project(tmp_path)
    res = runner.invoke(
        app, ["epub", "extract-text", str(project_dir), "--profile", "de_default"]
    )
    assert res.exit_code == 0, res.output
    assert "Alice ran fast." in res.output
    assert "Bob walked slowly." in res.output


def test_epub_grep_command(tmp_path: Path) -> None:
    project_dir = _epub_output_project(tmp_path)
    res = runner.invoke(
        app, ["epub", "grep", str(project_dir), "--profile", "de_default", "Bob"]
    )
    assert res.exit_code == 0, res.output
    assert "Bob walked slowly." in res.output


def test_epub_inspect_command_errors_without_output(tmp_path: Path) -> None:
    src = tmp_path / "book.md"
    src.write_text("# One\n\nAlice ran.\n", encoding="utf-8")
    project_dir = tmp_path / "book"
    runner.invoke(
        app,
        ["init", str(project_dir), "--target", "de", "--source-file", str(src)],
    )
    proj = load_project(project_dir, profile="de_default")
    if proj.output_dir is not None and proj.output_dir.is_dir():
        for child in list(proj.output_dir.iterdir()):
            if child.is_file():
                child.unlink()
        proj.output_dir.rmdir()
    res = runner.invoke(
        app, ["epub", "inspect", str(project_dir), "--profile", "de_default"]
    )
    assert res.exit_code != 0
    assert "error:" in res.output
