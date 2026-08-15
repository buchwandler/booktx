from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from booktx.models import ProfileConfig, TranslationTask
from booktx.validation_receipts import validation_receipt_key


def _task() -> TranslationTask:
    return TranslationTask(
        task_id="task-1",
        unit="batch",
        profile="de_default",
        source_language="en",
        target_language="de",
        target_locale="de-DE",
        chapter_id="0001",
        source_sha256="source",
    )


def test_receipt_key_changes_with_quality_policy(tmp_path: Path) -> None:
    input_path = tmp_path / "block.txt"
    input_path.write_text("submission\n", encoding="utf-8")
    project = SimpleNamespace(
        profile_config=ProfileConfig(
            profile="de_default", target_language="de", target_locale="de-DE"
        )
    )
    first = validation_receipt_key(_task(), input_path, project)
    project.profile_config = ProfileConfig(
        profile="de_default",
        target_language="de",
        target_locale="de-DE",
        submission_quality={"linguistic_audit": "error"},
    )
    second = validation_receipt_key(_task(), input_path, project)
    assert first != second
