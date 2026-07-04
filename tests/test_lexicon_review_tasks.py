from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from booktx.acceptance import SubmittedRecord, accept_translation_records
from booktx.cli import app
from booktx.config import (
    BooktxError,
    load_project,
    load_translation_review_task,
    load_translation_task,
    write_translation_store,
)
from booktx.context import load_context
from booktx.lexicon import TranslationLexicon, write_lexicon_shard
from booktx.models import (
    StoredTranslationRecordV2,
    TranslationCandidate,
    TranslationReviewCandidate,
    TranslationStoreV2,
)
from booktx.progress import source_record_sha256
from booktx.status import build_status_snapshot
from booktx.translation_store import sha256_text

runner = CliRunner()


def _make_project(tmp_path: Path) -> Path:
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
    ctx_init = runner.invoke(
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
    assert ctx_init.exit_code == 0, ctx_init.output
    ctx_ready = runner.invoke(
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
    assert ctx_ready.exit_code == 0, ctx_ready.output
    return project_dir


def _write_lexicon(
    monkeypatch,
    tmp_path: Path,
    *,
    rationale: str = "Avoid literal mould.",
    preferred: list[str] | None = None,
) -> Path:
    root = tmp_path / "global-lexicon"
    monkeypatch.setenv("BOOKTX_LEXICON_DIR", str(root))
    shard = root / "de.json"
    write_lexicon_shard(
        shard,
        TranslationLexicon.model_validate(
            {
                "language_key": "de",
                "source_language": "en",
                "target_language": "de",
                "entries": [
                    {
                        "id": "LEX-MOULDY",
                        "kind": "word_sense",
                        "source": "mouldy principles",
                        "source_variants": ["mouldy principles of magic"],
                        "source_regex": r"\bmouldy\s+principles(?:\s+of\s+magic)?\b",
                        "source_language": "en",
                        "target_language": "de",
                        "target_preferred": preferred
                        or ["schäbige Prinzipien", "schäbigen Prinzipien"],
                        "target_forbidden": [
                            "schimmlige Prinzipien",
                            "schimmligen Prinzipien",
                        ],
                        "sense": "stale doctrine",
                        "rationale": rationale,
                        "created_by_kind": "user",
                    }
                ],
            }
        ),
    )
    return shard


def _matching_record(project_dir: Path) -> tuple[str, str]:
    project = load_project(project_dir, profile="de_default")
    chunk = json.loads(next(project.chunks_dir.glob("*.json")).read_text("utf-8"))
    record = next(
        item
        for item in chunk["records"]
        if "mouldy principles" in item["source"].lower()
    )
    return record["id"], record["source"]


def _task_for_project(project_dir: Path) -> tuple[object, str]:
    next_res = runner.invoke(
        app,
        [
            "translate",
            "next",
            str(project_dir),
            "--profile",
            "de_default",
            "--unit",
            "batch",
            "--max-words",
            "100",
            "--json",
        ],
    )
    assert next_res.exit_code == 0, next_res.output
    payload = json.loads(next_res.output)
    proj = load_project(project_dir, profile="de_default")
    task = load_translation_task(proj, payload["task_id"])
    assert task is not None
    return task, payload["task_id"]


def _accepted_store(record_id: str, source: str, target: str) -> TranslationStoreV2:
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


def test_translation_task_persists_applicable_lexicon(monkeypatch, tmp_path: Path):
    _write_lexicon(monkeypatch, tmp_path)
    project_dir = _make_project(tmp_path)
    task, task_id = _task_for_project(project_dir)

    assert task.applicable_lexicon_sha256
    matching_record = next(
        record for record in task.records if record.applicable_lexicon
    )
    assert matching_record.applicable_lexicon[0].entry_id == "LEX-MOULDY"

    source_block = (
        load_project(project_dir, profile="de_default").tasks_dir
        / f"{task_id}.source.block.txt"
    ).read_text("utf-8")
    assert "# applicable lexicon:" in source_block
    assert "# lexicon: LEX-MOULDY" in source_block


def test_unrelated_lexicon_change_does_not_stale_task(monkeypatch, tmp_path: Path):
    shard = _write_lexicon(monkeypatch, tmp_path)
    project_dir = _make_project(tmp_path)
    task, _ = _task_for_project(project_dir)
    proj = load_project(project_dir, profile="de_default")
    rid, _ = _matching_record(project_dir)
    bundle = build_status_snapshot(proj, context_exists=True, context_ready=True)

    payload = json.loads(shard.read_text("utf-8"))
    payload["entries"].append(
        {
            "id": "LEX-UNRELATED",
            "source": "different phrase",
            "source_language": "en",
            "target_language": "de",
            "created_by_kind": "user",
        }
    )
    shard.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    result = accept_translation_records(
        proj,
        [
            SubmittedRecord(
                id=rid,
                target=(
                    "Wie jede Mottenart hatte sie die schäbigen Prinzipien "
                    "der Magie erlernt."
                ),
            )
        ],
        bundle=bundle,
        task=task,
        submission_translation_version=task.translation_version,
        enforce_task_version=True,
    )
    assert result.version_ref == task.translation_version


def test_applicable_lexicon_change_stales_translation_task(monkeypatch, tmp_path: Path):
    shard = _write_lexicon(monkeypatch, tmp_path)
    project_dir = _make_project(tmp_path)
    task, _ = _task_for_project(project_dir)
    proj = load_project(project_dir, profile="de_default")
    rid, _ = _matching_record(project_dir)
    bundle = build_status_snapshot(proj, context_exists=True, context_ready=True)

    payload = json.loads(shard.read_text("utf-8"))
    payload["entries"][0]["rationale"] = "Updated instruction text."
    shard.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    with pytest.raises(BooktxError) as excinfo:
        accept_translation_records(
            proj,
            [
                SubmittedRecord(
                    id=rid,
                    target=(
                        "Wie jede Mottenart hatte sie die schäbigen Prinzipien "
                        "der Magie erlernt."
                    ),
                )
            ],
            bundle=bundle,
            task=task,
            submission_translation_version=task.translation_version,
            enforce_task_version=True,
        )
    assert excinfo.value.code == "task_context_policy_stale"


def test_write_review_persists_applicable_lexicon_and_findings(
    monkeypatch, tmp_path: Path
):
    _write_lexicon(monkeypatch, tmp_path)
    project_dir = _make_project(tmp_path)
    rid, source = _matching_record(project_dir)
    proj = load_project(project_dir, profile="de_default")
    write_translation_store(
        proj,
        _accepted_store(
            rid,
            source,
            "Wie jede Mottenart hatte sie die schimmligen Prinzipien "
            "der Magie erlernt.",
        ),
    )
    review_cfg = runner.invoke(
        app,
        [
            "review",
            "configure",
            str(project_dir),
            "--profile",
            "de_default",
            "--enable",
            "--pass",
            "1",
            "--name",
            "Lexicon review",
            "--mode",
            "manual",
            "--enforce",
            "warn",
        ],
    )
    assert review_cfg.exit_code == 0, review_cfg.output

    res = runner.invoke(
        app,
        [
            "lexicon",
            "write-review",
            str(project_dir),
            "--profile",
            "de_default",
            "--pass",
            "1",
        ],
    )
    assert res.exit_code == 0, res.output
    task_id = next(
        line.split(": ", 1)[1]
        for line in res.output.splitlines()
        if line.startswith("review task: ")
    )
    review_task = load_translation_review_task(proj, task_id)
    assert review_task is not None
    assert review_task.applicable_lexicon_sha256
    assert review_task.records[0].applicable_lexicon[0].entry_id == "LEX-MOULDY"
    assert review_task.records[0].lexicon_findings[0].status == "forbidden_target"

    source_block = (
        proj.profile_dir / "reviews" / f"{task_id}.source.block.txt"
    ).read_text("utf-8")
    assert "LEXICON: LEX-MOULDY" in source_block
    assert "LEXICON-FINDING: forbidden_target" in source_block


def test_promote_context_defaults_to_question_for_ambiguous_entry(
    monkeypatch, tmp_path: Path
):
    _write_lexicon(monkeypatch, tmp_path)
    project_dir = _make_project(tmp_path)

    res = runner.invoke(
        app,
        [
            "lexicon",
            "promote-context",
            str(project_dir),
            "--profile",
            "de_default",
            "--entry",
            "LEX-MOULDY",
        ],
    )

    assert res.exit_code == 0, res.output
    ctx = load_context(load_project(project_dir, profile="de_default"))
    assert ctx is not None
    assert any(question.topic == "lexicon" for question in ctx.questions)
    assert ctx.ready is False


def test_promote_context_defaults_to_advisory_for_single_preferred(
    monkeypatch, tmp_path: Path
):
    _write_lexicon(monkeypatch, tmp_path, preferred=["schäbige Prinzipien"])
    project_dir = _make_project(tmp_path)

    res = runner.invoke(
        app,
        [
            "lexicon",
            "promote-context",
            str(project_dir),
            "--profile",
            "de_default",
            "--entry",
            "LEX-MOULDY",
        ],
    )

    assert res.exit_code == 0, res.output
    ctx = load_context(load_project(project_dir, profile="de_default"))
    assert ctx is not None
    glossary = next(
        entry for entry in ctx.glossary if entry.source == "mouldy principles"
    )
    assert glossary.target == "schäbige Prinzipien"
    assert glossary.require_target is False


def test_write_review_reruns_same_pass_from_active_review(monkeypatch, tmp_path: Path):
    _write_lexicon(monkeypatch, tmp_path)
    project_dir = _make_project(tmp_path)
    rid, source = _matching_record(project_dir)
    proj = load_project(project_dir, profile="de_default")
    active_review = TranslationReviewCandidate(
        pass_number=1,
        run_number=1,
        review_ref="R1.1",
        base_kind="translation",
        base_ref="1.1",
        base_target_sha256=sha256_text(source),
        target=(
            "Wie jede Mottenart hatte sie die schimmligen Prinzipien der Magie erlernt."
        ),
        target_sha256=sha256_text(
            "Wie jede Mottenart hatte sie die schimmligen Prinzipien der Magie erlernt."
        ),
        created_at="2026-07-03T08:00:00Z",
        updated_at="2026-07-03T08:00:00Z",
    )
    write_translation_store(
        proj,
        TranslationStoreV2(
            records={
                rid: StoredTranslationRecordV2(
                    chunk_id=int(rid.split("-", 1)[0]),
                    part_id=int(rid.split("-", 1)[1]),
                    source_sha256=source_record_sha256(source),
                    source=source,
                    active_version="1.1",
                    active_review="R1.1",
                    versions=[
                        TranslationCandidate(
                            version=1,
                            subversion=1,
                            version_ref="1.1",
                            target=source,
                            created_at="2026-07-03T08:00:00Z",
                            updated_at="2026-07-03T08:00:00Z",
                        )
                    ],
                    reviews=[active_review],
                )
            }
        ),
    )
    review_cfg = runner.invoke(
        app,
        [
            "review",
            "configure",
            str(project_dir),
            "--profile",
            "de_default",
            "--enable",
            "--pass",
            "1",
            "--name",
            "Lexicon review",
            "--mode",
            "manual",
            "--enforce",
            "warn",
        ],
    )
    assert review_cfg.exit_code == 0, review_cfg.output

    res = runner.invoke(
        app,
        [
            "lexicon",
            "write-review",
            str(project_dir),
            "--profile",
            "de_default",
            "--pass",
            "1",
        ],
    )

    assert res.exit_code == 0, res.output
    task_id = next(
        line.split(": ", 1)[1]
        for line in res.output.splitlines()
        if line.startswith("review task: ")
    )
    review_task = load_translation_review_task(proj, task_id)
    assert review_task is not None
    assert review_task.records[0].base_kind == "review"
    assert review_task.records[0].base_ref == "R1.1"
    assert review_task.records[0].review_ref == "R1.2"


def test_trigger_case_becomes_clean_after_review_insert(monkeypatch, tmp_path: Path):
    _write_lexicon(monkeypatch, tmp_path, preferred=["schäbigen Prinzipien"])
    project_dir = _make_project(tmp_path)
    rid, source = _matching_record(project_dir)
    proj = load_project(project_dir, profile="de_default")
    write_translation_store(
        proj,
        _accepted_store(
            rid,
            source,
            "Wie jede Mottenart hatte sie die schimmligen Prinzipien "
            "der Magie erlernt.",
        ),
    )
    review_cfg = runner.invoke(
        app,
        [
            "review",
            "configure",
            str(project_dir),
            "--profile",
            "de_default",
            "--enable",
            "--pass",
            "1",
            "--name",
            "Lexicon review",
            "--mode",
            "manual",
            "--enforce",
            "warn",
        ],
    )
    assert review_cfg.exit_code == 0, review_cfg.output

    create_res = runner.invoke(
        app,
        [
            "lexicon",
            "write-review",
            str(project_dir),
            "--profile",
            "de_default",
            "--pass",
            "1",
        ],
    )
    assert create_res.exit_code == 0, create_res.output
    task_id = next(
        line.split(": ", 1)[1]
        for line in create_res.output.splitlines()
        if line.startswith("review task: ")
    )
    ingest_path = proj.profile_dir / "reviews" / f"{task_id}.block.txt"
    ingest = ingest_path.read_text("utf-8").replace(
        "schimmligen Prinzipien", "schäbigen Prinzipien"
    )
    ingest_path.write_text(ingest, encoding="utf-8")

    insert_res = runner.invoke(
        app,
        [
            "review",
            "insert",
            str(project_dir),
            "--profile",
            "de_default",
            "--review-task-id",
            task_id,
            "--file",
            str(ingest_path),
            "--format",
            "block",
        ],
    )
    assert insert_res.exit_code == 0, insert_res.output

    audit = runner.invoke(
        app,
        ["lexicon", "audit", str(project_dir), "--profile", "de_default", "--jsonl"],
    )
    assert audit.exit_code == 0, audit.output
    assert audit.output.strip() == ""
