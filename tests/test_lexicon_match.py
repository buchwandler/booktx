from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from booktx.cli import app
from booktx.config import load_project, write_translation_store
from booktx.lexicon import EffectiveTranslationLexicon, LexiconEntry
from booktx.lexicon_audit import audit_lexicon
from booktx.lexicon_match import (
    entry_preferred_absence,
    entry_target_forbidden_hits,
    lexicon_source_matches,
)
from booktx.models import (
    StoredTranslationRecordV2,
    TranslationCandidate,
    TranslationStoreV2,
)
from booktx.progress import source_record_sha256
from booktx.status import build_status_snapshot

runner = CliRunner()


def _entry(
    entry_id: str,
    *,
    source: str = "mouldy principles",
    source_variants: list[str] | None = None,
    source_regex: str | None = r"\bmouldy\s+principles(?:\s+of\s+magic)?\b",
    case_sensitive: bool = False,
    preferred_policy: str = "off",
) -> LexiconEntry:
    return LexiconEntry(
        id=entry_id,
        kind="word_sense",
        source=source,
        source_variants=(
            ["mouldy principles of magic"]
            if source_variants is None
            else source_variants
        ),
        source_regex=source_regex,
        source_language="en",
        case_sensitive=case_sensitive,
        target_preferred=["schäbige Prinzipien", "schäbigen Prinzipien"],
        target_allowed=["verstaubten Prinzipien"],
        target_forbidden=["schimmlige Prinzipien", "schimmligen Prinzipien"],
        target_regex_forbidden=[r"\bschimmlig(?:e|en|er|es)?\s+Prinzipien\b"],
        preferred_policy=preferred_policy,  # type: ignore[arg-type]
        target_language="de",
        severity="warn",
        created_by_kind="user",
    )


