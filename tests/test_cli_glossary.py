from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from booktx.cli import app

runner = CliRunner()


def _make_project(tmp_path: Path) -> Path:
    source = tmp_path / "book.md"
    source.write_text("# One\n\nHello.\n", encoding="utf-8")
    project_dir = tmp_path / "book"
    init = runner.invoke(
        app,
        ["init", str(project_dir), "--target", "de", "--source-file", str(source)],
    )
    assert init.exit_code == 0, init.output
    extract = runner.invoke(app, ["extract", str(project_dir)])
    assert extract.exit_code == 0, extract.output
    return project_dir


def _make_context_project(tmp_path: Path) -> Path:
    project_dir = _make_project(tmp_path)
    init = runner.invoke(
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
    assert init.exit_code == 0, init.output
    ready = runner.invoke(
        app,
        [
            "context",
            "mark-ready",
            str(project_dir),
            "--profile",
            "de_default",
            "--force",
            "--reason",
            "test setup",
        ],
    )
    assert ready.exit_code == 0, ready.output
    return project_dir


def test_glossary_add_flat_entry_exports_same_shard_as_termbase(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    add = runner.invoke(
        app,
        [
            "glossary",
            "add",
            str(project_dir),
            "mouldy principles",
            "--profile",
            "de_default",
            "--target",
            "schäbige Prinzipien",
            "--forbid",
            "schimmlige Prinzipien",
            "--require-target",
            "--enforce",
            "error",
        ],
    )
    assert add.exit_code == 0, add.output

    glossary_export = runner.invoke(
        app,
        ["glossary", "export", str(project_dir), "--profile", "de_default", "--stdout"],
    )
    termbase_export = runner.invoke(
        app,
        [
            "termbase",
            "export",
            str(project_dir),
            "--profile",
            "de_default",
            "--scope",
            "profile",
            "--stdout",
        ],
    )
    assert glossary_export.exit_code == 0, glossary_export.output
    assert termbase_export.exit_code == 0, termbase_export.output
    assert json.loads(glossary_export.output) == json.loads(termbase_export.output)


def test_glossary_file_input_and_termbase_validate_entry(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    entry_path = tmp_path / "entry.json"
    entry_path.write_text(
        json.dumps(
            {
                "id": "TERM-ANT-KINDEN",
                "kind": "contextual_term",
                "source": "kinden",
                "source_regex": r"\bAnt-kinden\b",
                "source_language": "en",
                "target_language": "de",
                "usage_rules": [
                    {
                        "id": "rule-ant",
                        "source_cue": "Ant-kinden",
                        "required_target_literals": ["Ameisenkinden"],
                        "forbidden_target_literals": ["Kinden"],
                        "severity": "error",
                        "prompt": "Use the species-specific term.",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    validate = runner.invoke(
        app, ["termbase", "validate-entry", "--input", str(entry_path)]
    )
    assert validate.exit_code == 0, validate.output
    assert "valid termbase entry" in validate.output

    add = runner.invoke(
        app,
        [
            "glossary",
            "add",
            str(project_dir),
            "--profile",
            "de_default",
            "--file",
            str(entry_path),
        ],
    )
    assert add.exit_code == 0, add.output

    export_res = runner.invoke(
        app,
        [
            "termbase",
            "export",
            str(project_dir),
            "--profile",
            "de_default",
            "--scope",
            "profile",
            "--stdout",
        ],
    )
    assert export_res.exit_code == 0, export_res.output
    payload = json.loads(export_res.output)
    assert payload["language_key"] == "de"
    assert payload["entries"][0]["kind"] == "contextual_term"


def test_glossary_usage_commands_persist_approved_contextual_variants(
    tmp_path: Path,
):
    project_dir = _make_context_project(tmp_path)
    mandate = runner.invoke(
        app,
        [
            "context",
            "mandate-term",
            str(project_dir),
            "--profile",
            "de_default",
            "Beetle-kinden",
            "--target",
            "Käferart",
            "--enforce",
            "error",
        ],
    )
    assert mandate.exit_code == 0, mandate.output

    add_variant = runner.invoke(
        app,
        [
            "glossary",
            "add-variant",
            str(project_dir),
            "Beetle-kinden",
            "--profile",
            "de_default",
            "--target",
            "Käferartige",
            "--usage",
            "vocative",
        ],
    )
    assert add_variant.exit_code == 0, add_variant.output

    set_usage = runner.invoke(
        app,
        [
            "glossary",
            "set-usage",
            str(project_dir),
            "Beetle-kinden",
            "--profile",
            "de_default",
            "--person-singular",
            "Angehörige der Käferart",
        ],
    )
    assert set_usage.exit_code == 0, set_usage.output

    from booktx.config import load_project
    from booktx.context import load_context

    context = load_context(load_project(project_dir, profile="de_default"))
    entry = next(item for item in context.glossary if item.source == "Beetle-kinden")
    assert entry.target == "Käferart"
    assert entry.target_variants == ["Käferartige", "Angehörige der Käferart"]
    assert entry.usage_notes == {
        "vocative": "Käferartige",
        "person_singular": "Angehörige der Käferart",
    }
