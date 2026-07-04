from __future__ import annotations

from pathlib import Path

from booktx.config import init_project, load_project, write_translation_store
from booktx.context import (
    GlossaryEntry,
    default_context,
    load_context,
    write_context,
    write_context_markdown,
)
from booktx.glossary_audit import audit_glossary_term
from booktx.models import (
    Chunk,
    Record,
    StoredTranslationRecordV2,
    TranslatedRecord,
    TranslationCandidate,
    TranslationStoreV2,
)
from booktx.progress import source_record_sha256
from booktx.qa_scan import qa_scan
from booktx.status import build_status_snapshot
from booktx.termbase import (
    EffectiveTranslationTermbase,
    TermbaseEntry,
    TermbaseUsageRule,
)
from booktx.termbase_audit import audit_termbase
from booktx.validate import validate_record_pair


def _store_record(record_id: str, source: str, target: str) -> TranslationStoreV2:
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
                        status="accepted",
                        created_at="2026-07-04T08:00:00Z",
                        updated_at="2026-07-04T08:00:00Z",
                    )
                ],
            )
        }
    )


def _write_project(
    tmp_path: Path,
    *,
    source_text: str,
    glossary_entries: list[GlossaryEntry],
) -> tuple[Path, str]:
    source_file = tmp_path / "source.md"
    source_file.write_text(f"# One\n\n{source_text}\n", encoding="utf-8")
    proj = init_project(
        tmp_path / "book",
        target_language="de",
        source_file=source_file,
    )
    record_id = "0001-000001"
    chunk = Chunk(
        chunk_id="0001",
        source_language="en",
        target_language="de",
        records=[Record(id=record_id, source=source_text)],
    )
    proj.chunks_dir.mkdir(parents=True, exist_ok=True)
    (proj.chunks_dir / "0001.json").write_text(
        chunk.model_dump_json(), encoding="utf-8"
    )
    ctx = default_context(proj)
    ctx.ready = True
    ctx.glossary.extend(glossary_entries)
    write_context(proj, ctx)
    write_context_markdown(proj, ctx)
    return proj.root, record_id


def test_validation_qa_and_audit_align_on_glossary_violation(tmp_path: Path) -> None:
    project_dir, record_id = _write_project(
        tmp_path,
        source_text="The Lowlands answer.",
        glossary_entries=[
            GlossaryEntry(
                source="Lowlands",
                target="Niederlande",
                require_target=True,
                forbidden_targets=["Tieflande"],
                enforce="error",
                status="approved",
            )
        ],
    )
    proj = load_project(project_dir, profile="de_default")
    write_translation_store(
        proj,
        _store_record(record_id, "The Lowlands answer.", "Die Tieflande antworten."),
    )
    bundle = build_status_snapshot(proj, context_exists=True, context_ready=True)

    ctx = load_context(proj)
    assert ctx is not None
    report = validate_record_pair(
        Record(id=record_id, source="The Lowlands answer."),
        TranslatedRecord(id=record_id, target="Die Tieflande antworten."),
        "0001",
        ctx,
    )
    qa = qa_scan(proj, bundle, forbidden=True, glossary=True)
    audit = audit_glossary_term(proj, bundle, source_term="Lowlands")
    assert audit is not None

    assert any(f.rule == "forbidden_term_used" for f in report)
    assert any(f.rule == "forbidden_target" for f in qa.findings)
    assert audit.forbidden_violation_records == 1
    assert audit.records[0].forbidden_found == ["Tieflande"]


def test_termbase_audit_supports_contextual_usage_rules(tmp_path: Path) -> None:
    project_dir, record_id = _write_project(
        tmp_path,
        source_text="The Ant-kinden waited.",
        glossary_entries=[],
    )
    proj = load_project(project_dir, profile="de_default")
    write_translation_store(
        proj,
        _store_record(record_id, "The Ant-kinden waited.", "Die Kinden warteten."),
    )
    bundle = build_status_snapshot(proj, context_exists=True, context_ready=True)
    effective = EffectiveTranslationTermbase(
        language_keys=["de"],
        source_language="en",
        target_language="de",
        entries=[
            TermbaseEntry(
                id="TERM-ANT-KINDEN",
                kind="contextual_term",
                source="kinden",
                source_regex=r"\bAnt-kinden\b",
                source_language="en",
                target_language="de",
                usage_rules=[
                    TermbaseUsageRule(
                        id="rule-ant",
                        source_cue="Ant-kinden",
                        required_target_literals=["Ameisenkinden"],
                        forbidden_target_literals=["Kinden"],
                        severity="error",
                        prompt="Use the species-specific form.",
                    )
                ],
            )
        ],
    )

    result = audit_termbase(proj, bundle, effective)

    assert result.finding_count == 1
    assert result.matches[0].entry_id == "TERM-ANT-KINDEN"
    assert result.matches[0].rule_id == "rule-ant"
    assert result.matches[0].status == "forbidden_target"
    assert result.matches[0].target_forbidden_found == ["Kinden"]
