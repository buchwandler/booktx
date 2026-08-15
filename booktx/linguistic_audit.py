"""Deterministic, conservative linguistic audits for translation submissions."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Literal

from booktx.grammar_audit import audit_text as audit_legacy_grammar_text
from booktx.quality_backends import LinguisticBackend
from booktx.quality_backends.languagetool import LocalLanguageToolBackend
from booktx.translation_quality import resolve_submission_quality_policy

if TYPE_CHECKING:
    from booktx.models import SubmissionQualityConfig

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


def _finding_severity(
    configured: Literal["off", "warn", "error"] | None, *, strict: bool
) -> Literal["warn", "error"] | None:
    if configured == "off":
        return None
    if configured is not None:
        return configured
    return "error" if strict else "warn"


def audit_text(
    source: str,
    target: str,
    record_id: str,
    *,
    locale: str,
    strict: bool = False,
    target_language_rules: bool = True,
    suspicious_length_ratio: Literal["off", "warn", "error"] | None = None,
) -> list[LinguisticAuditFinding]:
    """Audit one source/target pair using high-confidence offline rules."""
    if not locale.strip().casefold().replace("_", "-").startswith("de"):
        return []
    source_visible = _visible(source)
    target_visible = _visible(target)
    findings: list[LinguisticAuditFinding] = []

    severity = _finding_severity(None, strict=strict) or "warn"
    if target_language_rules:
        # Only flag an auxiliary-only German clause when the English source
        # itself carries a perfect-tense lexical verb. This avoids possession.
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
                    severity,
                    "de_auxiliary_predicate_missing",
                    "German auxiliary clause ends after a noun phrase; a lexical "
                    "predicate may be missing from the translation.",
                    target_visible,
                )
            )

    length_severity = _finding_severity(
        suspicious_length_ratio, strict=strict
    )
    if (
        length_severity is not None
        and len(source_visible) >= 80
        and len(target_visible) < max(12, len(source_visible) // 5)
    ):
        findings.append(
            LinguisticAuditFinding(
                record_id,
                length_severity,
                "de_suspicious_length_ratio",
                "target is unusually short compared with the source; inspect "
                "for omission.",
                target_visible,
            )
        )

    repeated = (
        _REPEATED_WORD_RE.search(target_visible) if target_language_rules else None
    )
    if repeated:
        findings.append(
            LinguisticAuditFinding(
                record_id,
                severity,
                "de_repeated_word",
                f"word {repeated.group(1)!r} is repeated consecutively.",
                target_visible,
            )
        )

    if target_language_rules:
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
    config: SubmissionQualityConfig | None = None,
    requested_quality: str | None = None,
    backend: LinguisticBackend | None = None,
    ignored_terms_by_id: dict[str, tuple[str, ...]] | None = None,
    ignored_categories: tuple[str, ...] = (),
) -> list[LinguisticAuditFinding]:
    """Audit ``(record_id, source, target)`` tuples in order.

    The legacy ``strict`` argument remains supported for callers outside the
    submission workflow. New callers should pass ``config`` so configured
    severities and backend selection are authoritative.
    """
    policy = resolve_submission_quality_policy(
        config,
        requested_quality=(
            requested_quality
            if requested_quality is not None
            else ("strict" if strict else None)
        ),
    )
    if config is not None and policy.mode == "protocol":
        return []
    configured_rules = config is None or policy.linguistic_audit != "off"
    configured_length = (
        None if config is None else policy.suspicious_length_ratio
    )
    if (
        config is not None
        and backend is None
        and policy.grammar_backend == "languagetool-local"
    ):
        if not policy.grammar_backend_command:
            raise ValueError(
                "languagetool-local requires grammar_backend_command"
            )
        backend = LocalLanguageToolBackend(
            command=policy.grammar_backend_command,
            expected_version=policy.grammar_backend_version,
            timeout_seconds=policy.grammar_backend_timeout_seconds,
        )
    findings: list[LinguisticAuditFinding] = []
    for record_id, source, target in records:
        findings.extend(
            audit_text(
                source,
                target,
                record_id,
                locale=locale,
                strict=strict,
                target_language_rules=(
                    configured_rules
                    and (config is None or policy.target_language_rules)
                ),
                suspicious_length_ratio=configured_length,
            )
        )
        if backend is not None:
            terms = (ignored_terms_by_id or {}).get(record_id, ())
            backend_findings = backend.audit(
                locale=locale,
                record_id=record_id,
                source=source,
                target=target,
                ignored_terms=terms,
                ignored_categories=ignored_categories
                or tuple(
                    getattr(config, "grammar_backend_ignored_categories", [])
                ),
            )
            backend_severity: Literal["warn", "error"] = (
                "error"
                if config is not None and policy.linguistic_audit == "error"
                else "warn"
            )
            findings.extend(
                LinguisticAuditFinding(
                    record_id,
                    backend_severity,
                    item.rule,
                    item.message,
                    item.excerpt,
                )
                for item in backend_findings
            )
    return findings
