from __future__ import annotations

from types import SimpleNamespace

from booktx.judge_policy import (
    JudgeBatchPolicy,
    count_candidates_sentences,
    count_sentences,
)


def test_count_sentences_handles_quotes_abbreviations_and_xhtml() -> None:
    assert count_sentences("„Hallo!“, sagte sie. Dann ging er.") == 2
    assert count_sentences("z. B. ein Satz.") == 1
    assert count_sentences("<em>Ein Satz.</em>") == 1
    assert count_sentences("…") == 1


def test_count_candidates_sentences_uses_largest_candidate() -> None:
    candidates = [
        SimpleNamespace(target="Ein Satz."),
        SimpleNamespace(target="Ein Satz. Noch einer."),
    ]
    assert count_candidates_sentences(candidates) == 2


def test_policy_reads_v2_todo_batch_fields() -> None:
    todo = SimpleNamespace(
        batch_records=40,
        batch_sentences=60,
        batch_words=1800,
        batch_rendered_lines=700,
        max_words=900,
    )
    assert JudgeBatchPolicy.from_todo(todo) == JudgeBatchPolicy(40, 60, 1800, 700)


def test_v1_todo_loads_with_v2_defaults(tmp_path) -> None:
    import json

    from booktx.judge_todos import load_todo

    profile = tmp_path / "profile"
    (profile / "judge-todos").mkdir(parents=True)
    (profile / "judge-todos" / "judge-todo-old.json").write_text(
        json.dumps(
            {
                "schema": "booktx.judge-todo.v1",
                "todo_id": "judge-todo-old",
                "profile": "judge",
                "purpose": "revise",
                "revision_focus": "grammar",
                "chapter_ids": ["0001", "0002"],
                "max_records": 20,
                "max_sentences": 5,
                "max_words": 900,
                "created_at": "now",
            }
        ),
        encoding="utf-8",
    )
    todo = load_todo(SimpleNamespace(profile_dir=profile), "judge-todo-old")
    assert todo is not None
    assert todo.schema_version == 1
    assert todo.batch_records == 20
    assert todo.batch_sentences == 5
    assert todo.from_chapter == "0001"
