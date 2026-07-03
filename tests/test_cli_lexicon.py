from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from booktx.cli import app
from booktx.config import (
    load_project,
    profile_lexicon_path,
    project_lexicon_path,
    write_translation_store,
)
from booktx.lexicon import TranslationLexicon, write_lexicon_shard
from booktx.models import (
    StoredTranslationRecordV2,
    TranslationCandidate,
    TranslationStoreV2,
)
from booktx.progress import source_record_sha256

runner = CliRunner()


def _make_project(tmp_path: Path, source_text: str | None = None) -> tuple[Path, Path]:
    source = tmp_path / "book.md"
    source.write_text(
        source_text
        or (
            "# Chapter One\n\n"
            "Like any Moth-kinden of standing she had learned the mouldy "
            "principles of magic.\n"
        ),
        encoding="utf-8",
    )
    project_dir = tmp_path / "book"
    init = runner.invoke(
        app,
        ["init", str(project_dir), "--target", "de", "--source-file", str(source)],
    )
    assert init.exit_code == 0, init.output
    extract = runner.invoke(app, ["extract", str(project_dir)])
    assert extract.exit_code == 0, extract.output
    profile_root = project_dir / "translations" / "de_default"
    return project_dir, profile_root


def _store_record(project_dir: Path, target: str) -> None:
    project = load_project(project_dir)
    chunk = json.loads(next(project.chunks_dir.glob("*.json")).read_text("utf-8"))
    record = next(
        (
            item
            for item in chunk["records"]
            if "mouldy principles" in item["source"].lower()
        ),
        chunk["records"][0],
    )
    write_translation_store(
        project,
        TranslationStoreV2(
            records={
                record["id"]: StoredTranslationRecordV2(
                    chunk_id=int(record["id"].split("-", 1)[0]),
                    part_id=int(record["id"].split("-", 1)[1]),
                    source_sha256=source_record_sha256(record["source"]),
                    source=record["source"],
                    active_version="1.1",
                    versions=[
                        TranslationCandidate(
                            version=1,
                            subversion=1,
                            version_ref="1.1",
                            target=target,
                            created_at="2026-07-03T08:00:00Z",
                            updated_at="2026-07-03T08:00:00Z",
                        )
                    ],
                )
            }
        ),
    )


def _global_shard(monkeypatch, tmp_path: Path) -> Path:
    root = tmp_path / "global-lexicon"
    monkeypatch.setenv("BOOKTX_LEXICON_DIR", str(root))
    return root / "de.json"


def test_lexicon_add_global_writes_shard_and_status(monkeypatch, tmp_path: Path):
    shard_path = _global_shard(monkeypatch, tmp_path)
    res = runner.invoke(
        app,
        [
            "lexicon",
            "add",
            "--scope",
            "global",
            "--language",
            "de",
            "--id",
            "LEX-MOULDY",
            "--source",
            "mouldy principles",
            "--source-regex",
            r"\bmouldy\s+principles(?:\s+of\s+magic)?\b",
            "--preferred",
            "schäbige Prinzipien",
            "--forbid",
            "schimmlige Prinzipien",
            "--approve",
        ],
    )
    assert res.exit_code == 0, res.output
    assert shard_path.is_file()

    status = runner.invoke(
        app,
        ["lexicon", "status", "--scope", "global", "--language", "de", "--json"],
    )
    assert status.exit_code == 0, status.output
    payload = json.loads(status.output)
    assert payload["active_entries"] == 1
    assert payload["layers"][0]["path"] == "$BOOKTX_LEXICON_DIR/de.json"


