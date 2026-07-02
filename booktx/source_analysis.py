"""Deterministic source-level analysis for booktx (Phase 0 + Phase 1A).

This module inspects the *extracted* source representation and proposes likely
important words, names, invented terms, repeated phrases, and style signals
*before* translation starts. Its output is **evidence**, never policy:

* ``context.json`` remains canonical for profile-local translation decisions.
* ``.booktx/names.json`` is never mutated by analysis.
* Generated reports never contain approved translation decisions.

Phase 0 contracts (stable candidate identity, extracted-input fingerprint,
semantic digest, source-text preparation, blocking preflight) and the Phase 1A
simple engine (no spaCy dependency) live here. spaCy enrichment, the decisions
sidecar, context prefill, and candidate promotion are deliberately out of scope
and must not be added in this phase.

The JSON report (``SourceAnalysisReport``) is authoritative; the Markdown view
(``render_report_markdown``) is a generated readable rendering of that JSON.
"""

# ruff: noqa: E501

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from hashlib import sha256
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from booktx.epub_inline_xhtml import strip_inline_xhtml
from booktx.errors import BooktxError, _err

if TYPE_CHECKING:
    from booktx.chapters import ChapterMap
    from booktx.config import Project
    from booktx.models import Chunk, Record

__all__ = [
    "IDENTITY_RULESET_VERSION",
    "ANALYSIS_RULESET_VERSION",
    "COMMON_WORDS_VERSION",
    "ANALYSIS_SCHEMA",
    "SNAPSHOT_SCHEMA",
    "AnalysisCapabilities",
    "SourceAnalysisSettings",
    "SourceAnalysisOccurrence",
    "SourceCandidate",
    "SourceStyleMetrics",
    "SourceAnalysisReport",
    "SourceAnalysisSnapshot",
    "PreparedRecord",
    "SnapshotRead",
    "SnapshotValidationError",
    "prepare_record",
    "prepare_records",
    "candidate_id_from_identity",
    "extracted_input_sha256",
    "compute_analysis_sha256",
    "source_analysis_preflight",
    "build_source_analysis",
    "build_snapshot",
    "validate_snapshot_payload",
    "read_snapshot",
    "read_canonical_report",
    "render_report_markdown",
    "common_word_set",
    "common_words_metadata",
    "resolve_engine",
    "CaseBucket",
]


# --- Ruleset / schema versions ----------------------------------------------

#: Identity-ruleset version. Bumping this changes every candidate id and requires
#: an explicit migration. Candidate identity must never depend on score, rank,
#: detector kind, spaCy model, occurrence count, or analysis settings.
IDENTITY_RULESET_VERSION = "1"

#: Analysis-ruleset version. Covers scoring constants, detector behaviour,
#: normalization, phrase-boundary/overlap rules, and bundled common-word data.
ANALYSIS_RULESET_VERSION = "1"

#: Version stamp of the bundled common-word lists (feeds the analysis ruleset).
COMMON_WORDS_VERSION = "2026.07"

ANALYSIS_SCHEMA: Literal["booktx.source-analysis.v1"] = "booktx.source-analysis.v1"
SNAPSHOT_SCHEMA: Literal["booktx.source-analysis-snapshot.v1"] = (
    "booktx.source-analysis-snapshot.v1"
)


# --- Scoring constants (owned by ANALYSIS_RULESET_VERSION) -------------------

_PHRASE_BONUS = 1.5
_PHRASE_KINDS = frozenset({"phrase", "title_candidate", "hyphenated_term"})
_COMMON_PENALTY = 0.2
_SNIPPET_WIDTH = 120
_MAX_EXAMPLES_PER_CANDIDATE = 3


# --- Data models ------------------------------------------------------------


class AnalysisCapabilities(BaseModel):
    """Resolved engine capabilities, recorded independently of the request."""

    model_config = ConfigDict(extra="forbid")

    tokenizer: bool
    sentence_boundaries: bool
    lemmatizer: bool
    pos: bool
    parser: bool
    noun_chunks: bool
    ner: bool


class SourceAnalysisSettings(BaseModel):
    """Effective analysis settings used to produce a report."""

    model_config = ConfigDict(extra="forbid")

    engine_requested: Literal["auto", "spacy", "simple"]
    engine_resolved: Literal["spacy", "simple"]
    spacy_model: str | None = None
    spacy_version: str | None = None
    model_version: str | None = None
    min_count: int
    ngram_max: int
    top: int
    include_common: bool


class SourceAnalysisOccurrence(BaseModel):
    """One bounded evidence occurrence of a candidate."""

    model_config = ConfigDict(extra="forbid")

    record_id: str
    chapter_id: str | None = None
    chapter_title: str | None = None
    visible_text: str
    snippet: str


class SourceCandidate(BaseModel):
    """One merged analysis candidate with a stable content-derived id."""

    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    normalized: str
    surface_forms: list[str] = Field(default_factory=list)
    lemma: str | None = None
    kind: Literal[
        "word",
        "phrase",
        "proper_name",
        "place_name",
        "hyphenated_term",
        "invented_term",
        "title_candidate",
    ]
    detectors: list[str] = Field(default_factory=list)
    category_hint: str | None = None
    count: int
    record_frequency: int
    chapter_frequency: int
    score: float
    uncommon_score: float
    first_record_id: str | None = None
    examples: list[SourceAnalysisOccurrence] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    reason: str = ""
    already_protected: bool = False
    suggested_context_action: Literal[
        "none",
        "ask_question",
        "add_advisory_glossary",
        "review_name_policy",
        "review_for_binding_glossary",
    ] = "none"


class SourceStyleMetrics(BaseModel):
    """Structured style observations (not synthetic candidates)."""

    model_config = ConfigDict(extra="forbid")

    record_count_with_dialogue: int
    dialogue_record_ratio: float
    quote_counts: dict[str, int] = Field(default_factory=dict)
    em_dash_count: int
    emphasis_count: int
    sentence_count: int | None = None
    average_sentence_words: float | None = None
    capability_warnings: list[str] = Field(default_factory=list)


