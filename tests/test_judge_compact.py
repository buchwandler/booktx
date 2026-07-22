from __future__ import annotations

from booktx.judge_acceptance import parse_judge_decisions_submission
from booktx.judge_tasks import render_judge_grammar_decisions, render_judge_grammar_task
from booktx.models import JudgeTask, JudgeTaskCandidate, JudgeTaskRecord


def _task() -> JudgeTask:
    records = [
        JudgeTaskRecord(
            id=f"0001-{index:06d}",
            chunk_id="chunk-1",
            source="Source sentence.",
            source_sha256="sha",
            output_version_ref="v1",
            candidates=[
                JudgeTaskCandidate(
                    label="A",
                    profile="de",
                    target_language="de",
                    selected_kind="translation",
                    selected_ref="v1",
                    target="Ein deutscher Zielsatz.",
                    target_sha256="target-sha",
                )
            ],
        )
        for index in range(1, 4)
    ]
    return JudgeTask(
        judge_task_id="task-1",
        profile="judge",
        source_language="en",
        target_language="de",
        chapter_id="0001",
        created_at="now",
        source_sha256="source-sha",
        selection_purpose="revise",
        revision_focus="grammar",
        records=records,
    )


def test_compact_grammar_render_is_target_first_and_small() -> None:
    task = _task()
    source = render_judge_grammar_task(task)
    decisions = render_judge_grammar_decisions(task)
    assert source.index("BASE_TARGET:") < source.index("SOURCE_GUARD:")
    assert "format: grammar-decisions-v2" in decisions
    assert len(source.splitlines()) < 60
    assert len(decisions.splitlines()) < 30


def test_compact_decisions_round_trip_inline_and_multiline_targets() -> None:
    text = (
        "format: grammar-decisions-v2\n"
        "judge_task_id: task-1\n\n"
        "0001-000001 | edited | A | grammar: agreement\n"
        "TARGET:\nKorrigierter Satz.\nEND_TARGET\n\n"
        "0001-000002 | copy | A\nTARGET: Inline Satz.\nEND_TARGET\n"
    )
    task_id, records = parse_judge_decisions_submission(text)
    assert task_id == "task-1"
    assert records[0].target == "Korrigierter Satz."
    assert records[1].target == "Inline Satz."