def test_lexicon_export_and_import_global(monkeypatch, tmp_path: Path):
    shard_path = _global_shard(monkeypatch, tmp_path)
    write_lexicon_shard(
        shard_path,
        TranslationLexicon(
            language_key="de",
            source_language="en",
            target_language="de",
            entries=[],
        ),
    )
    export_path = tmp_path / "lexicon-de.json"
    export_res = runner.invoke(
        app,
        [
            "lexicon",
            "export",
            "--scope",
            "global",
            "--language",
            "de",
            "--output",
            str(export_path),
        ],
    )
    assert export_res.exit_code == 0, export_res.output
    assert export_path.is_file()

    import_res = runner.invoke(
        app,
        [
            "lexicon",
            "import",
            "--scope",
            "global",
            "--language",
            "de",
            "--input",
            str(export_path),
            "--mode",
            "dry-run",
        ],
    )
    assert import_res.exit_code == 0, import_res.output
    assert "added=0 updated=0" in import_res.output

    payload = json.loads(export_path.read_text("utf-8"))
    payload["entries"].append(
        {
            "id": "LEX-IMPORTED",
            "source": "mouldy principles",
            "source_language": "en",
            "target_language": "de",
            "created_by_kind": "import",
            "updated_at": "2026-07-03T08:00:00Z",
        }
    )
    export_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    merge_res = runner.invoke(
        app,
        [
            "lexicon",
            "import",
            "--scope",
            "global",
            "--language",
            "de",
            "--input",
            str(export_path),
            "--mode",
            "merge",
            "--on-conflict",
            "overwrite",
        ],
    )
    assert merge_res.exit_code == 0, merge_res.output
    assert "added=1 updated=0" in merge_res.output

    replace_res = runner.invoke(
        app,
        [
            "lexicon",
            "import",
            "--scope",
            "global",
            "--language",
            "de",
            "--input",
            str(export_path),
            "--mode",
            "replace",
        ],
    )
    assert replace_res.exit_code == 0, replace_res.output
    assert "backup:" in replace_res.output
    assert list(shard_path.parent.glob("de.*.bak.json"))


def test_lexicon_scan_source_and_audit_jsonl(monkeypatch, tmp_path: Path):
    shard_path = _global_shard(monkeypatch, tmp_path)
    write_lexicon_shard(
        shard_path,
        TranslationLexicon.model_validate(
            {
                "language_key": "de",
                "source_language": "en",
                "target_language": "de",
                "entries": [
                    {
                        "id": "LEX-MOULDY",
                        "source": "mouldy principles",
                        "source_variants": ["mouldy principles of magic"],
                        "source_regex": r"\bmouldy\s+principles(?:\s+of\s+magic)?\b",
                        "source_language": "en",
                        "target_language": "de",
                        "target_preferred": ["schäbige Prinzipien"],
                        "target_forbidden": ["schimmligen Prinzipien"],
                        "created_by_kind": "user",
                    }
                ],
            }
        ),
    )
    project_dir, _ = _make_project(tmp_path)
    _store_record(
        project_dir,
        "Wie jede Mottenart hatte sie die schimmligen Prinzipien der Magie erlernt.",
    )

    scan = runner.invoke(app, ["lexicon", "scan-source", str(project_dir), "--jsonl"])
    assert scan.exit_code == 0, scan.output
    assert "LEX-MOULDY" in scan.output

    audit = runner.invoke(app, ["lexicon", "audit", str(project_dir), "--jsonl"])
    assert audit.exit_code == 0, audit.output
    assert "forbidden_target" in audit.output
    assert "schimmligen Prinzipien" in audit.output


def test_lexicon_status_merges_layers(monkeypatch, tmp_path: Path):
    shard_path = _global_shard(monkeypatch, tmp_path)
    project_dir, _ = _make_project(tmp_path)
    project = load_project(project_dir)
    write_lexicon_shard(
        shard_path,
        TranslationLexicon.model_validate(
            {
                "language_key": "de",
                "source_language": "en",
                "target_language": "de",
                "entries": [
                    {
                        "id": "LEX-SHARED",
                        "source": "mouldy principles",
                        "source_language": "en",
                        "target_language": "de",
                        "created_by_kind": "user",
                    }
                ],
            }
        ),
    )
    write_lexicon_shard(
        project_lexicon_path(project, "de"),
        TranslationLexicon.model_validate(
            {
                "language_key": "de",
                "source_language": "en",
                "target_language": "de",
                "entries": [
                    {
                        "id": "LEX-SHARED",
                        "status": "disabled",
                        "source": "mouldy principles",
                        "source_language": "en",
                        "target_language": "de",
                        "created_by_kind": "user",
                    }
                ],
            }
        ),
    )

    status = runner.invoke(app, ["lexicon", "status", str(project_dir), "--json"])
    assert status.exit_code == 0, status.output
    payload = json.loads(status.output)
    assert payload["disabled_entries"] == 1
    assert "LEX-SHARED" in payload["conflicts"]