class SourceAnalysisReport(BaseModel):
    """Authoritative generated source-analysis evidence (JSON)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["booktx.source-analysis.v1"] = Field(
        default="booktx.source-analysis.v1", alias="schema"
    )
    identity_ruleset_version: str
    analysis_ruleset_version: str
    source_sha256: str
    extracted_input_sha256: str
    chapter_map_sha256: str
    analysis_sha256: str
    source_language: str
    generated_at: str
    settings: SourceAnalysisSettings
    capabilities: AnalysisCapabilities
    record_count: int
    chapter_count: int
    candidates: list[SourceCandidate]
    style_metrics: SourceStyleMetrics
    warnings: list[str] = Field(default_factory=list)


class SourceAnalysisSnapshot(BaseModel):
    """Profile-local snapshot envelope embedding the canonical report."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["booktx.source-analysis-snapshot.v1"] = Field(
        default="booktx.source-analysis-snapshot.v1", alias="schema"
    )
    generated: Literal[True] = True
    canonical: Literal[False] = False
    profile: str
    snapshot_generated_at: str
    source_sha256: str
    extracted_input_sha256: str
    analysis_sha256: str
    report: SourceAnalysisReport


# --- Bundled common-word lists ----------------------------------------------
#
# Small curated lists of very frequent function words / common words for the
# initially supported language (English). These are intentionally tiny: they
# only need to suppress obvious noise. Unsupported languages still run using
# corpus-internal signals and emit a warning.

_COMMON_WORDS_EN = frozenset(
    """
    a an the and or but if then else of to in on at by for with from into onto
    upon over under above below between among through during before after as is
    are was were be been being am do does did doing have has had having i you he
    she it we they me him her us them my your his its our their this that these
    those there here not no nor so too very can could shall should will would may
    might must ought about above across against along although among any both each
    few more most other some such only own same than that them then thence there
    these they thine this those thou though three thy til tis unto was wept were
    what when where which while who whom why will with within without yet you your
    hers ourselves yourself yourselves themselves itself myself himself everything
    nothing someone anyone everyone none one two three four five six seven eight
    nine ten first second third new old good bad great little big long short high
    low own all another such
    """.split()
)


def common_word_set(language: str) -> frozenset[str]:
    """Return the bundled common-word set for ``language`` (empty if unknown)."""
    lang = (language or "").lower().split("-")[0]
    if lang == "en":
        return _COMMON_WORDS_EN
    return frozenset()


def common_words_metadata(language: str) -> dict[str, str]:
    """Return source/license/version metadata for the bundled common-word list."""
    lang = (language or "").lower().split("-")[0]
    if lang == "en":
        return {
            "source": "booktx-curated",
            "license": "CC0-1.0 (booktx-curated public domain)",
            "version": COMMON_WORDS_VERSION,
            "language": "en",
        }
    return {
        "source": "none",
        "license": "n/a",
        "version": COMMON_WORDS_VERSION,
        "language": lang,
    }


# --- Canonical hashing helpers ----------------------------------------------


def _canonical_json(payload: object) -> str:
    """Serialize to deterministic compact JSON with sorted keys."""
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _sha256_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


# --- Source-text preparation ------------------------------------------------

# Alphabetic word runs (Unicode). Digits and punctuation are separators for
# candidate purposes; hyphenated compounds are detected separately.
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
# Hyphenated compound: two or more alphabetic runs joined by ASCII hyphens.
_HYPHENATED_RE = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)+", re.UNICODE)
# Matches a placeholder token OR, for EPUB records, a residual inline XHTML tag.
_TOKEN_OR_XHTML_RE = re.compile(r"__(?:NAME|TAG)_(\d+)__|<[^>]*>")
_TOKEN_ONLY_RE = re.compile(r"__(?:NAME|TAG)_(\d+)__")

#: Case-semantic buckets used for stable candidate identity.
CaseBucket = Literal["title", "upper", "lower", "mixed"]


@dataclass(slots=True)
class _Span:
    start: int
    end: int


@dataclass(slots=True)
class PreparedRecord:
    """One record reduced to clean visible analysis text plus span metadata.

    ``visible_text`` has name placeholders restored to visible names, tag
    placeholders restored to their visible text (but flagged opaque), and EPUB
    inline XHTML tags stripped. ``opaque_spans`` mark code-like spans that must
    not generate candidates; ``protected_spans`` mark text restored from a
    protected name (still analyzable, flagged ``already_protected``).
    """

    record_id: str
    chunk_id: str
    source_markup: str
    chapter_id: str | None
    chapter_title: str | None
    visible_text: str
    opaque_spans: list[_Span] = field(default_factory=list)
    protected_spans: list[_Span] = field(default_factory=list)


def _strip_tag_visible(original: str) -> str:
    """Return the human-readable text of a tag placeholder original."""
    return strip_inline_xhtml(original)


def prepare_record(
    record: Record,
    *,
    chunk_id: str,
    chapter_id: str | None = None,
    chapter_title: str | None = None,
) -> PreparedRecord:
    """Reduce one extracted record to clean visible analysis text.

    Steps (Phase 0 contract):

    1. NAME placeholders are restored to their original visible names and the
       resulting span is marked protected (still analyzable, flagged later).
    2. TAG placeholders are restored to their visible text but the span is marked
       opaque so code-like content never becomes a candidate.
    3. For ``epub-inline-xhtml:v1`` records, residual inline XHTML tags are
       stripped (markup removed, inner text kept as prose).
    4. Placeholder tokens and markup therefore never appear in tokenization.
    """
    source = record.source
    is_epub = record.source_markup == "epub-inline-xhtml:v1"
    name_by_token = {
        ph.token: ph.original for ph in record.placeholders if ph.kind == "name"
    }
    tag_by_token = {
        ph.token: ph.original for ph in record.placeholders if ph.kind == "tag"
    }

    pattern = _TOKEN_OR_XHTML_RE if is_epub else _TOKEN_ONLY_RE
    out: list[str] = []
    opaque: list[_Span] = []
    protected: list[_Span] = []
    pos = 0
    for match in pattern.finditer(source):
        # Append the literal text before this match verbatim.
        if match.start() > pos:
            out.append(source[pos : match.start()])
        token = match.group(0)
        if token.startswith("__NAME_"):
            original = name_by_token.get(token, token)
            start = sum(len(part) for part in out)
            out.append(original)
            end = start + len(original)
            if original:
                protected.append(_Span(start, end))
        elif token.startswith("__TAG_"):
            original = tag_by_token.get(token, token)
            visible = _strip_tag_visible(original)
            start = sum(len(part) for part in out)
            out.append(visible)
            end = start + len(visible)
            if visible:
                opaque.append(_Span(start, end))
        else:
            # Residual inline XHTML tag: markup removed, no text contributed.
            pass
        pos = match.end()
    if pos < len(source):
        out.append(source[pos:])

    visible_text = "".join(out)
    return PreparedRecord(
        record_id=record.id,
        chunk_id=chunk_id,
        source_markup=record.source_markup,
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        visible_text=visible_text,
        opaque_spans=opaque,
        protected_spans=protected,
    )


