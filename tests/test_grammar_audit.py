from __future__ import annotations

from booktx.grammar_audit import audit_text


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
        f.rule == "grammar_apposition_case" and f.severity == "error"
        for f in case
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