def test_lexicon_isolated_global_reads_and_profile_writes(monkeypatch, tmp_path: Path):
    shard_path = _global_shard(monkeypatch, tmp_path)
    write_lexicon_shard(
        shard_path,
        TranslationLexicon(
            language_key="de",
            source_language="en",
            target_language="de",
            entries=[],
        ),
    )
    project_dir, profile_root = _make_project(tmp_path)
    monkeypatch.chdir(profile_root)

    status = runner.invoke(
        app, ["lexicon", "status", ".", "--scope", "global", "--language", "de"]
    )
    assert status.exit_code == 0, status.output
    assert str(tmp_path) not in status.output
    assert "$BOOKTX_LEXICON_DIR/de.json" in status.output

    blocked = runner.invoke(
        app,
        [
            "lexicon",
            "add",
            ".",
            "--scope",
            "global",
            "--language",
            "de",
            "--id",
            "LEX-BLOCKED",
            "--source",
            "mouldy principles",
        ],
    )
    assert blocked.exit_code != 0
    assert "blocked" in blocked.output.lower()

    allowed = runner.invoke(
        app,
        [
            "lexicon",
            "add",
            ".",
            "--scope",
            "profile",
            "--language",
            "de",
            "--id",
            "LEX-PROFILE",
            "--source",
            "mouldy principles",
        ],
    )
    assert allowed.exit_code == 0, allowed.output
    project = load_project(project_dir)
    assert profile_lexicon_path(project, "de").is_file()


def test_lexicon_locale_precedence_prefers_profile_locale_override(
    monkeypatch, tmp_path: Path
):
    _global_shard(monkeypatch, tmp_path)
    project_dir, _ = _make_project(tmp_path)
    create = runner.invoke(
        app,
        [
            "profile",
            "create",
            str(project_dir),
            "de_locale",
            "--target",
            "de",
            "--target-locale",
            "de-DE",
            "--select",
        ],
    )
    assert create.exit_code == 0, create.output
    project = load_project(project_dir, profile="de_locale")
    write_lexicon_shard(
        project_lexicon_path(project, "de"),
        TranslationLexicon.model_validate(
            {
                "language_key": "de",
                "source_language": "en",
                "target_language": "de",
                "entries": [
                    {
                        "id": "LEX-MOULDY",
                        "source": "mouldy principles",
                        "source_variants": ["mouldy principles of magic"],
                        "source_regex": r"\bmouldy\s+principles(?:\s+of\s+magic)?\b",
                        "source_language": "en",
                        "target_language": "de",
                        "target_forbidden": ["schimmligen Prinzipien"],
                        "created_by_kind": "user",
                    }
                ],
            }
        ),
    )
    write_lexicon_shard(
        profile_lexicon_path(project, "de-DE"),
        TranslationLexicon.model_validate(
            {
                "language_key": "de-DE",
                "source_language": "en",
                "target_language": "de",
                "target_locale": "de-DE",
                "entries": [
                    {
                        "id": "LEX-MOULDY",
                        "source": "mouldy principles",
                        "source_variants": ["mouldy principles of magic"],
                        "source_regex": r"\bmouldy\s+principles(?:\s+of\s+magic)?\b",
                        "source_language": "en",
                        "target_language": "de",
                        "target_locale": "de-DE",
                        "target_forbidden": ["modrigen Prinzipien"],
                        "created_by_kind": "user",
                    }
                ],
            }
        ),
    )
    _store_record(
        project_dir,
        "Wie jede Mottenart hatte sie die modrigen Prinzipien der Magie erlernt.",
    )

    audit = runner.invoke(
        app,
        ["lexicon", "audit", str(project_dir), "--profile", "de_locale", "--jsonl"],
    )
    assert audit.exit_code == 0, audit.output
    assert "modrigen Prinzipien" in audit.output
    assert "schimmligen Prinzipien" not in audit.output