def prepare_records(
    chunks: list[Chunk],
    chapter_by_record: dict[str, dict[str, str | None]],
) -> list[PreparedRecord]:
    """Prepare every record in chunk order, attaching chapter metadata."""
    out: list[PreparedRecord] = []
    for chunk in chunks:
        for record in chunk.records:
            meta = chapter_by_record.get(record.id, {})
            out.append(
                prepare_record(
                    record,
                    chunk_id=chunk.chunk_id,
                    chapter_id=meta.get("chapter_id"),
                    chapter_title=meta.get("chapter_title"),
                )
            )
    return out


# --- Normalization + case bucket --------------------------------------------


def normalize_token(text: str) -> str:
    """Normalize a surface token for identity/frequency grouping."""
    return " ".join(text.casefold().split())


def case_bucket(surface: str) -> CaseBucket:
    """Classify the dominant case pattern of a surface form.

    Two forms that differ only by this bucket (e.g. ``Empire`` vs ``empire``)
    get separate candidate ids when their uses are meaningfully distinct.
    """
    letters = [ch for ch in surface if ch.isalpha()]
    if not letters:
        return "lower"
    upper = sum(1 for ch in letters if ch.isupper())
    if upper == len(letters):
        return "upper"
    if upper == 1 and letters[0].isupper():
        return "title"
    if upper == 0:
        return "lower"
    return "mixed"


def _phrase_bucket(surfaces: list[str]) -> CaseBucket:
    """Case bucket for a multi-token phrase (title if every token is title/upper)."""
    if not surfaces:
        return "lower"
    buckets = {case_bucket(s) for s in surfaces}
    if buckets <= {"title", "upper"}:
        return "title"
    if buckets == {"lower"}:
        return "lower"
    return "mixed"


# --- Stable candidate identity ----------------------------------------------


def candidate_id_from_identity(
    *,
    source_language: str,
    normalized: str,
    tokens: list[str],
    case_bucket_value: CaseBucket,
) -> str:
    """Return a stable content-derived candidate id.

    The identity payload is canonical JSON over (identity ruleset version,
    source language, normalized text, token boundaries, case-semantic bucket)
    and deliberately EXCLUDES score, rank, detector kind, spaCy model,
    occurrence count, and analysis settings.
    """
    payload = {
        "identity_ruleset_version": IDENTITY_RULESET_VERSION,
        "source_language": source_language,
        "normalized": normalized,
        "token_count": len(tokens),
        "tokens": tokens,
        "case_bucket": case_bucket_value,
    }
    digest = sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return "CAND-" + digest[:16].upper()


# --- Extracted-input fingerprint --------------------------------------------


def extracted_input_sha256(
    chunks: list[Chunk],
    *,
    source_language: str,
    source_sha256: str,
    record_id_scheme: str,
    chapter_map: ChapterMap,
    chapter_by_record: dict[str, dict[str, str | None]],
) -> str:
    """Fingerprint the actual extracted representation, not just source bytes.

    Changes when records, placeholders, protected terms, chapter assignments,
    record-id scheme, chapter-map version, or segmentation metadata change,
    even when the raw source bytes are unchanged.
    """
    records_payload: list[dict[str, object]] = []
    for chunk in chunks:
        for record in chunk.records:
            meta = chapter_by_record.get(record.id, {})
            records_payload.append(
                {
                    "id": record.id,
                    "chunk_id": chunk.chunk_id,
                    "source": record.source,
                    "source_markup": record.source_markup,
                    "span_index": record.span_index,
                    "span_record_index": record.span_record_index,
                    "protected_terms": list(record.protected_terms),
                    "placeholders": [
                        {"token": ph.token, "original": ph.original, "kind": ph.kind}
                        for ph in record.placeholders
                    ],
                    "chapter_id": meta.get("chapter_id"),
                    "chapter_title": meta.get("chapter_title"),
                }
            )
    payload = {
        "source_language": source_language,
        "source_sha256": source_sha256,
        "record_id_scheme": record_id_scheme,
        "chapter_map_version": chapter_map.version,
        "chapter_map_source_sha256": chapter_map.source_sha256,
        "records": records_payload,
    }
    return _sha256_text(_canonical_json(payload))


def _chapter_map_sha256(chapter_map: ChapterMap) -> str:
    """Stable digest of the chapter-map structure (ids/titles/ranges)."""
    payload = {
        "version": chapter_map.version,
        "source_sha256": chapter_map.source_sha256,
        "chapters": [
            {
                "chapter_id": ch.chapter_id,
                "title": ch.title,
                "start_record_id": ch.start_record_id,
                "end_record_id": ch.end_record_id,
                "record_count": ch.record_count,
            }
            for ch in chapter_map.chapters
        ],
    }
    return _sha256_text(_canonical_json(payload))


# --- Semantic digest --------------------------------------------------------


_EXCLUDED_FROM_DIGEST = {"analysis_sha256", "generated_at"}


def compute_analysis_sha256(report: SourceAnalysisReport) -> str:
    """Semantic digest over canonical report content.

    Excludes ``analysis_sha256`` itself, ``generated_at``, snapshot envelope
    metadata, and Markdown. Deterministic for unchanged input, rulesets,
    capabilities, model version, and settings.
    """
    payload = report.model_dump(by_alias=True, mode="json")
    for key in _EXCLUDED_FROM_DIGEST:
        payload.pop(key, None)
    return _sha256_text(_canonical_json(payload))


