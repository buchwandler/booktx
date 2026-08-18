from __future__ import annotations

from pathlib import Path

import pytest

from booktx.config import (
    create_profile,
    init_source_project,
    load_project,
    write_translation_version_ledger,
)
from booktx.epub_inline_xhtml import (
    inline_skeleton,
    protect_names_in_xhtml_text_nodes,
    sanitize_target_fragment,
    strip_inline_xhtml,
)
from booktx.inline_audit import migrate_inline_xhtml, safe_migrated_target
from booktx.io_utils import write_json_model_atomic
from booktx.models import (
    Chunk,
    Record,
    StoredTranslationRecordV2,
    TranslatedChunk,
    TranslatedRecord,
    TranslationCandidate,
    TranslationStoreV2,
    TranslationSubversionLedgerEntry,
    TranslationTrackLedgerEntry,
    TranslationVersionLedger,
)
from booktx.progress import source_record_sha256
from booktx.store import StoreFormat, open_translation_store
from booktx.validate import load_effective_translated_chunks


def test_protect_names_in_xhtml_text_nodes_does_not_tokenize_href():
    text, placeholders = protect_names_in_xhtml_text_nodes(
        '<a href="https://example.invalid/Alice">Alice</a>', ["Alice"]
    )

    assert 'href="https://example.invalid/Alice"' in text
    assert ">__NAME_001__<" in text
    assert placeholders[0].original == "Alice"


def test_sanitize_target_fragment_rejects_script():
    sanitized = sanitize_target_fragment("<script>alert(1)</script>", "<em>Title</em>")

    assert any(issue.rule == "inline_xhtml_no_block_tags" for issue in sanitized.issues)


def test_strip_inline_xhtml_returns_visible_text():
    assert (
        strip_inline_xhtml("Use <code>pip install booktx</code> first.")
        == "Use pip install booktx first."
    )


def test_inline_skeleton_preserves_attrs():
    skeleton = inline_skeleton('<span class="smallcaps">Alice</span>')

    assert skeleton[0].tag == "span"
    assert skeleton[0].attrs == (("class", "smallcaps"),)


def test_migrate_inline_xhtml_wraps_full_record_emphasis_safe_case():
    assert (
        safe_migrated_target(
            "<em>Running down again – always at the worst possible moment!</em>",
            "Schon wieder am Ablaufen – immer im denkbar schlechtesten Moment!",
        )
        == "<em>Schon wieder am Ablaufen – immer im denkbar schlechtesten Moment!</em>"
    )


def test_migrate_inline_xhtml_wraps_exact_title_safe_case():
    assert (
        safe_migrated_target(
            "the <em>Esca Volenti</em> shuddered", "die Esca Volenti erbebte"
        )
        == "die <em>Esca Volenti</em> erbebte"
    )


def test_migrate_inline_xhtml_refuses_ambiguous_translated_phrase():
    assert safe_migrated_target("the <em>red ship</em>", "das rote Schiff") is None


def _inline_migration_project(tmp_path: Path, store_format: StoreFormat):
    source_project = init_source_project(tmp_path / f"book-{store_format.value}")
    (source_project.source_dir / "story.md").write_text(
        "# Demo\n\nEsca Volenti.\n", encoding="utf-8"
    )
    profile = create_profile(
        source_project.root,
        "de_default",
        target_language="de",
        store_format=store_format.value,
    )
    source_record = Record(
        id="0001-000001",
        source="the <em>Esca Volenti</em> shuddered",
        source_markup="epub-inline-xhtml:v1",
    )
    write_json_model_atomic(
        profile.chunks_dir / "0001.json",
        Chunk(
            chunk_id="0001",
            source_language="en",
            target_language="de",
            records=[source_record],
        ),
    )
    target = "die Esca Volenti erbebte"
    open_translation_store(profile).write_materialized_v2(
        TranslationStoreV2(
            records={
                source_record.id: StoredTranslationRecordV2(
                    chunk_id=1,
                    part_id=1,
                    source_sha256=source_record_sha256(source_record.source),
                    source=source_record.source,
                    active_version="1.1",
                    versions=[
                        TranslationCandidate(
                            version=1,
                            subversion=1,
                            version_ref="1.1",
                            target=target,
                            created_at="2026-06-22T12:00:00Z",
                            updated_at="2026-06-22T12:00:00Z",
                        )
                    ],
                )
            }
        )
    )
    write_translation_version_ledger(
        profile,
        TranslationVersionLedger(
            active_version="1.1",
            tracks={
                "1": TranslationTrackLedgerEntry(
                    version=1,
                    actor="user:test",
                    harness="pytest",
                    model="human",
                    created_at="2026-06-22T12:00:00Z",
                    updated_at="2026-06-22T12:00:00Z",
                    subversions={
                        "1": TranslationSubversionLedgerEntry(
                            version=1,
                            subversion=1,
                            version_ref="1.1",
                            context_sha256="a" * 64,
                            created_at="2026-06-22T12:00:00Z",
                            updated_at="2026-06-22T12:00:00Z",
                        )
                    },
                )
            },
        ),
    )
    write_json_model_atomic(
        profile.translated_dir / "0001.json",
        TranslatedChunk(
            chunk_id="0001",
            records=[TranslatedRecord(id=source_record.id, target=target)],
        ),
    )
    return load_project(profile.root, profile="de_default"), source_record.id


@pytest.mark.parametrize("store_format", [StoreFormat.V2, StoreFormat.V3])
def test_migrate_inline_xhtml_write_safe_updates_canonical_store(
    tmp_path: Path, store_format: StoreFormat
):
    proj, record_id = _inline_migration_project(tmp_path, store_format)

    report = migrate_inline_xhtml(proj, write_safe=True)

    assert report["written"] is True
    assert [item["record_id"] for item in report["mapped_records"]] == [record_id]

    reloaded = load_project(proj.root, profile="de_default")
    stored = open_translation_store(reloaded).get_record(record_id)
    assert stored is not None
    assert stored.versions[0].target == "die <em>Esca Volenti</em> erbebte"

    effective = load_effective_translated_chunks(reloaded)
    assert (
        effective.chunks["0001"].records[0].target
        == "die <em>Esca Volenti</em> erbebte"
    )
    assert (
        (reloaded.translated_dir / "0001.json").read_text("utf-8")
        .find("die <em>Esca Volenti</em> erbebte")
        >= 0
    )
