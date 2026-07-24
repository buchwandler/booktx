from __future__ import annotations

from types import SimpleNamespace

from booktx.models import StoredTranslationRecordV2, TranslationCandidate, TranslationStoreV2
from booktx.translation_concordance import build_concordance, render_concordance_human


def _record(source: str, target: str, chunk: str, part: str) -> StoredTranslationRecordV2:
    record_id = f"{chunk}-{part}"
    return StoredTranslationRecordV2(
        chunk_id=chunk,
        part_id=part,
        source_sha256="source",
        source=source,
        active_version="1.1",
        versions=[
            TranslationCandidate(
                version=1,
                subversion=1,
                version_ref="1.1",
                target=target,
                status="accepted",
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
            )
        ],
    )


def _bundle():
    ids = ["0001-000001", "0001-000002", "0001-000003"]
    source = {
        ids[0]: SimpleNamespace(source="The Dragonfly guard arrived.", chunk_id="0001"),
        ids[1]: SimpleNamespace(source="A quiet sentence.", chunk_id="0001"),
        ids[2]: SimpleNamespace(source="The Dragonfly captain waited.", chunk_id="0001"),
    }
    index = SimpleNamespace(
        record_ids_by_chapter={"0001": ids},
        source_by_id=source,
        record_to_chapter={record_id: "0001" for record_id in ids},
        chapters_by_id={},
    )
    return SimpleNamespace(index=index)


def test_concordance_groups_queries_and_excludes_later_task_records(monkeypatch):
    store = TranslationStoreV2(
        source_sha256="source",
        records={
            "0001-000001": _record("The Dragonfly guard arrived.", "Die Libellenwache kam.", "0001", "000001"),
            "0001-000002": _record("A quiet sentence.", "Ein ruhiger Satz.", "0001", "000002"),
            "0001-000003": _record("The Dragonfly captain waited.", "Der Libellenhauptmann wartete.", "0001", "000003"),
        },
    )
    monkeypatch.setattr("booktx.translation_concordance.load_translation_store", lambda project: store)
    task = SimpleNamespace(task_id="TASK", records=[SimpleNamespace(id="0001-000003", source="The Dragonfly captain waited.")])
    report = build_concordance(
        SimpleNamespace(profile="de", root="."),
        _bundle(),
        task=task,
        source_queries=["Dragonfly"],
        target_queries=["Libellenwache"],
        scope="before-task",
        max_examples=3,
    )
    assert report.records_scanned == 2
    assert [group.total_matches for group in report.queries] == [1, 1]
    assert report.queries[0].examples[0].record_id == "0001-000001"
    assert "Binding policy wins" in render_concordance_human(report)


def test_concordance_auto_suppresses_unseen_cues(monkeypatch):
    store = TranslationStoreV2(
        source_sha256="source",
        records={"0001-000001": _record("The Dragonfly guard arrived.", "Die Libellenwache kam.", "0001", "000001")},
    )
    monkeypatch.setattr("booktx.translation_concordance.load_translation_store", lambda project: store)
    task = SimpleNamespace(task_id="TASK", records=[SimpleNamespace(id="0001-000002", source="The Dragonfly guard arrived.")])
    report = build_concordance(SimpleNamespace(profile="de", root="."), _bundle(), task=task, auto=True, scope="before-task")
    assert all(group.total_matches > 0 for group in report.queries)