# --- Engine resolution (Phase 1A: simple only) ------------------------------


def resolve_engine(
    engine_requested: str,
    spacy_model: str | None,
) -> tuple[Literal["spacy", "simple"], AnalysisCapabilities, list[str]]:
    """Resolve the engine and report honest capabilities + warnings.

    Phase 1A implements the simple engine only. ``auto`` resolves to ``simple``.
    An explicit ``spacy`` request fails with a controlled error when spaCy is
    not installed (spaCy enrichment is Phase 1B).
    """
    warnings: list[str] = []
    if engine_requested == "spacy":
        try:
            import spacy  # noqa: F401  # capability probe only  # type: ignore[import-not-found]
        except ImportError as exc:
            raise _err(
                "source_analysis_spacy_unavailable",
                "spaCy analysis engine was requested but spaCy is not installed; "
                "use --engine simple or auto (spaCy enrichment is not yet available)",
            ) from exc
        # spaCy present but enrichment is Phase 1B: fall back with a warning.
        warnings.append(
            "spaCy enrichment is not implemented in this build; using the simple engine."
        )
        resolved: Literal["spacy", "simple"] = "simple"
    else:
        resolved = "simple"
    capabilities = AnalysisCapabilities(
        tokenizer=True,
        sentence_boundaries=True,
        lemmatizer=False,
        pos=False,
        parser=False,
        noun_chunks=False,
        ner=False,
    )
    capability_warnings = [
        "sentence_boundaries: heuristic splitter (no linguistic model)"
    ]
    return resolved, capabilities, warnings + capability_warnings


# --- Tokenization of prepared text ------------------------------------------


@dataclass(slots=True)
class _Token:
    surface: str
    normalized: str
    start: int
    end: int
    bucket: CaseBucket
    protected: bool


def _in_any_span(pos: int, spans: list[_Span]) -> bool:
    return any(span.start <= pos < span.end for span in spans)


