from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from booktx.cli import app
from booktx.config import (
    BooktxError,
    canonical_language_key,
    global_lexicon_dir,
    global_lexicon_path,
    lexicon_language_keys,
    load_project,
    profile_lexicon_path,
    project_lexicon_path,
)
from booktx.lexicon import (
    LexiconEntry,
    TranslationLexicon,
    canonical_lexicon_json,
    load_lexicon_shard,
    merge_effective_lexicon,
    resolved_lexicon_layers,
    write_lexicon_shard,
)

runner = CliRunner()


def _make_project(tmp_path: Path) -> Path:
    source = tmp_path / "book.md"
    source.write_text("# One\n\nHello.\n", encoding="utf-8")
    project_dir = tmp_path / "book"
    res = runner.invoke(
        app,
        [
            "init",
            str(project_dir),
            "--target",
            "de",
            "--source-file",
            str(source),
        ],
    )
    assert res.exit_code == 0, res.output
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
        ],
    )
    assert create.exit_code == 0, create.output
    return project_dir


def _entry(
    entry_id: str, *, status: str = "approved", target_locale: str = ""
) -> LexiconEntry:
    return LexiconEntry(
        id=entry_id,
        status=status,
        source="mouldy principles",
        source_variants=["mouldy principles of magic"],
        source_regex=r"\bmouldy\s+principles(?:\s+of\s+magic)?\b",
        source_language="en",
        target_preferred=["schäbige Prinzipien", "schäbigen Prinzipien"],
        target_forbidden=["schimmlige Prinzipien", "schimmligen Prinzipien"],
        target_regex_forbidden=[r"\bschimmlig(?:e|en|er|es)?\s+Prinzipien\b"],
        target_language="de",
        target_locale=target_locale,
        sense="stale doctrine",
        rationale="Avoid literal mould.",
        created_at="2026-07-03T08:00:00Z",
        updated_at="2026-07-03T08:00:00Z",
        created_by_kind="user",
    )


def test_canonical_language_key_normalizes_common_forms():
    assert canonical_language_key("de") == "de"
    assert canonical_language_key("de-de") == "de-DE"
    assert canonical_language_key("zh-hant-tw") == "zh-Hant-TW"


@pytest.mark.parametrize("value", ["", "../de", "de.json", "de/DE", ".de", "de\\DE"])
def test_canonical_language_key_rejects_invalid_values(value: str):
    with pytest.raises(BooktxError):
        canonical_language_key(value)


def test_translation_lexicon_rejects_duplicate_entry_ids():
    with pytest.raises(ValidationError, match="duplicate lexicon entry id"):
        TranslationLexicon(
            language_key="de",
            source_language="en",
            target_language="de",
            entries=[_entry("LEX-ONE"), _entry("LEX-ONE")],
        )


def test_translation_lexicon_rejects_target_locale_mismatch():
    with pytest.raises(ValidationError, match="target_locale must match"):
        TranslationLexicon(
            language_key="de",
            source_language="en",
            target_language="de",
            entries=[_entry("LEX-ONE", target_locale="de-DE")],
        )


def test_lexicon_entry_rejects_invalid_timestamp():
    with pytest.raises(ValidationError, match="RFC 3339 UTC timestamp"):
        LexiconEntry(
            id="LEX-ONE",
            source="mouldy principles",
            source_language="en",
            target_language="de",
            updated_at="2026-07-03",
        )


def test_canonical_lexicon_json_sorts_entries_by_id():
    shard = TranslationLexicon(
        language_key="de",
        source_language="en",
        target_language="de",
        entries=[_entry("LEX-Z"), _entry("LEX-A")],
    )
    payload = json.loads(canonical_lexicon_json(shard))
    assert [entry["id"] for entry in payload["entries"]] == ["LEX-A", "LEX-Z"]


def test_write_and_load_lexicon_roundtrip(tmp_path: Path):
    path = tmp_path / "de.json"
    shard = TranslationLexicon(
        language_key="de",
        source_language="en",
        target_language="de",
        entries=[_entry("LEX-A")],
    )
    write_lexicon_shard(path, shard)
    loaded = load_lexicon_shard(path)
    assert loaded == shard


def test_merge_effective_lexicon_uses_whole_entry_override_and_tombstone(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("BOOKTX_LEXICON_DIR", str(tmp_path / "global-lexicon"))
    project_dir = _make_project(tmp_path)
    project = load_project(project_dir, profile="de_locale")

    write_lexicon_shard(
        global_lexicon_path("de"),
        TranslationLexicon(
            language_key="de",
            source_language="en",
            target_language="de",
            entries=[_entry("LEX-ONE"), _entry("LEX-TWO")],
        ),
    )
    write_lexicon_shard(
        project_lexicon_path(project, "de"),
        TranslationLexicon(
            language_key="de",
            source_language="en",
            target_language="de",
            entries=[
                _entry("LEX-ONE", status="disabled"),
                _entry("LEX-TWO").model_copy(
                    update={
                        "target_preferred": ["verstaubte Prinzipien"],
                        "target_forbidden": [],
                    }
                ),
            ],
        ),
    )

    layers = resolved_lexicon_layers(project, language_keys=["de"], scope="effective")
    effective = merge_effective_lexicon(layers, language_keys=["de"])

    by_id = {entry.id: entry for entry in effective.entries}
    assert by_id["LEX-ONE"].status == "disabled"
    assert by_id["LEX-TWO"].target_preferred == ["verstaubte Prinzipien"]
    assert by_id["LEX-TWO"].target_forbidden == []


def test_lexicon_language_keys_uses_profile_locale_sequence(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    project = load_project(project_dir, profile="de_locale")
    assert lexicon_language_keys(project) == ["de", "de-DE"]


def test_lexicon_paths_follow_expected_layout(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BOOKTX_LEXICON_DIR", str(tmp_path / "global-lexicon"))
    project_dir = _make_project(tmp_path)
    project = load_project(project_dir, profile="de_locale")

    assert global_lexicon_dir() == (tmp_path / "global-lexicon").resolve()
    assert global_lexicon_path("de").name == "de.json"
    assert (
        project_lexicon_path(project, "de")
        .as_posix()
        .endswith(".booktx/lexicon/de.json")
    )
    assert (
        profile_lexicon_path(project, "de-DE")
        .as_posix()
        .endswith("translations/de_locale/lexicon-overrides/de-DE.json")
    )
