"""Deterministic, conservative linguistic audits for translation submissions."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Literal

from booktx.grammar_audit import audit_text as audit_legacy_grammar_text

__all__ = ["LinguisticAuditFinding", "audit_text", "audit_records"]


@dataclass(frozen=True, slots=True)
class LinguisticAuditFinding:
    record_id: str
    severity: Literal["info", "warn", "error"]
    rule: str
    message: str
    excerpt: str = ""

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


_TAG_RE = re.compile(r"<[^>]+>")
_ENGLISH_AUXILIARY_RE = re.compile(
    r"\b(?:has|have|had)\s+[A-Za-z][A-Za-z'-]*(?:ed|en)\b", re.IGNORECASE
)
_GERMAN_PARTICIPLE_RE = re.compile(
    r"\b(?:ge[A-Za-zÄÖÜäöüß-]+(?:t|en)|[A-Za-zÄÖÜäöüß-]+(?:iert|t))\b",
    re.IGNORECASE,
)
_GERMAN_AUXILIARY_RE = re.compile(r"\b(?:hat|haben|ist|sind|wird|wurde)\b", re.I)
_GERMAN_ACCUSATIVE_NP_RE = re.compile(
    r"\b(?:hat|haben)\s+(?:den|die|das|einen|eine|ein|meinen|meine|mein|ihren|ihre|ihr)\s+"
    r"[A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]+\s*[.!?]$",
    re.IGNORECASE,
)
_REPEATED_WORD_RE = re.compile(r"\b([A-Za-zÄÖÜäöüß]+)\s+\1\b", re.IGNORECASE)


def _visible(text: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub("", text)).strip()


def _is_german(locale: str) -> bool:
    return locale.strip().casefold().replace("_", "-").startswith("de")


def audit_text(
    source: str,
    target: str,
    record_id: str,
    *,
    locale: str,
    strict: bool = False,
) -> list[LinguisticAuditFinding]:
    """Audit one source/target pair using high-confidence offline rules."""
    if not _is_german(locale):
        return []
    source_visible = _visible(source)
    target_visible = _visible(target)
    findings: list[LinguisticAuditFinding] = []

    # Only flag an auxiliary-only German clause when the English source itself
    # carries a perfect-tense lexical verb. This avoids flagging possession.
    german_auxiliary = _GERMAN_AUXILIARY_RE.search(target_visible)
    predicate_after_auxiliary = (
        _GERMAN_PARTICIPLE_RE.search(target_visible[german_auxiliary.end() :])
        if german_auxiliary
        else None
    )
    if (
        _ENGLISH_AUXILIARY_RE.search(source_visible)
        and _GERMAN_ACCUSATIVE_NP_RE.search(target_visible)
        and predicate_after_auxiliary is None
    ):
        findings.append(
            LinguisticAuditFinding(
                record_id,
                "error" if strict else "warn",
                "de_auxiliary_predicate_missing",
                "German auxiliary clause ends after a noun phrase; a lexical "
                "predicate may be missing from the translation.",
                target_visible,
            )
        )

    if len(source_visible) >= 80 and len(target_visible) < max(
        12, len(source_visible) // 5
    ):
        findings.append(
            LinguisticAuditFinding(
                record_id,
                "error" if strict else "warn",
                "de_suspicious_length_ratio",
                "target is unusually short compared with the source; inspect "
                "for omission.",
                target_visible,
            )
        )

    repeated = _REPEATED_WORD_RE.search(target_visible)
    if repeated:
        findings.append(
            LinguisticAuditFinding(
                record_id,
                "error" if strict else "warn",
                "de_repeated_word",
                f"word {repeated.group(1)!r} is repeated consecutively.",
                target_visible,
            )
        )

    for legacy in audit_legacy_grammar_text(target_visible, record_id):
        findings.append(
            LinguisticAuditFinding(
                record_id,
                legacy.severity,
                legacy.rule,
                legacy.message,
                legacy.excerpt,
            )
        )
    return findings


def audit_records(
    records: list[tuple[str, str, str]],
    *,
    locale: str,
    strict: bool = False,
) -> list[LinguisticAuditFinding]:
    """Audit ``(record_id, source, target)`` tuples in order."""
    findings: list[LinguisticAuditFinding] = []
    for record_id, source, target in records:
        findings.extend(
            audit_text(source, target, record_id, locale=locale, strict=strict)
        )
    return findings
