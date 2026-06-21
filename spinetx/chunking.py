"""Sentence segmentation and chunk packing.

The extractor hands chunking a list of *protected prose spans* (each already
had names and inline tags replaced by placeholder tokens). chunking:

1. Segments each span into sentences with :mod:`pysbd`.
2. Assigns each resulting sentence a stable record id.
3. Packs records into :class:`~spinetx.models.Chunk` objects of at most
   ``chunk_size`` records, numbering chunks from 1.

Record ids are ``NNNN-NNNNNN``: the 4-digit chunk id, a dash, and a 1-based
6-digit index inside that chunk. Chunk ids are zero-padded 4-digit strings.

The goal stated in the spec is **one source sentence to one translated
sentence** — chunking never merges or splits beyond what pysbd returns.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import pysbd

from spinetx.models import Chunk, Placeholder, Record

__all__ = [
    "ProseSpan",
    "segment_spans",
    "pack_chunks",
    "spans_to_chunks",
]

# pysbd emits some SyntaxWarnings on import under Python 3.12+; silence them at
# the boundary so they never leak into spinetx output.
warnings.filterwarnings(
    "ignore",
    message="invalid escape sequence",
    category=SyntaxWarning,
    module=r"pysbd\..*",
)


@dataclass(slots=True)
class ProseSpan:
    """A protected prose span produced by a format extractor.

    ``text`` is the prose with names/tags already replaced by placeholder
    tokens. ``placeholders`` lists every placeholder that appears anywhere in
    ``text``; they are attached to *each* record derived from this span so the
    validator and build steps can restore them. ``protected_terms`` is the
    subset of names relevant to this span (for the ``protected_terms`` field).
    """

    text: str
    placeholders: list[Placeholder]
    protected_terms: list[str]


def _segmenter(language: str) -> pysbd.Segmenter:
    # pysbd uses ISO-639-1 codes; spinetx config codes are BCP-47-ish, so we
    # pass the primary subtag and let pysbd fall back if needed.
    code = language.split("-")[0].lower() or "en"
    try:
        return pysbd.Segmenter(language=code)
    except Exception:  # noqa: BLE001 - unknown language -> English fallback
        return pysbd.Segmenter(language="en")


def segment_spans(spans: list[ProseSpan], *, language: str = "en") -> list[Record]:
    """Segment every span into one :class:`Record` per sentence.

    Empty/whitespace-only sentences are dropped so a span never yields a blank
    record. Each record inherits the full placeholder + protected-term list of
    its parent span (the contract lists them per record).
    """
    segmenter = _segmenter(language)
    records: list[Record] = []
    counter = 0
    for span in spans:
        if not span.text or not span.text.strip():
            continue
        sentences = segmenter.segment(span.text)
        for sentence in sentences:
            cleaned = sentence.strip()
            if not cleaned:
                continue
            counter += 1
            records.append(
                Record(
                    id=f"{counter:06d}",  # provisional; repack reassigns
                    source=cleaned,
                    protected_terms=list(span.protected_terms),
                    placeholders=list(span.placeholders),
                )
            )
    return records


def pack_chunks(
    records: list[Record],
    *,
    source_language: str,
    target_language: str,
    chunk_size: int = 50,
) -> list[Chunk]:
    """Pack records into chunks of at most ``chunk_size`` and assign final ids.

    Final record ids are ``NNNN-NNNNNN`` (chunk id + 1-based intra-chunk index).
    """
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    chunks: list[Chunk] = []
    for chunk_idx, start in enumerate(range(0, len(records), chunk_size), start=1):
        chunk_id = f"{chunk_idx:04d}"
        bucket = records[start : start + chunk_size]
        renumbered: list[Record] = []
        for intra, rec in enumerate(bucket, start=1):
            renumbered.append(rec.model_copy(update={"id": f"{chunk_id}-{intra:06d}"}))
        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                source_language=source_language,
                target_language=target_language,
                records=renumbered,
            )
        )
    return chunks


def spans_to_chunks(
    spans: list[ProseSpan],
    *,
    source_language: str,
    target_language: str,
    chunk_size: int = 50,
) -> list[Chunk]:
    """Convenience: segment spans then pack into chunks."""
    records = segment_spans(spans, language=source_language)
    return pack_chunks(
        records,
        source_language=source_language,
        target_language=target_language,
        chunk_size=chunk_size,
    )
