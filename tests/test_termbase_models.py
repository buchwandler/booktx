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
    global_termbase_dir,
    global_termbase_path,
    load_project,
    profile_termbase_path,
    project_termbase_path,
    termbase_language_keys,
)
from booktx.termbase import (
    TermbaseEntry,
    TermbaseUsageRule,
    TranslationTermbase,
    canonical_termbase_json,
    deterministic_context_id,
    load_termbase_shard,
    merge_effective_termbase,
    resolved_termbase_layers,
    write_termbase_shard,
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
) -> TermbaseEntry:
    return TermbaseEntry(
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


def test_translation_termbase_rejects_duplicate_entry_ids():
    with pytest.raises(ValidationError, match="duplicate termbase entry id"):
        TranslationTermbase(
            language_key="de",
            source_language="en",
            target_language="de",
            entries=[_entry("LEX-ONE"), _entry("LEX-ONE")],
        )


def test_translation_termbase_rejects_target_locale_mismatch():
    with pytest.raises(ValidationError, match="target_locale must match"):
        TranslationTermbase(
            language_key="de",
            source_language="en",
            target_language="de",
            entries=[_entry("LEX-ONE", target_locale="de-DE")],
        )


def test_termbase_entry_rejects_invalid_timestamp():
    with pytest.raises(ValidationError, match="RFC 3339 UTC timestamp"):
        TermbaseEntry(
            id="LEX-ONE",
            source="mouldy principles",
            source_language="en",
            target_language="de",
            updated_at="2026-07-03",
        )


def test_canonical_termbase_json_sorts_entries_by_id():
    shard = TranslationTermbase(
        language_key="de",
        source_language="en",
        target_language="de",
        entries=[_entry("LEX-Z"), _entry("LEX-A")],
    )
    payload = json.loads(canonical_termbase_json(shard))
    assert [entry["id"] for entry in payload["entries"]] == ["LEX-A", "LEX-Z"]


def test_write_and_load_termbase_roundtrip(tmp_path: Path):
    path = tmp_path / "de.json"
    shard = TranslationTermbase(
        language_key="de",
        source_language="en",
        target_language="de",
        entries=[_entry("LEX-A")],
    )
    write_termbase_shard(path, shard)
    loaded = load_termbase_shard(path)
    assert loaded == shard


def test_contextual_entry_requires_usage_rules():
    with pytest.raises(ValidationError, match="contextual_term entries require"):
        TermbaseEntry(
            id="TERM-CONTEXTUAL",
            kind="contextual_term",
            source="kinden",
            source_language="en",
            target_language="de",
        )


def test_contextual_usage_rules_preserve_order_and_generate_context_ids():
    entry = TermbaseEntry(
        id="TERM-KINDEN",
        kind="contextual_term",
        source="kinden",
        source_language="en",
        target_language="de",
        usage_rules=[
            TermbaseUsageRule(
                id="rule-specific",
                source_cue="Ant-kinden",
                required_target_literals=["Ameisenkinden"],
                prompt="Use the colony-specific form.",
            ),
            TermbaseUsageRule(
                id="rule-fallback",
                fallback=True,
                allowed_target_literals=["Kinden"],
                prompt="Use the generic form when no species-specific cue applies.",
            ),
        ],
    )

    assert [rule.id for rule in entry.usage_rules] == ["rule-specific", "rule-fallback"]
    assert entry.usage_rules[0].context_id == deterministic_context_id(
        "rule-specific", "Ant-kinden"
    )
    assert entry.usage_rules[1].context_id == deterministic_context_id(
        "rule-fallback", "fallback"
    )

    shard = TranslationTermbase(
        language_key="de",
        source_language="en",
        target_language="de",
        entries=[entry],
    )
    payload = json.loads(canonical_termbase_json(shard))
    assert payload["version"] == 2
    assert [rule["id"] for rule in payload["entries"][0]["usage_rules"]] == [
        "rule-specific",
        "rule-fallback",
    ]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("source_regex", r"(?i)kinden", "inline regex flags"),
        ("source_regex", r"(kinden)\1", "numeric backreferences"),
        ("forbidden_target_regexes", [r"(?P<lemma>kinden)"], "not allowed"),
    ],
)
def test_contextual_usage_rules_reject_unsafe_regex_features(
    field: str, value: object, message: str
):
    kwargs = {
        "id": "rule-unsafe",
        "source_cue": "kinden",
        "forbidden_target_literals": ["Kinden"],
    }
    kwargs[field] = value
    with pytest.raises(ValidationError, match=message):
        TermbaseUsageRule(**kwargs)


def test_merge_effective_termbase_uses_whole_entry_override_and_tombstone(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("BOOKTX_TERMBASE_DIR", str(tmp_path / "global-termbase"))
    project_dir = _make_project(tmp_path)
    project = load_project(project_dir, profile="de_locale")

    write_termbase_shard(
        global_termbase_path("de"),
        TranslationTermbase(
            language_key="de",
            source_language="en",
            target_language="de",
            entries=[_entry("LEX-ONE"), _entry("LEX-TWO")],
        ),
    )
    write_termbase_shard(
        project_termbase_path(project, "de"),
        TranslationTermbase(
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

    layers = resolved_termbase_layers(project, language_keys=["de"], scope="effective")
    effective = merge_effective_termbase(layers, language_keys=["de"])

    by_id = {entry.id: entry for entry in effective.entries}
    assert by_id["LEX-ONE"].status == "disabled"
    assert by_id["LEX-TWO"].target_preferred == ["verstaubte Prinzipien"]
    assert by_id["LEX-TWO"].target_forbidden == []


def test_termbase_language_keys_uses_profile_locale_sequence(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    project = load_project(project_dir, profile="de_locale")
    assert termbase_language_keys(project) == ["de", "de-DE"]


def test_termbase_paths_follow_expected_layout(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BOOKTX_TERMBASE_DIR", str(tmp_path / "global-termbase"))
    project_dir = _make_project(tmp_path)
    project = load_project(project_dir, profile="de_locale")

    assert global_termbase_dir() == (tmp_path / "global-termbase").resolve()
    assert global_termbase_path("de").name == "de.json"
    assert (
        project_termbase_path(project, "de")
        .as_posix()
        .endswith(".booktx/termbase/de.json")
    )
    assert (
        profile_termbase_path(project, "de-DE")
        .as_posix()
        .endswith("translations/de_locale/termbase-overrides/de-DE.json")
    )
