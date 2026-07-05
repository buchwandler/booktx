from types import SimpleNamespace

from booktx.context import TranslationContext
from booktx.source_analysis import (
    AnalysisCapabilities,
    SourceAnalysisReport,
    SourceAnalysisSettings,
    SourceCandidate,
    SourceStyleMetrics,
)
from booktx.source_analysis_context import SourceAnalysisDecisions
from booktx.source_interview import INTERVIEW_SCHEMA, build_ledger, render_card


def _report(candidates):
    return SourceAnalysisReport(
        identity_ruleset_version="1",
        analysis_ruleset_version="1",
        source_sha256="s",
        extracted_input_sha256="e",
        chapter_map_sha256="c",
        analysis_sha256="a",
        source_language="en",
        generated_at="now",
        settings=SourceAnalysisSettings(
            engine_requested="simple",
            engine_resolved="simple",
            spacy_model=None,
            min_count=1,
            ngram_max=4,
            include_common=False,
            top=10,
        ),
        capabilities=AnalysisCapabilities(
            tokenizer=False,
            sentence_boundaries=False,
            lemmatizer=False,
            pos=False,
            parser=False,
            noun_chunks=False,
            ner=False,
        ),
        record_count=1,
        chapter_count=1,
        style_metrics=SourceStyleMetrics(
            record_count_with_dialogue=0,
            dialogue_record_ratio=0.0,
            em_dash_count=0,
            emphasis_count=0,
        ),
        candidates=candidates,
    )


def _candidate(cid, text, bucket, risk=1.0, count=1, chapters=1, record="0001:0001"):
    return SourceCandidate(
        id=cid,
        text=text,
        normalized=text.casefold(),
        kind="invented_term",
        count=count,
        record_frequency=count,
        chapter_frequency=chapters,
        score=1.0,
        uncommon_score=1.0,
        first_record_id=record,
        reason="needs policy",
        review_bucket=bucket,
        risk_score=risk,
    )


def _context():
    return TranslationContext(source_language="en", target_language="de")


def _project(tmp_path):
    profile_dir = tmp_path / "translations" / "p"
    profile_dir.mkdir(parents=True)
    return SimpleNamespace(
        root=tmp_path,
        profile_dir=profile_dir,
        profile_name="p",
        config=SimpleNamespace(source_language="en"),
        profile_config=SimpleNamespace(target_language="de", target_locale="de-DE"),
    )


def test_ledger_schema_ordering_and_card(tmp_path):
    report = _report(
        [
            _candidate("C2", "maybe", "maybe", risk=9, count=9),
            _candidate("C1", "binding", "binding_glossary", risk=1, count=1),
        ]
    )
    ledger = build_ledger(
        "p", report, _context(), SourceAnalysisDecisions(), _project(tmp_path)
    )
    assert ledger.schema_name == INTERVIEW_SCHEMA
    assert [item.candidate_id for item in ledger.items] == ["C1", "C2"]
    dumped = ledger.model_dump_json(by_alias=True)
    assert '"schema":"booktx.source-interview.v1"' in dumped
    card = render_card(ledger, ledger.items[0])
    assert "Source interview: C1" in card
    assert "booktx source interview-answer BOOK" in card


def test_ledger_suppresses_no_action_and_ignored(tmp_path):
    report = _report(
        [
            _candidate("C1", "skip", "no_action"),
            _candidate("C2", "ignore", "binding_glossary"),
            _candidate("C3", "keep", "binding_glossary"),
        ]
    )
    decisions = SourceAnalysisDecisions.model_validate(
        {
            "dispositions": [
                {
                    "candidate_id": "C2",
                    "normalized": "ignore",
                    "disposition": "ignored",
                    "reason": "done",
                    "decided_by": "test",
                    "decided_at": "now",
                }
            ]
        }
    )
    ledger = build_ledger("p", report, _context(), decisions, _project(tmp_path))
    assert [item.candidate_id for item in ledger.items] == ["C3"]
