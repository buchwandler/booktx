"""Context terminology validation tests."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from booktx.cli import app
from booktx.config import init_project, load_project
from booktx.context import (
    ChapterContext,
    context_markdown_path,
    default_context,
    write_context,
    write_context_markdown,
)
from booktx.models import Chunk, Record
from booktx.validate import validate_project

runner = CliRunner()


def _src_chunk() -> Chunk:
    return Chunk(
        chunk_id="0001",
        source_language="en",
        target_language="de",
        records=[
            Record(
                id="0001-000001",
                source="The Wasp Empire has commenced its war against the Lowlands.",
            )
        ],
    )


def _write_project(tmp_path: Path, target: str = "die Niederlande") -> Path:
    proj = init_project(tmp_path / "book", target_language="de")
    proj.chunks_dir.mkdir(parents=True, exist_ok=True)
    proj.translated_dir.mkdir(parents=True, exist_ok=True)
    chunk = _src_chunk()
    (proj.chunks_dir / "0001.json").write_text(
        chunk.model_dump_json(), encoding="utf-8"
    )
    (proj.translated_dir / "0001.json").write_text(
        json.dumps(
            {
                "chunk_id": "0001",
                "records": [{"id": "0001-000001", "target": target}],
            }
        ),
        encoding="utf-8",
    )
    return proj.root


def _write_context(proj_path: Path, enforce: str = "error") -> None:
    from booktx.context import load_seed_template

    proj = load_project(proj_path, profile="de_default")
    ctx = default_context(proj)
    # Load Shadows-of-Apt template for these tests.
    extra_q, extra_g = load_seed_template("shadows_of_apt")
    ctx.questions.extend(extra_q)
    ctx.glossary.extend(extra_g)
    for entry in ctx.glossary:
        if entry.source == "Lowlands":
            entry.enforce = enforce  # type: ignore[assignment]
    write_context(proj, ctx)
    write_context_markdown(proj, ctx)


def test_forbidden_term_used_error_fails_report(tmp_path: Path):
    proj_path = _write_project(tmp_path)
    _write_context(proj_path, enforce="error")
    report = validate_project(load_project(proj_path, profile="de_default"))
    assert not report.passed
    finding = next(f for f in report.findings if f.rule == "forbidden_term_used")
    assert finding.severity == "error"
    assert finding.record_id == "0001-000001"
    assert "Lowlands" in finding.message
    assert "Niederlande" in finding.message


def test_forbidden_term_used_warn_passes_with_warning(tmp_path: Path):
    proj_path = _write_project(tmp_path)
    _write_context(proj_path, enforce="warn")
    report = validate_project(load_project(proj_path, profile="de_default"))
    assert report.passed
    finding = next(f for f in report.findings if f.rule == "forbidden_term_used")
    assert finding.severity == "warn"


def test_forbidden_term_enforce_off_emits_no_finding(tmp_path: Path):
    proj_path = _write_project(tmp_path)
    _write_context(proj_path, enforce="off")
    report = validate_project(load_project(proj_path, profile="de_default"))
    assert report.passed
    assert "forbidden_term_used" not in {f.rule for f in report.findings}


def test_missing_context_keeps_existing_validate_behavior(tmp_path: Path):
    proj_path = _write_project(tmp_path)
    report = validate_project(load_project(proj_path, profile="de_default"))
    assert report.passed
    assert report.findings == []


def test_forbidden_target_only_checked_when_source_term_matches(tmp_path: Path):
    proj_path = _write_project(tmp_path, target="die Niederlande")
    proj = load_project(proj_path, profile="de_default")
    chunk = Chunk(
        chunk_id="0001",
        source_language="en",
        target_language="de",
        records=[Record(id="0001-000001", source="A different region.")],
    )
    (proj.chunks_dir / "0001.json").write_text(
        chunk.model_dump_json(), encoding="utf-8"
    )
    _write_context(proj_path, enforce="error")
    report = validate_project(load_project(proj_path, profile="de_default"))
    assert report.passed
    assert "forbidden_term_used" not in {f.rule for f in report.findings}


def test_validate_cli_exits_nonzero_for_error_enforcement(tmp_path: Path):
    proj_path = _write_project(tmp_path)
    _write_context(proj_path, enforce="error")
    res = runner.invoke(app, ["validate", str(proj_path), "--profile", "de_default"])
    assert res.exit_code == 1
    assert "forbidden_term_used" in res.output


def test_validate_cli_passes_with_warning_for_warn_enforcement(tmp_path: Path):
    proj_path = _write_project(tmp_path)
    _write_context(proj_path, enforce="warn")
    res = runner.invoke(app, ["validate", str(proj_path), "--profile", "de_default"])
    assert res.exit_code == 0, res.output
    assert "forbidden_term_used" in res.output
    assert "warnings=1" in res.output


def test_validate_cli_fail_on_warnings_exits_nonzero(tmp_path: Path):
    proj_path = _write_project(tmp_path)
    _write_context(proj_path, enforce="warn")
    res = runner.invoke(
        app,
        ["validate", str(proj_path), "--profile", "de_default", "--fail-on-warnings"],
    )
    assert res.exit_code == 1, res.output
    assert "forbidden_term_used" in res.output
    assert "warnings=1" in res.output


# --- context render drift diagnostics --------------------------------------

_MD_WITH_0006 = "## Chapter notes\n\n### 0006 — TWO\n- Decision: keep Apt\n"


def _drift_project(
    tmp_path: Path,
    *,
    json_chapters: list[ChapterContext] | None = None,
    md_text: str | None = None,
) -> Path:
    proj = init_project(tmp_path / "book", target_language="de")
    ctx = default_context(proj)
    if json_chapters is not None:
        ctx.chapter_contexts = list(json_chapters)
    write_context(proj, ctx)
    if md_text is not None:
        context_markdown_path(proj).write_text(md_text, encoding="utf-8")
    else:
        write_context_markdown(proj, ctx)
    return proj.root


def _drift_finding(report):
    return next((f for f in report.findings if f.rule == "context_render_drift"), None)


def test_validate_reports_missing_markdown_only_chapter(tmp_path: Path):
    proj_path = _drift_project(tmp_path, json_chapters=[], md_text=_MD_WITH_0006)
    report = validate_project(load_project(proj_path, profile="de_default"))
    finding = _drift_finding(report)
    assert finding is not None
    assert "missing_in_json=0006" in finding.message
    assert "import-md" in finding.message


def test_validate_reports_conflicting_existing_chapter(tmp_path: Path):
    proj_path = _drift_project(
        tmp_path,
        json_chapters=[ChapterContext(chapter_id="0006", title="TWO")],
        md_text=_MD_WITH_0006,
    )
    report = validate_project(load_project(proj_path, profile="de_default"))
    finding = _drift_finding(report)
    assert finding is not None
    assert "conflicting=0006" in finding.message
    assert "import-md" in finding.message


def test_validate_safe_render_drift_suggests_render_write(tmp_path: Path):
    proj_path = _drift_project(tmp_path)
    proj = load_project(proj_path, profile="de_default")
    # Change rendered Markdown outside the chapter notes section.
    md = context_markdown_path(proj).read_text("utf-8") + "\norphan line\n"
    context_markdown_path(proj).write_text(md, encoding="utf-8")
    report = validate_project(proj)
    finding = _drift_finding(report)
    assert finding is not None
    assert "context render . --write" in finding.message
    assert "import-md" not in finding.message


def test_validate_unsafe_drift_does_not_suggest_bare_render_write(tmp_path: Path):
    proj_path = _drift_project(tmp_path, json_chapters=[], md_text=_MD_WITH_0006)
    report = validate_project(load_project(proj_path, profile="de_default"))
    finding = _drift_finding(report)
    assert finding is not None
    # The bare safe suggestion must not appear for unsafe drift.
    assert "context render . --write`" not in finding.message


def test_validate_ignores_crlf_vs_lf_drift(tmp_path: Path):
    proj_path = _drift_project(tmp_path)
    proj = load_project(proj_path, profile="de_default")
    raw = context_markdown_path(proj).read_bytes()
    md = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    md = md.replace("\n", "\r\n")
    context_markdown_path(proj).write_bytes(md.encode("utf-8"))
    report = validate_project(proj)
    assert _drift_finding(report) is None


# --- whole-term forbidden matching -----------------------------------------


def test_forbidden_target_does_not_match_substring_inside_word(tmp_path: Path):
    """Forbidden 'Reich' must not match 'zahlreiche'."""
    proj_path = _write_project(tmp_path, target="zahlreiche Siege")
    proj = load_project(proj_path, profile="de_default")
    from booktx.context import TranslationContext

    ctx = TranslationContext(
        source_language="en",
        target_language="de",
        glossary=[
            {
                "source": "Lowlands",
                "target": None,
                "forbidden_targets": ["Reich"],
                "enforce": "error",
                "category": "term",
                "status": "approved",
                "notes": "",
                "examples": [],
                "case_sensitive": False,
            }
        ],
    )
    from booktx.context import write_context

    write_context(proj, ctx)
    report = validate_project(proj)
    assert report.passed


def test_forbidden_target_matches_standalone_word_with_punctuation(tmp_path: Path):
    """Forbidden 'Reich' must match 'das Reich' and '(Reich)'."""
    proj_path = _write_project(tmp_path, target="das Reich")
    proj = load_project(proj_path, profile="de_default")
    from booktx.context import TranslationContext

    ctx = TranslationContext(
        source_language="en",
        target_language="de",
        glossary=[
            {
                "source": "Lowlands",
                "target": None,
                "forbidden_targets": ["Reich"],
                "enforce": "error",
                "category": "term",
                "status": "approved",
                "notes": "",
                "examples": [],
                "case_sensitive": False,
            }
        ],
    )
    from booktx.context import write_context

    write_context(proj, ctx)
    report = validate_project(proj)
    assert not report.passed


def test_forbidden_target_case_insensitive_whole_word(tmp_path: Path):
    """Case-insensitive 'reich' must match 'REICH' standalone."""
    proj_path = _write_project(tmp_path, target="REICH")
    proj = load_project(proj_path, profile="de_default")
    from booktx.context import TranslationContext

    ctx = TranslationContext(
        source_language="en",
        target_language="de",
        glossary=[
            {
                "source": "Lowlands",
                "target": None,
                "forbidden_targets": ["reich"],
                "enforce": "error",
                "category": "term",
                "status": "approved",
                "notes": "",
                "examples": [],
                "case_sensitive": False,
            }
        ],
    )
    from booktx.context import write_context

    write_context(proj, ctx)
    report = validate_project(proj)
    assert not report.passed


def test_source_term_gate_uses_whole_term_not_substring(tmp_path: Path):
    """Source-term gate must not match 'Lowlands' inside 'LowlandsRegion'."""
    proj_path = _write_project(tmp_path, target="die Niederlande")
    proj = load_project(proj_path, profile="de_default")
    # Change the source text so the term is a substring.
    proj = load_project(proj_path, profile="de_default")
    chunk = Chunk(
        chunk_id="0001",
        source_language="en",
        target_language="de",
        records=[Record(id="0001-000001", source="The LowlandsRegion is safe.")],
    )
    (proj.chunks_dir / "0001.json").write_text(
        chunk.model_dump_json(), encoding="utf-8"
    )
    _write_context(proj_path, enforce="error")
    report = validate_project(proj)
    # The source term 'Lowlands' should not match 'LowlandsRegion'.
    assert report.passed
    assert "forbidden_term_used" not in {f.rule for f in report.findings}


def test_forbidden_phrase_matches_with_word_boundaries(tmp_path: Path):
    """Multi-word forbidden phrase matches with word boundaries."""
    proj_path = _write_project(tmp_path, target="die Wasp Empire")
    proj = load_project(proj_path, profile="de_default")
    from booktx.context import TranslationContext

    ctx = TranslationContext(
        source_language="en",
        target_language="de",
        glossary=[
            {
                "source": "Lowlands",
                "target": None,
                "forbidden_targets": ["Wasp Empire"],
                "enforce": "error",
                "category": "term",
                "status": "approved",
                "notes": "",
                "examples": [],
                "case_sensitive": False,
            }
        ],
    )
    from booktx.context import write_context

    write_context(proj, ctx)
    report = validate_project(proj)
    assert not report.passed


def test_forbidden_empty_term_returns_no_match(tmp_path: Path):
    """Empty forbidden term should never match."""
    from booktx.glossary_match import contains_term

    assert not contains_term("any text", "", case_sensitive=False)
    assert not contains_term("", "Reich", case_sensitive=False)
