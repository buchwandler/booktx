from __future__ import annotations

from pathlib import Path

from booktx.store import StoreFormat, open_translation_store
from tests.store_backend_fixtures import (
    EXPECTED_BUILD_TEXT,
    build_output_text,
    create_rich_store_fixture,
    normalized_editor_indexes,
    normalized_semantic_projection,
)


def _effective_by_slug(projection: dict, record_ids: dict[str, str]) -> dict[str, dict]:
    by_record_id = {record["record_id"]: record for record in projection["records"]}
    return {
        slug: by_record_id[record_id]["effective"]
        for slug, record_id in record_ids.items()
    }


def test_v2_to_v3_roundtrip_preserves_rich_normalized_semantics(tmp_path: Path):
    fixture = create_rich_store_fixture(
        tmp_path / "roundtrip",
        store_format=StoreFormat.V2,
    )
    v2_repo = open_translation_store(fixture.project, default_format=StoreFormat.V2)
    baseline = normalized_semantic_projection(v2_repo.materialize_v2())

    assert _effective_by_slug(baseline, fixture.record_ids) == {
        "wasp": {
            "selected_kind": "review",
            "selected_ref": "R2.1",
            "version_ref": "1.2",
            "review_ref": "R2.1",
            "review_chain": ["R1.1", "R1.2", "R2.1"],
            "target": "Die Wespenkundschafterin kam nun schliesslich an.",
        },
        "mantis": {
            "selected_kind": "translation",
            "selected_ref": "1.1",
            "version_ref": "1.1",
            "review_ref": None,
            "review_chain": [],
            "target": "Die Mantis-Kapitaenin antwortete.",
        },
        "beetle": {
            "selected_kind": "translation",
            "selected_ref": "1.1",
            "version_ref": "1.1",
            "review_ref": None,
            "review_chain": [],
            "target": "Der Kaeferarchivar schrieb neue Notizen.",
        },
        "cicada": {
            "selected_kind": "translation",
            "selected_ref": "1.1",
            "version_ref": "1.1",
            "review_ref": None,
            "review_chain": [],
            "target": "Die Zikadensaengerin wartete.",
        },
    }

    v3_repo = open_translation_store(fixture.project, default_format=StoreFormat.V3)
    v3_repo.write_materialized_v2(v2_repo.materialize_v2())
    roundtrip = normalized_semantic_projection(v3_repo.materialize_v2())
    assert roundtrip == baseline


def test_v2_and_v3_match_build_and_editor_index_goldens(tmp_path: Path):
    fixture_v2 = create_rich_store_fixture(
        tmp_path / "v2",
        store_format=StoreFormat.V2,
        activate_stale_review=False,
    )
    fixture_v3 = create_rich_store_fixture(
        tmp_path / "v3",
        store_format=StoreFormat.V3,
        activate_stale_review=False,
    )

    assert build_output_text(fixture_v2.project) == EXPECTED_BUILD_TEXT
    assert build_output_text(fixture_v3.project) == EXPECTED_BUILD_TEXT

    v2_indexes = normalized_editor_indexes(fixture_v2.project)
    v3_indexes = normalized_editor_indexes(fixture_v3.project)
    assert v3_indexes == v2_indexes

    beetle_id = fixture_v2.record_ids["beetle"]
    wasp_id = fixture_v2.record_ids["wasp"]
    mantis_id = fixture_v2.record_ids["mantis"]
    cicada_id = fixture_v2.record_ids["cicada"]

    assert v2_indexes["findings"] == []

    assert v2_indexes["source"]["record_count"] == 6
    assert v2_indexes["target"]["record_count"] == 4
    assert v2_indexes["target"]["missing_count"] == 2
    assert v2_indexes["source_target"]["record_count"] == 6
    assert v2_indexes["source_target"]["translated_count"] == 4

    wasp = v2_indexes["source_target"]["records"][wasp_id]
    mantis = v2_indexes["source_target"]["records"][mantis_id]
    beetle = v2_indexes["source_target"]["records"][beetle_id]
    cicada = v2_indexes["source_target"]["records"][cicada_id]

    assert wasp["selected_kind"] == "review"
    assert wasp["selected_ref"] == "R2.1"
    assert wasp["review_chain"] == ["R1.1", "R1.2", "R2.1"]
    assert wasp["target"] == "Die Wespenkundschafterin kam nun schliesslich an."

    assert mantis["selected_kind"] == "translation"
    assert mantis["selected_ref"] == "1.1"
    assert mantis["target"] == "Die Mantis-Kapitaenin antwortete."

    assert beetle["selected_kind"] == "translation"
    assert beetle["selected_ref"] == "1.1"
    assert beetle["target"] == "Der Kaeferarchivar schrieb neue Notizen."

    assert cicada["selected_kind"] == "translation"
    assert cicada["selected_ref"] == "1.1"
    assert cicada["target"] == "Die Zikadensaengerin wartete."
