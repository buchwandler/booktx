from __future__ import annotations

import json
from types import SimpleNamespace

from booktx.linguistic_audit import audit_records
from booktx.models import SubmissionQualityConfig
from booktx.quality_backends.languagetool import LocalLanguageToolBackend
from booktx.translation_quality import (
    GERMAN_GRAMMAR_CHECKLIST,
    finding_blocks,
    render_target_quality_prompt,
    resolve_submission_quality_policy,
)


def test_quality_modes_have_explicit_blocking_semantics() -> None:
    config = SubmissionQualityConfig(linguistic_audit="error")
    assert resolve_submission_quality_policy(config).mode == "strict"
    assert finding_blocks("error", mode="basic")
    assert finding_blocks("warn", mode="strict")
    assert not finding_blocks("warn", mode="basic")
    assert not finding_blocks("error", mode="protocol")


def test_german_prompt_uses_canonical_checklist_without_reasoning_output() -> None:
    prompt = "\n".join(render_target_quality_prompt("de-DE"))
    assert "reread the target sentence on its own" in prompt
    assert "Do not emit intermediate drafts" in prompt
    assert all(item in prompt for item in GERMAN_GRAMMAR_CHECKLIST)


def test_configured_length_rule_can_be_disabled() -> None:
    source = "This is a very long source sentence " * 5
    target = "Kurz."
    config = SubmissionQualityConfig(suspicious_length_ratio="off")
    assert not audit_records(
        [("0001-000001", source, target)], locale="de-DE", config=config
    )


def test_german_repeated_word_is_an_advisory_in_basic_mode() -> None:
    findings = audit_records(
        [("0001-000001", "She knew her.", "Sie wusste, dass sie sie kannte.")],
        locale="de-DE",
    )

    repeated = [finding for finding in findings if finding.rule == "de_repeated_word"]
    assert len(repeated) == 1
    assert repeated[0].severity == "warn"


def test_local_languagetool_adapter_is_pinned_and_parses_json(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="LanguageTool 6.0", stderr="")
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "matches": [
                        {
                            "message": "Check agreement",
                            "offset": 0,
                            "length": 4,
                            "rule": {
                                "id": "DE_AGREEMENT",
                                "issueType": "grammar",
                                "category": {"id": "GRAMMAR"},
                            },
                        }
                    ]
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("booktx.quality_backends.languagetool.subprocess.run", fake_run)
    backend = LocalLanguageToolBackend("languagetool", expected_version="6.0")
    findings = backend.audit(
        locale="de-DE",
        record_id="r1",
        source="source",
        target="Das Haus.",
    )
    assert backend.identity.endswith(":6.0")
    assert findings[0].rule == "languagetool:DE_AGREEMENT"
    assert calls[0][-1] == "--version"
    assert calls[1][-1] == "--json"
