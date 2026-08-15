"""Shared first-pass translation quality policy and prompt primitives.

The translation and grammar-judge workflows intentionally share this module.
It keeps the target-language checklist, quality-mode semantics, and policy
identity in one place so lint, acceptance, and generated agent instructions
cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from booktx.versioning import canonical_json_sha256

if TYPE_CHECKING:
    from booktx.models import SubmissionQualityConfig

QualityMode = Literal["protocol", "basic", "strict"]
QualitySeverity = Literal["off", "warn", "error"]
SelfReviewMode = Literal["off", "optional", "required"]

QUALITY_MODES: tuple[QualityMode, ...] = ("protocol", "basic", "strict")

# This is deliberately a checklist of high-value review dimensions, not a
# claim that a regular expression audit can prove German grammar.
GERMAN_GRAMMAR_CHECKLIST: tuple[str, ...] = (
    "subject–verb agreement",
    "case government and apposition agreement",
    "article and adjective endings",
    "pronoun reference",
    "finite-verb position and separable verbs",
    "tense and mood consistency",
    "punctuation and German quotation marks",
    "capitalization and spelling",
    "dangling participial or infinitive constructions",
    "sentence-boundary preservation",
)


def is_german_locale(locale: str) -> bool:
    """Return whether ``locale`` identifies a German target language."""
    return locale.strip().casefold().replace("_", "-").startswith("de")


def render_target_language_checklist(locale: str) -> list[str]:
    """Render the canonical checklist as plain Markdown bullet lines."""
    if not is_german_locale(locale):
        return [
            "target-language grammar, spelling, punctuation, and sentence completeness",
            "target-only reread for natural syntax and reference clarity",
        ]
    return list(GERMAN_GRAMMAR_CHECKLIST)


@dataclass(frozen=True, slots=True)
class ResolvedSubmissionQualityPolicy:
    """Effective profile policy plus the requested command quality mode."""

    mode: QualityMode
    linguistic_audit: QualitySeverity
    target_language_rules: bool
    suspicious_length_ratio: QualitySeverity
    self_review: SelfReviewMode
    grammar_backend: str
    grammar_backend_command: str | None
    grammar_backend_version: str | None
    grammar_backend_timeout_seconds: float
    grammar_backend_ignored_categories: tuple[str, ...]

    @property
    def self_review_required(self) -> bool:
        return self.self_review == "required"

    @property
    def run_linguistic_audit(self) -> bool:
        return (
            self.mode != "protocol"
            and self.linguistic_audit != "off"
            and self.target_language_rules
        )

    @property
    def backend_identity(self) -> str:
        return ":".join(
            [
                self.grammar_backend,
                self.grammar_backend_command or "",
                self.grammar_backend_version or "",
            ]
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "linguistic_audit": self.linguistic_audit,
            "target_language_rules": self.target_language_rules,
            "suspicious_length_ratio": self.suspicious_length_ratio,
            "self_review": self.self_review,
            "grammar_backend": self.grammar_backend,
            "grammar_backend_command": self.grammar_backend_command,
            "grammar_backend_version": self.grammar_backend_version,
            "grammar_backend_timeout_seconds": self.grammar_backend_timeout_seconds,
            "grammar_backend_ignored_categories": list(
                self.grammar_backend_ignored_categories
            ),
        }

    @property
    def fingerprint(self) -> str:
        return canonical_json_sha256(self.as_dict())


def resolve_submission_quality_policy(
    config: SubmissionQualityConfig | None,
    *,
    requested_quality: str | None = None,
) -> ResolvedSubmissionQualityPolicy:
    """Resolve legacy/profile configuration and an optional CLI quality mode."""
    if requested_quality is None:
        requested_quality = (
            "strict" if config and config.linguistic_audit == "error" else "basic"
        )
    if requested_quality not in QUALITY_MODES:
        raise ValueError("quality must be protocol, basic, or strict")

    # Defaults preserve the current behavior of profiles without the optional
    # [submission_quality] table: basic linguistic warnings are advisory.
    return ResolvedSubmissionQualityPolicy(
        mode=requested_quality,
        linguistic_audit=config.linguistic_audit if config else "warn",
        target_language_rules=config.target_language_rules if config else True,
        suspicious_length_ratio=(
            config.suspicious_length_ratio if config else "warn"
        ),
        self_review=config.self_review if config else "optional",
        grammar_backend=config.grammar_backend if config else "builtin",
        grammar_backend_command=(
            config.grammar_backend_command if config else None
        ),
        grammar_backend_version=(
            config.grammar_backend_version if config else None
        ),
        grammar_backend_timeout_seconds=(
            config.grammar_backend_timeout_seconds if config else 10.0
        ),
        grammar_backend_ignored_categories=tuple(
            config.grammar_backend_ignored_categories if config else []
        ),
    )


def severity_for_policy(
    configured: QualitySeverity,
    *,
    mode: QualityMode,
) -> Literal["info", "warn", "error"] | None:
    """Resolve a configured finding severity for a quality mode.

    ``basic`` blocks only configured errors; ``strict`` blocks warnings and
    errors. ``protocol`` does not run linguistic findings at all.
    """
    if configured == "off" or mode == "protocol":
        return None
    if configured == "error":
        return "error"
    return "warn"


def finding_blocks(
    severity: Literal["info", "warn", "error"], *, mode: QualityMode
) -> bool:
    """Return whether a linguistic finding blocks the resolved mode."""
    return mode == "strict" and severity in {"warn", "error"} or (
        mode == "basic" and severity == "error"
    )


def render_target_quality_prompt(locale: str) -> list[str]:
    """Return the reusable target-language self-review prompt lines."""
    lines = [
        "## Target-language quality gate",
        "",
        "The ingest block must contain final prose, not first drafts.",
        "",
        "For every source record:",
        "1. Draft the translation using the binding context, glossary, termbase, "
        "and continuity evidence.",
        "2. Before writing the final target, reread the target sentence on its own "
        "as target-language prose, without following the source syntax.",
        "3. Check the complete target-language grammar checklist internally.",
        "4. Recompare against SOURCE only for omissions, meaning drift, names, "
        "terminology, placeholders, markup, and sentence coverage.",
        "5. Write only the corrected final target sentence to the ingest block.",
        "",
        "Do not emit intermediate drafts, explanations, or checklist answers.",
        "",
        "For the completed batch, reread the target records once in order and "
        "correct only clear grammar, reference, agreement, punctuation, or "
        "continuity defects. Do not rewrite already-correct prose merely for "
        "stylistic variation.",
        "",
        "Target-language checklist:",
    ]
    lines.extend(f"- {item}" for item in render_target_language_checklist(locale))
    return lines


def policy_fingerprint(
    config: SubmissionQualityConfig | None,
    *,
    requested_quality: str | None = None,
) -> str:
    """Return the stable identity used by validation receipts."""
    return resolve_submission_quality_policy(
        config, requested_quality=requested_quality
    ).fingerprint


__all__ = [
    "GERMAN_GRAMMAR_CHECKLIST",
    "QUALITY_MODES",
    "QualityMode",
    "ResolvedSubmissionQualityPolicy",
    "finding_blocks",
    "is_german_locale",
    "policy_fingerprint",
    "render_target_language_checklist",
    "render_target_quality_prompt",
    "resolve_submission_quality_policy",
    "severity_for_policy",
]