def _make_project(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "book.md"
    source.write_text(
        "# Chapter One\n\n"
        "Like any Moth-kinden of standing she had learned the mouldy "
        "principles of magic.\n",
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
    return project_dir, "0001-000001"


def _effective(record_id: str, source: str, target: str) -> TranslationStoreV2:
    chunk_id, part_id = (int(part) for part in record_id.split("-"))
    return TranslationStoreV2(
        records={
            record_id: StoredTranslationRecordV2(
                chunk_id=chunk_id,
                part_id=part_id,
                source_sha256=source_record_sha256(source),
                source=source,
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
    )


def test_literal_boundary_match():
    spans = lexicon_source_matches(
        "the mouldy principles of magic",
        [_entry("LEX-ONE", source_regex=None, source_variants=[])],
    )
    assert [(span.entry_id, span.source_match) for span in spans] == [
        ("LEX-ONE", "mouldy principles")
    ]


def test_no_substring_false_positive():
    spans = lexicon_source_matches(
        "the mouldyprinciples of magic",
        [_entry("LEX-ONE", source_regex=None, source_variants=[])],
    )
    assert spans == []


def test_regex_match_optional_of_magic():
    spans = lexicon_source_matches(
        "she had learned the mouldy principles of magic",
        [
            _entry(
                "LEX-ONE",
                source="mouldy precepts",
                source_variants=[],
                source_regex=r"\bmouldy\s+principles(?:\s+of\s+magic)?\b",
            )
        ],
    )
    assert [span.source_match for span in spans] == ["mouldy principles of magic"]


def test_literal_and_regex_same_span_are_deduped():
    spans = lexicon_source_matches(
        "she had learned the mouldy principles of magic",
        [
            _entry(
                "LEX-ONE",
                source="mouldy principles of magic",
                source_variants=[],
                source_regex=r"\bmouldy\s+principles(?:\s+of\s+magic)?\b",
            )
        ],
    )
    assert len([span for span in spans if span.entry_id == "LEX-ONE"]) == 1


def test_longest_match_shadows_shorter_contained_entry():
    spans = lexicon_source_matches(
        "she had learned the mouldy principles of magic",
        [
            _entry(
                "LEX-SHORT",
                source="mouldy principles",
                source_variants=[],
                source_regex=None,
            ),
            _entry(
                "LEX-LONG",
                source="mouldy principles of magic",
                source_variants=[],
                source_regex=None,
            ),
        ],
    )
    short = next(span for span in spans if span.entry_id == "LEX-SHORT")
    long = next(span for span in spans if span.entry_id == "LEX-LONG")
    assert long.shadowed is False
    assert short.shadowed is True


def test_partially_overlapping_matches_remain_separate():
    spans = lexicon_source_matches(
        "the stale mouldy principles",
        [
            _entry(
                "LEX-A", source="stale mouldy", source_variants=[], source_regex=None
            ),
            _entry(
                "LEX-B",
                source="mouldy principles",
                source_variants=[],
                source_regex=None,
            ),
        ],
    )
    assert [span.entry_id for span in spans] == ["LEX-A", "LEX-B"]
    assert all(span.shadowed is False for span in spans)


def test_case_sensitive_matching_is_respected():
    entry = _entry(
        "LEX-ONE",
        source="Mouldy Principles",
        source_variants=[],
        source_regex=None,
        case_sensitive=True,
    )
    assert lexicon_source_matches("Mouldy Principles", [entry])
    assert lexicon_source_matches("mouldy principles", [entry]) == []


def test_forbidden_literal_target_detects_phrase():
    hits = entry_target_forbidden_hits(
        "Wie jede Mottenart hatte sie die schimmligen Prinzipien der Magie erlernt.",
        _entry("LEX-ONE"),
    )
    assert "schimmligen Prinzipien" in hits


def test_forbidden_regex_detects_inflected_variant():
    hits = entry_target_forbidden_hits(
        "Sie folgte den schimmliger Prinzipien nicht.",
        _entry("LEX-ONE"),
    )
    assert "schimmliger Prinzipien" in hits


def test_preferred_absence_follows_policy():
    entry = _entry("LEX-ONE", preferred_policy="required")
    preferred_hits, missing = entry_preferred_absence(
        "Sie hatte die fremden Regeln gelernt.",
        entry,
    )
    assert preferred_hits == []
    assert missing is True

    advisory_entry = _entry("LEX-TWO", preferred_policy="advisory")
    _, advisory_missing = entry_preferred_absence(
        "Sie hatte die verstaubten Prinzipien gelernt.",
        advisory_entry,
    )
    assert advisory_missing is False

    off_entry = _entry("LEX-THREE", preferred_policy="off")
    _, off_missing = entry_preferred_absence("Ganz anderer Zieltext.", off_entry)
    assert off_missing is False


def test_audit_flags_forbidden_effective_target_only(tmp_path: Path):
    project_dir, record_id = _make_project(tmp_path)
    project = load_project(project_dir)
    bundle = build_status_snapshot(project, context_exists=False, context_ready=False)
    matching_view = next(
        view
        for view in bundle.index.source_by_id.values()
        if "mouldy principles" in view.source
    )
    target = (
        "Wie jede Mottenart hatte sie die schimmligen Prinzipien der Magie erlernt."
    )
    write_translation_store(
        project, _effective(matching_view.record_id, matching_view.source, target)
    )
    bundle = build_status_snapshot(project, context_exists=False, context_ready=False)
    effective = EffectiveTranslationLexicon(
        language_keys=["de"],
        source_language="en",
        target_language="de",
        entries=[_entry("LEX-ONE")],
    )

    result = audit_lexicon(project, bundle, effective)

    assert result.source_matched_records == 1
    assert result.audited_records == 1
    assert result.finding_count == 1
    finding = result.matches[0]
    assert finding.status == "forbidden_target"
    assert "schimmligen Prinzipien" in finding.target_forbidden_found


def test_audit_does_not_flag_unrelated_mouldy_with_phrase_specific_rule(tmp_path: Path):
    source = tmp_path / "book.md"
    source.write_text(
        "# Chapter One\n\nThe mouldy tapestry hung in silence.\n", encoding="utf-8"
    )
    project_dir = tmp_path / "book"
    init = runner.invoke(
        app,
        ["init", str(project_dir), "--target", "de", "--source-file", str(source)],
    )
    assert init.exit_code == 0, init.output
    extract = runner.invoke(app, ["extract", str(project_dir)])
    assert extract.exit_code == 0, extract.output
    project = load_project(project_dir)
    target = "Der schimmlige Wandteppich hing still da."
    bundle = build_status_snapshot(project, context_exists=False, context_ready=False)
    first_view = next(iter(bundle.index.source_by_id.values()))
    write_translation_store(
        project, _effective(first_view.record_id, first_view.source, target)
    )
    bundle = build_status_snapshot(project, context_exists=False, context_ready=False)
    effective = EffectiveTranslationLexicon(
        language_keys=["de"],
        source_language="en",
        target_language="de",
        entries=[_entry("LEX-ONE")],
    )

    result = audit_lexicon(project, bundle, effective)

    assert result.source_matched_records == 0
    assert result.matches == []