def _tokenize_prepared(prepared: PreparedRecord) -> list[_Token]:
    """Yield word tokens with positions, skipping opaque spans."""
    text = prepared.visible_text
    opaque = prepared.opaque_spans
    protected = prepared.protected_spans
    tokens: list[_Token] = []
    for match in _WORD_RE.finditer(text):
        start, end = match.start(), match.end()
        # Skip tokens that fall inside an opaque (code-like) span.
        if _in_any_span(start, opaque) or _in_any_span((start + end) // 2, opaque):
            continue
        surface = match.group(0)
        is_protected = _in_any_span(start, protected) or _in_any_span(
            end - 1, protected
        )
        tokens.append(
            _Token(
                surface=surface,
                normalized=normalize_token(surface),
                start=start,
                end=end,
                bucket=case_bucket(surface),
                protected=is_protected,
            )
        )
    return tokens


def _hyphenated_spans(prepared: PreparedRecord) -> list[tuple[int, int, str]]:
    """Hyphenated compounds with positions, skipping opaque spans."""
    text = prepared.visible_text
    opaque = prepared.opaque_spans
    out: list[tuple[int, int, str]] = []
    for match in _HYPHENATED_RE.finditer(text):
        start, end = match.start(), match.end()
        if _in_any_span(start, opaque) or _in_any_span((start + end) // 2, opaque):
            continue
        out.append((start, end, match.group(0)))
    return out


def _snippet(text: str, start: int, end: int) -> str:
    """Bounded evidence window around a span, without internal file paths."""
    width = _SNIPPET_WIDTH
    lo = max(0, start - width // 2)
    hi = min(len(text), end + width // 2)
    window = text[lo:hi].strip()
    if len(window) > width:
        window = window[:width]
    return " ".join(window.split())


# --- Candidate accumulator --------------------------------------------------


@dataclass
class _Accum:
    identity: str
    text: str
    normalized: str
    tokens: list[str]
    bucket: CaseBucket
    kind: str
    detector: str
    reason_codes: list[str]
    count: int = 0
    records: set[str] = field(default_factory=set)
    chapters: set[str] = field(default_factory=set)
    surfaces: dict[str, int] = field(default_factory=dict)
    first_record_id: str | None = None
    examples: list[SourceAnalysisOccurrence] = field(default_factory=list)
    already_protected: bool = False


_KIND_PRECEDENCE = {
    "proper_name": 0,
    "place_name": 1,
    "invented_term": 2,
    "hyphenated_term": 3,
    "title_candidate": 4,
    "phrase": 5,
    "word": 6,
}


def _merge_kind(existing: str, candidate: str) -> str:
    if _KIND_PRECEDENCE[candidate] < _KIND_PRECEDENCE[existing]:
        return candidate
    return existing


def _add_occurrence(
    accum: _Accum,
    *,
    prepared: PreparedRecord,
    surface: str,
    start: int,
    end: int,
) -> None:
    accum.count += 1
    accum.records.add(prepared.record_id)
    if prepared.chapter_id:
        accum.chapters.add(prepared.chapter_id)
    accum.surfaces[surface] = accum.surfaces.get(surface, 0) + 1
    if accum.first_record_id is None:
        accum.first_record_id = prepared.record_id
    if len(accum.examples) < _MAX_EXAMPLES_PER_CANDIDATE:
        accum.examples.append(
            SourceAnalysisOccurrence(
                record_id=prepared.record_id,
                chapter_id=prepared.chapter_id,
                chapter_title=prepared.chapter_title,
                visible_text=prepared.visible_text[start:end],
                snippet=_snippet(prepared.visible_text, start, end),
            )
        )


# --- Detectors --------------------------------------------------------------


def _detect_tokens(
    prepared_records: list[PreparedRecord],
    source_language: str,
    min_count: int,
    accum_by_id: dict[str, _Accum],
) -> None:
    """Word + proper-name + protected-name detection from word tokens."""
    for prepared in prepared_records:
        tokens = _tokenize_prepared(prepared)
        for tok in tokens:
            bucket = tok.bucket
            kind = "proper_name" if bucket == "title" else "word"
            detector = "title_case" if kind == "proper_name" else "frequency"
            identity = candidate_id_from_identity(
                source_language=source_language,
                normalized=tok.normalized,
                tokens=[tok.normalized],
                case_bucket_value=bucket,
            )
            accum = accum_by_id.get(identity)
            if accum is None:
                accum = _Accum(
                    identity=identity,
                    text=tok.surface,
                    normalized=tok.normalized,
                    tokens=[tok.normalized],
                    bucket=bucket,
                    kind=kind,
                    detector=detector,
                    reason_codes=[],
                )
                accum_by_id[identity] = accum
            else:
                accum.kind = _merge_kind(accum.kind, kind)
            if tok.protected:
                accum.already_protected = True
            _add_occurrence(
                accum,
                prepared=prepared,
                surface=tok.surface,
                start=tok.start,
                end=tok.end,
            )


def _detect_hyphenated(
    prepared_records: list[PreparedRecord],
    source_language: str,
    accum_by_id: dict[str, _Accum],
) -> None:
    """Hyphenated-term detection."""
    for prepared in prepared_records:
        for start, end, surface in _hyphenated_spans(prepared):
            normalized = normalize_token(surface)
            tokens = [normalize_token(part) for part in surface.split("-")]
            bucket = case_bucket(surface)
            identity = candidate_id_from_identity(
                source_language=source_language,
                normalized=normalized,
                tokens=tokens,
                case_bucket_value=bucket,
            )
            accum = accum_by_id.get(identity)
            if accum is None:
                accum = _Accum(
                    identity=identity,
                    text=surface,
                    normalized=normalized,
                    tokens=tokens,
                    bucket=bucket,
                    kind="hyphenated_term",
                    detector="hyphenated",
                    reason_codes=[],
                )
                accum_by_id[identity] = accum
            else:
                accum.kind = _merge_kind(accum.kind, "hyphenated_term")
            _add_occurrence(
                accum, prepared=prepared, surface=surface, start=start, end=end
            )


# Stop words at phrase boundaries: common words + pure punctuation tokens.
def _is_phrase_boundary_token(norm: str, common: frozenset[str]) -> bool:
    return norm in common or len(norm) <= 1


def _detect_phrases(
    prepared_records: list[PreparedRecord],
    source_language: str,
    common: frozenset[str],
    min_count: int,
    ngram_max: int,
    accum_by_id: dict[str, _Accum],
) -> None:
    """Statistical 2-N token phrase detection after boundary trimming.

    Phrases never cross record or chapter boundaries. Leading/trailing common
    words are trimmed. Only phrases with length 2..ngram_max are retained.
    """
    if ngram_max < 2:
        return
    for prepared in prepared_records:
        tokens = _tokenize_prepared(prepared)
        n = len(tokens)
        for size in range(2, ngram_max + 1):
            for i in range(0, n - size + 1):
                window = tokens[i : i + size]
                # Trim leading/trailing boundary (common/short) tokens.
                lo, hi = 0, len(window)
                while lo < hi - 1 and _is_phrase_boundary_token(
                    window[lo].normalized, common
                ):
                    lo += 1
                while hi > lo + 1 and _is_phrase_boundary_token(
                    window[hi - 1].normalized, common
                ):
                    hi -= 1
                trimmed = window[lo:hi]
                if len(trimmed) < 2:
                    continue
                # Require every surviving token to be non-boundary.
                if any(
                    _is_phrase_boundary_token(t.normalized, common) for t in trimmed
                ):
                    continue
                surfaces = [t.surface for t in trimmed]
                norms = [t.normalized for t in trimmed]
                joined = " ".join(norms)
                bucket = _phrase_bucket(surfaces)
                kind = "title_candidate" if bucket == "title" else "phrase"
                detector = "title_span" if kind == "title_candidate" else "ngram"
                identity = candidate_id_from_identity(
                    source_language=source_language,
                    normalized=joined,
                    tokens=norms,
                    case_bucket_value=bucket,
                )
                start = trimmed[0].start
                end = trimmed[-1].end
                accum = accum_by_id.get(identity)
                if accum is None:
                    accum = _Accum(
                        identity=identity,
                        text=" ".join(surfaces),
                        normalized=joined,
                        tokens=norms,
                        bucket=bucket,
                        kind=kind,
                        detector=detector,
                        reason_codes=[],
                    )
                    accum_by_id[identity] = accum
                else:
                    accum.kind = _merge_kind(accum.kind, kind)
                _add_occurrence(
                    accum,
                    prepared=prepared,
                    surface=" ".join(surfaces),
                    start=start,
                    end=end,
                )


# --- Style metrics ----------------------------------------------------------

_QUOTE_STYLES = [
    ("double", re.compile(r'[\u201c\u201d"]')),
    ("single", re.compile(r"[\u2018\u2019']")),
    ("guillemet", re.compile(r"[«»\u2039\u203a]")),
    ("german", re.compile(r"[„“]")),
]
_DIALOGUE_RE = re.compile(r'[\u201c\u201d"\u201e„«»]')
_EM_DASH_RE = re.compile(r"\u2014")
_EPUB_EMPHASIS_RE = re.compile(r"<(?:em|strong|i|b)\b", re.IGNORECASE)
_MD_EMPHASIS_RE = re.compile(r"(?<!\*)\*[^*\s][^*]*\*(?!\*)|(?<!_)_[^_\s][^_]*_(?!_)")
_SENTENCE_SPLIT_RE = re.compile(r"[.!?。！？]+[\s]+|$")


def _count_sentences(text: str) -> int:
    if not text.strip():
        return 0
    # Heuristic: count sentence-ending punctuation followed by whitespace.
    return max(1, len(re.findall(r"[.!?。！？]+(?:\s+|$)", text)))


def _build_style_metrics(
    prepared_records: list[PreparedRecord],
    raw_sources: list[str],
    capability_warnings: list[str],
) -> SourceStyleMetrics:
    total = len(prepared_records)
    quote_counts: dict[str, int] = {name: 0 for name, _ in _QUOTE_STYLES}
    em_dash_count = 0
    emphasis_counts: list[int] = []
    dialogue_records = 0
    sentence_total = 0
    word_total = 0
    for prepared, source in zip(prepared_records, raw_sources, strict=True):
        text = prepared.visible_text
        if _DIALOGUE_RE.search(text):
            dialogue_records += 1
        for name, pattern in _QUOTE_STYLES:
            quote_counts[name] += len(pattern.findall(text))
        em_dash_count += len(_EM_DASH_RE.findall(text))
        if prepared.source_markup == "epub-inline-xhtml:v1":
            emphasis_counts.append(len(_EPUB_EMPHASIS_RE.findall(source)))
        else:
            emphasis_counts.append(len(_MD_EMPHASIS_RE.findall(source)))
        sentence_total += _count_sentences(text)
        word_total += len(_WORD_RE.findall(text))
    emphasis_count = sum(emphasis_counts)
    sentence_count = sentence_total or None
    average = (word_total / sentence_total) if sentence_total else None
    return SourceStyleMetrics(
        record_count_with_dialogue=dialogue_records,
        dialogue_record_ratio=(dialogue_records / total) if total else 0.0,
        quote_counts=quote_counts,
        em_dash_count=em_dash_count,
        emphasis_count=emphasis_count,
        sentence_count=sentence_count,
        average_sentence_words=round(average, 3) if average is not None else None,
        capability_warnings=list(capability_warnings),
    )


# --- Scoring + assembly -----------------------------------------------------


def _score_accum(
    accum: _Accum, common: frozenset[str], include_common: bool
) -> float | None:
    if accum.count <= 0:
        return None
    is_common = accum.normalized in common or all(tok in common for tok in accum.tokens)
    if is_common and not include_common and accum.kind in {"word"}:
        # Plain common words are suppressed unless --include-common is set.
        return None
    uncommon_score = _COMMON_PENALTY if is_common else 1.0
    phrase_bonus = _PHRASE_BONUS if accum.kind in _PHRASE_KINDS else 1.0
    score = (
        math.log(1 + accum.count)
        * (1 + math.log(1 + len(accum.chapters)))
        * uncommon_score
        * phrase_bonus
    )
    return score


def _suggested_action(kind: str, already_protected: bool) -> str:
    if kind in {"proper_name", "place_name"} and not already_protected:
        return "review_name_policy"
    if kind in {"hyphenated_term", "invented_term", "title_candidate"}:
        return "review_for_binding_glossary"
    if kind == "phrase":
        return "add_advisory_glossary"
    return "none"


def _finalize_candidate(
    accum: _Accum,
    score: float,
    common: frozenset[str],
) -> SourceCandidate:
    is_common = accum.normalized in common or all(tok in common for tok in accum.tokens)
    uncommon_score = _COMMON_PENALTY if is_common else 1.0
    surface_forms = [
        s for s, _ in sorted(accum.surfaces.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    detectors = sorted(
        {accum.detector, *(["protected_name"] if accum.already_protected else [])}
    )
    reason_codes = sorted(set(accum.reason_codes) | {accum.detector})
    if accum.already_protected and "already_protected" not in reason_codes:
        reason_codes.append("already_protected")
    return SourceCandidate(
        id=accum.identity,
        text=accum.text,
        normalized=accum.normalized,
        surface_forms=surface_forms,
        kind=accum.kind,  # type: ignore[arg-type]
        detectors=detectors,
        count=accum.count,
        record_frequency=len(accum.records),
        chapter_frequency=len(accum.chapters),
        score=round(score, 6),
        uncommon_score=uncommon_score,
        first_record_id=accum.first_record_id,
        examples=accum.examples,
        reason_codes=reason_codes,
        reason=_reason_text(accum),
        already_protected=accum.already_protected,
        suggested_context_action=_suggested_action(accum.kind, accum.already_protected),  # type: ignore[arg-type]
    )


def _reason_text(accum: _Accum) -> str:
    parts: list[str] = []
    if accum.kind == "proper_name":
        parts.append("recurring title-case entity")
    elif accum.kind == "hyphenated_term":
        parts.append("repeated hyphenated term")
    elif accum.kind == "title_candidate":
        parts.append("repeated title-case span")
    elif accum.kind == "phrase":
        parts.append("repeated phrase")
    else:
        parts.append("frequent term")
    if accum.already_protected:
        parts.append("already protected at extraction time")
    return "; ".join(parts)


def _sort_key(candidate: SourceCandidate) -> tuple[float, int, str, str]:
    return (-candidate.score, -candidate.count, candidate.normalized, candidate.id)


# --- Preflight --------------------------------------------------------------


def source_analysis_preflight(
    project: Project,
    *,
    chapter_map: ChapterMap | None,
) -> None:
    """Block analysis when extraction is missing or the chapter audit fails.

    A missing/stale chapter map is a controlled error in a dry run, never a
    silent repair. Blocking EPUB chapter-map/TOC audit findings also block so an
    incomplete chapter map cannot produce a deceptively complete report.
    """
    if not project.chunks():
        raise _err(
            "source_analysis_no_extraction",
            "no extracted source records found; run `booktx extract` before analysis",
        )
    if chapter_map is None:
        raise _err(
            "source_analysis_no_chapter_map",
            "no chapter map found; run `booktx extract` (EPUB) or "
            "`booktx chapters PROJECT_DIR` (markdown) before analysis. "
            "Analysis does not repair a missing chapter map.",
        )
    # Blocking EPUB TOC audit (no-op for markdown projects).
    try:
        from booktx.epub_toc_audit import audit_epub_chapter_map

        audit = audit_epub_chapter_map(project, chapter_map=chapter_map)
    except Exception:  # noqa: BLE001 - audit is advisory for blocking decisions
        return
    blocking = audit.error_findings
    if blocking:
        first = blocking[0]
        raise _err(
            "source_analysis_blocking_chapter_audit",
            "blocking EPUB chapter-map audit finding prevents analysis: "
            f"{first.code}: {first.message}",
        )


# --- Report assembly --------------------------------------------------------


def build_source_analysis(
    project: Project,
    *,
    engine_requested: str = "auto",
    spacy_model: str | None = None,
    min_count: int = 2,
    ngram_max: int = 4,
    top: int = 200,
    include_common: bool = False,
    generated_at: str | None = None,
) -> SourceAnalysisReport:
    """Build the authoritative source-analysis report (no file writes).

    Runs preflight, loads source chunks and a read-only chapter map, prepares
    records, runs the simple engine, merges/scores/orders candidates, computes
    style metrics, and assembles the report with its semantic digest.
    """
    from booktx.chapters import load_chapter_map_only
    from booktx.config import (
        project_source_sha256,
    )
    from booktx.editor_indexes import build_chapter_record_map
    from booktx.io_utils import utc_timestamp
    from booktx.progress import load_source_chunks

    if ngram_max < 1 or ngram_max > 4:
        raise _err(
            "source_analysis_bad_ngram_max",
            "--ngram-max must be between 1 and 4",
        )
    if min_count < 1:
        raise _err(
            "source_analysis_bad_min_count",
            "--min-count must be at least 1",
        )
    if top < 1:
        raise _err(
            "source_analysis_bad_top",
            "--top must be at least 1",
        )

    chapter_map = load_chapter_map_only(project)
    source_analysis_preflight(project, chapter_map=chapter_map)
    assert chapter_map is not None  # preflight guarantees it

    chunks = load_source_chunks(project)
    chapter_by_record = build_chapter_record_map(chunks, chapter_map)
    source_language = (
        chunks[0].source_language if chunks else project.config.source_language
    )
    record_id_scheme = chunks[0].record_id_scheme if chunks else "chunk-local:v1"
    source_sha = project_source_sha256(project)
    extracted_input = extracted_input_sha256(
        chunks,
        source_language=source_language,
        source_sha256=source_sha,
        record_id_scheme=record_id_scheme,
        chapter_map=chapter_map,
        chapter_by_record=chapter_by_record,
    )
    chapter_map_sha = _chapter_map_sha256(chapter_map)

    resolved, capabilities, capability_warnings = resolve_engine(
        engine_requested, spacy_model
    )

    prepared = prepare_records(chunks, chapter_by_record)
    raw_sources = [record.source for chunk in chunks for record in chunk.records]
    common = common_word_set(source_language)

    warnings: list[str] = []
    if not common:
        warnings.append(
            f"no bundled common-word list for source language '{source_language}'; "
            "running with corpus-internal signals only"
        )

    accum_by_id: dict[str, _Accum] = {}
    _detect_tokens(prepared, source_language, min_count, accum_by_id)
    _detect_hyphenated(prepared, source_language, accum_by_id)
    _detect_phrases(
        prepared, source_language, common, min_count, ngram_max, accum_by_id
    )

    # Apply min_count + scoring, then global --top after merging.
    scored: list[tuple[_Accum, float]] = []
    for accum in accum_by_id.values():
        if accum.count < min_count:
            continue
        score = _score_accum(accum, common, include_common)
        if score is None:
            continue
        scored.append((accum, score))
    scored.sort(
        key=lambda pair: (
            -pair[1],
            -pair[0].count,
            pair[0].normalized,
            pair[0].identity,
        )
    )
    scored = scored[:top]

    candidates = [_finalize_candidate(accum, score, common) for accum, score in scored]
    candidates.sort(key=_sort_key)

    style_metrics = _build_style_metrics(prepared, raw_sources, capability_warnings)
    settings = SourceAnalysisSettings(
        engine_requested=engine_requested,  # type: ignore[arg-type]
        engine_resolved=resolved,
        spacy_model=spacy_model,
        spacy_version=None,
        model_version=None,
        min_count=min_count,
        ngram_max=ngram_max,
        top=top,
        include_common=include_common,
    )
    chapter_count = len(chapter_map.chapters)
    report = SourceAnalysisReport(
        identity_ruleset_version=IDENTITY_RULESET_VERSION,
        analysis_ruleset_version=ANALYSIS_RULESET_VERSION,
        source_sha256=source_sha,
        extracted_input_sha256=extracted_input,
        chapter_map_sha256=chapter_map_sha,
        analysis_sha256="",  # filled after digest
        source_language=source_language,
        generated_at=generated_at or utc_timestamp(),
        settings=settings,
        capabilities=capabilities,
        record_count=len(prepared),
        chapter_count=chapter_count,
        candidates=candidates,
        style_metrics=style_metrics,
        warnings=warnings,
    )
    report.analysis_sha256 = compute_analysis_sha256(report)
    return report


# --- Snapshot envelope + validation -----------------------------------------


def build_snapshot(
    report: SourceAnalysisReport, *, profile: str, generated_at: str
) -> SourceAnalysisSnapshot:
    """Wrap a canonical report in a profile-scoped snapshot envelope."""
    return SourceAnalysisSnapshot(
        schema=SNAPSHOT_SCHEMA,
        generated=True,
        canonical=False,
        profile=profile,
        snapshot_generated_at=generated_at,
        source_sha256=report.source_sha256,
        extracted_input_sha256=report.extracted_input_sha256,
        analysis_sha256=report.analysis_sha256,
        report=report,
    )


class SnapshotValidationError(BooktxError):
    """Raised when a snapshot envelope is missing, stale, or tampered."""


@dataclass(slots=True)
class SnapshotRead:
    """A validated snapshot read result for profile-root rendering."""

    snapshot: SourceAnalysisSnapshot
    stale: bool
    hint: str = ""


def validate_snapshot_payload(payload: dict[str, object]) -> SourceAnalysisSnapshot:
    """Validate a parsed snapshot payload and verify its embedded digest."""
    schema = payload.get("schema") or payload.get("schema_name")
    if schema != SNAPSHOT_SCHEMA:
        raise SnapshotValidationError(
            "source_analysis_bad_snapshot_schema",
            f"source-analysis snapshot has unexpected schema: {schema!r}",
        )
    if payload.get("generated") is not True or payload.get("canonical") is not False:
        raise SnapshotValidationError(
            "source_analysis_bad_snapshot_envelope",
            "source-analysis snapshot envelope flags are invalid",
        )
    snapshot = SourceAnalysisSnapshot.model_validate(payload)
    recomputed = compute_analysis_sha256(snapshot.report)
    if recomputed != snapshot.analysis_sha256:
        raise SnapshotValidationError(
            "source_analysis_snapshot_tampered",
            "source-analysis snapshot analysis_sha256 does not match its embedded report",
        )
    return snapshot


def read_snapshot(
    path: object, *, expected_analysis_sha256: str | None = None
) -> SnapshotRead:
    """Read and validate a profile snapshot, reporting staleness safely.

    ``path`` is accepted as ``object`` so callers can pass a profile-root
    relative marker without exposing parent paths here. The hint never contains
    absolute or parent paths.
    """
    from pathlib import Path

    p = Path(path)  # type: ignore[arg-type]
    if not p.is_file():
        raise SnapshotValidationError(
            "source_analysis_snapshot_missing",
            "no source-analysis snapshot exists for this profile; "
            "run `booktx source analyze . --write --sync-profiles` from the project root",
        )
    payload = json.loads(p.read_text("utf-8"))
    snapshot = validate_snapshot_payload(payload)
    stale = False
    hint = ""
    if (
        expected_analysis_sha256
        and snapshot.analysis_sha256 != expected_analysis_sha256
    ):
        stale = True
        hint = (
            "source-analysis snapshot is stale relative to the canonical report; "
            "refresh with `booktx source analyze . --write --sync-profiles`"
        )
    return SnapshotRead(snapshot=snapshot, stale=stale, hint=hint)


def read_canonical_report(project: Project) -> SourceAnalysisReport | None:
    """Read the canonical project-root report, or ``None`` when absent."""
    from booktx.config import source_analysis_path

    path = source_analysis_path(project)
    if not path.is_file():
        return None
    payload = json.loads(path.read_text("utf-8"))
    if (payload.get("schema") or payload.get("schema_name")) != ANALYSIS_SCHEMA:
        raise SnapshotValidationError(
            "source_analysis_bad_report_schema",
            "canonical source-analysis report has unexpected schema",
        )
    report = SourceAnalysisReport.model_validate(payload)
    recomputed = compute_analysis_sha256(report)
    if recomputed != report.analysis_sha256:
        raise SnapshotValidationError(
            "source_analysis_report_tampered",
            "canonical source-analysis report analysis_sha256 does not match its content",
        )
    return report


# --- Markdown rendering -----------------------------------------------------


def _capabilities_label(cap: AnalysisCapabilities) -> str:
    names = [
        name
        for name, on in (
            ("tokenizer", cap.tokenizer),
            ("sentence_boundaries", cap.sentence_boundaries),
            ("lemmatizer", cap.lemmatizer),
            ("pos", cap.pos),
            ("parser", cap.parser),
            ("noun_chunks", cap.noun_chunks),
            ("ner", cap.ner),
        )
        if on
    ]
    return ", ".join(names) if names else "(none)"


def render_report_markdown(report: SourceAnalysisReport) -> str:
    """Render a deterministic Markdown view of the report (JSON authoritative)."""
    lines: list[str] = []
    lines.append("# booktx source analysis")
    lines.append("")
    lines.append(f"Source SHA256: {report.source_sha256}")
    lines.append(f"Extracted input SHA256: {report.extracted_input_sha256}")
    lines.append(f"Chapter map SHA256: {report.chapter_map_sha256}")
    lines.append(f"Analysis SHA256: {report.analysis_sha256}")
    lines.append(f"Identity ruleset: {report.identity_ruleset_version}")
    lines.append(f"Analysis ruleset: {report.analysis_ruleset_version}")
    lines.append(f"Source language: {report.source_language}")
    lines.append(f"Engine: {report.settings.engine_resolved}")
    lines.append(f"Capabilities: {_capabilities_label(report.capabilities)}")
    lines.append(f"Records: {report.record_count}")
    lines.append(f"Chapters: {report.chapter_count}")
    lines.append(f"Candidates: {len(report.candidates)}")
    lines.append("")

    if report.warnings:
        lines.append("## Warnings")
        lines.append("")
        for warning in report.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    top_candidates = report.candidates[:20]
    lines.append("## Highest priority candidates")
    lines.append("")
    if top_candidates:
        lines.append(
            "| ID | Candidate | Kind | Count | Records | Chapters | Suggested action | Reason |"
        )
        lines.append("|---|---|---|---:|---:|---:|---|---|")
        for cand in top_candidates:
            reason = cand.reason or cand.kind
            lines.append(
                f"| {cand.id} | {cand.text} | {cand.kind} | {cand.count} | "
                f"{cand.record_frequency} | {cand.chapter_frequency} | "
                f"{cand.suggested_context_action} | {reason} |"
            )
        lines.append("")
    else:
        lines.append("_No candidates above the current thresholds._")
        lines.append("")

    metrics = report.style_metrics
    lines.append("## Style observations")
    lines.append("")
    lines.append(
        f"- records with dialogue: {metrics.record_count_with_dialogue} "
        f"({metrics.dialogue_record_ratio:.2%})"
    )
    if metrics.quote_counts:
        quote_summary = ", ".join(
            f"{k}={v}" for k, v in metrics.quote_counts.items() if v
        )
        lines.append(f"- quote styles: {quote_summary or 'none'}")
    lines.append(f"- em dashes: {metrics.em_dash_count}")
    lines.append(f"- emphasis spans: {metrics.emphasis_count}")
    if metrics.sentence_count is not None:
        avg = (
            metrics.average_sentence_words
            if metrics.average_sentence_words is not None
            else 0
        )
        lines.append(f"- sentences: {metrics.sentence_count} (avg {avg:.1f} words)")
    if metrics.capability_warnings:
        for warning in metrics.capability_warnings:
            lines.append(f"- capability: {warning}")
    lines.append("")

    if report.candidates:
        lines.append("## Full candidates")
        lines.append("")
        lines.append(
            "| ID | Candidate | Kind | Count | Records | Chapters | Score | Uncommon | Protected | Detectors |"
        )
        lines.append("|---|---|---|---:|---:|---:|---:|---:|:---:|---|")
        for cand in report.candidates:
            detectors = ", ".join(cand.detectors)
            lines.append(
                f"| {cand.id} | {cand.text} | {cand.kind} | {cand.count} | "
                f"{cand.record_frequency} | {cand.chapter_frequency} | {cand.score:.4f} | "
                f"{cand.uncommon_score:.2f} | {'yes' if cand.already_protected else 'no'} | "
                f"{detectors} |"
            )
        lines.append("")

    return "\n".join(lines)
