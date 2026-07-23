from __future__ import annotations

from booktx.grammar_audit import audit_text
from booktx.linguistic_audit import audit_text as audit_translation_text


def test_known_grammar_copy_regressions_are_flagged() -> None:
    joined = audit_text(
        "Nicht, wenn ich so schnell zurückbin, dass sie es nicht merken.",
        "r1",
    )
    assert any(
        f.rule == "grammar_separable_verb_spacing" and f.severity == "error"
        for f in joined
    )

    case = audit_text(
        (
            "Sie war in braunem und grünem Stoff gekleidet, "
            "strapazierfähiges Zeug, wie er es nicht kannte."
        ),
        "r2",
    )
    assert any(
        f.rule == "grammar_apposition_case" and f.severity == "error" for f in case
    )

    dangling = audit_text(
        (
            "Gehemmt von dem ungewohnten Umhang, auf unbekanntem Grund, "
            "würde es nicht gut für ihn ausgehen."
        ),
        "r3",
    )
    assert any(
        f.rule == "grammar_dangling_participial_phrase" and f.severity == "warn"
        for f in dangling
    )


def test_german_missing_predicate_regression_is_flagged() -> None:
    findings = audit_translation_text(
        "She has killed my son<i>.</i>",
        "Sie hat meinen Sohn<i>.</i>",
        "0196-000018",
        locale="de-DE",
    )
    assert any(f.rule == "de_auxiliary_predicate_missing" for f in findings)


def test_german_valid_auxiliary_constructions_pass() -> None:
    assert not audit_translation_text(
        "She has killed my son<i>.</i>",
        "Sie hat meinen Sohn gesehen<i>.</i>",
        "r-valid-perfect",
        locale="de-DE",
    )
    assert not audit_translation_text(
        "She has a son.",
        "Sie hat einen Sohn.",
        "r-valid-possession",
        locale="de-DE",
    )
    assert not audit_translation_text(
        "She is a doctor.",
        "Sie ist Ärztin.",
        "r-valid-copula",
        locale="de-DE",
    )


def test_german_rules_are_locale_scoped() -> None:
    assert not audit_translation_text(
        "She has killed my son<i>.</i>",
        "Sie hat meinen Sohn<i>.</i>",
        "r-non-german",
        locale="fr-FR",
    )
