from __future__ import annotations

from types import SimpleNamespace

from booktx.tasks import limit_records_by_budget


def test_translation_budget_honors_records_sentences_and_rendered_lines() -> None:
    source = {
        "r1": SimpleNamespace(source="one", source_words=1),
        "r2": SimpleNamespace(source="two\nthree", source_words=1),
        "r3": SimpleNamespace(source="four", source_words=1),
    }
    assert limit_records_by_budget(
        list(source), source, max_words=99, max_records=2
    ) == ["r1", "r2"]
    assert limit_records_by_budget(
        list(source), source, max_words=99, max_sentences=2
    ) == ["r1", "r2"]
    assert limit_records_by_budget(
        list(source), source, max_words=99, max_rendered_lines=2
    ) == ["r1"]


def test_translation_budget_keeps_one_oversized_record_for_progress() -> None:
    source = {"r1": SimpleNamespace(source="long", source_words=100)}
    assert limit_records_by_budget(
        ["r1"], source, max_words=1, max_records=1
    ) == ["r1"]
