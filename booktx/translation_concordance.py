"""Task-scoped, read-only translation concordance evidence.

The concordance deliberately resolves effective candidates through the store
abstraction and scans the canonical source order once per invocation.  It is
historical evidence, not glossary or context policy.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from booktx.config import load_translation_store
from booktx.translation_store import effective_target_candidate

if TYPE_CHECKING:
    from booktx.config import Project
    from booktx.models import TranslationTask
    from booktx.status import StatusBundle

__all__ = [
    "ConcordanceQuery",
    "ConcordanceHit",
    "ConcordanceGroup",
    "TranslationConcordanceReport",
    "build_concordance",
    "render_concordance_human",
    "render_concordance_markdown",
]

MAX_AUTO_QUERIES = 20
DEFAULT_MAX_EXAMPLES = 3
MAX_EXAMPLES = 20


class ConcordanceQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: str
    side: Literal["source", "target"]
    text: str
    mode: Literal["literal", "regex"] = "literal"
    origin: Literal[
        "explicit", "glossary", "termbase", "source_analysis", "heuristic"
    ] = "explicit"


class ConcordanceHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str
    chapter_id: str = ""
    chapter_title: str = ""
    source: str
    target: str
    effective_ref: str = ""
    matched_text: str = ""
    match_spans: list[tuple[int, int]] = Field(default_factory=list)
    precedes_task: bool = True
    query_origin: str


class ConcordanceGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: str
    side: Literal["source", "target"]
    query: str
    mode: Literal["literal", "regex"]
    origin: str
    total_matches: int = 0
    rendered_examples: int = 0
    truncated: bool = False
    examples: list[ConcordanceHit] = Field(default_factory=list)


class TranslationConcordanceReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "booktx.translation-concordance.v1"
    profile: str = ""
    task_id: str | None = None
    scope: Literal["before-task", "all"]
    records_scanned: int = 0
    queries: list[ConcordanceGroup] = Field(default_factory=list)
    source_sha256: str = ""
    store_sha256: str = ""
    policy_notice: str = (
        "Binding policy wins over concordance evidence. "
        "Concordance evidence is read-only observed usage and is not approval."
    )


def _fingerprint(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _query_pattern(query: ConcordanceQuery) -> re.Pattern[str]:
    if query.mode == "regex":
        return re.compile(query.text, re.IGNORECASE)
    return re.compile(re.escape(query.text), re.IGNORECASE)


def _auto_queries(
    task: TranslationTask, bundle: StatusBundle
) -> list[ConcordanceQuery]:
    """Extract a small deterministic set of continuity cues from task sources."""
    candidates: set[str] = set()
    for item in task.records:
        text = item.source
        for match in re.finditer(
            r"\b[A-ZÄÖÜ][\wÄÖÜäöüß-]*(?:\s+[A-ZÄÖÜ][\wÄÖÜäöüß-]*){0,3}", text
        ):
            cue = match.group(0).strip('.,;:!?()[]{}"')
            if len(cue) >= 4:
                candidates.add(cue)
        for match in re.finditer(r"\b[\wÄÖÜäöüß]+-[\wÄÖÜäöüß-]+\b", text):
            candidates.add(match.group(0))

    # Prefer longer phrases and avoid generic sentence-start words.
    stop = {"The", "This", "That", "When", "And", "But", "With", "Then", "There"}
    ordered = sorted(
        (cue for cue in candidates if cue.split()[0] not in stop),
        key=lambda cue: (-len(cue.split()), -len(cue), cue.casefold()),
    )
    source_ids = set(bundle.index.source_by_id)
    del source_ids  # Keeps the helper's dependency explicit for callers/tests.
    return [
        ConcordanceQuery(
            query_id=f"q-{idx:04d}", side="source", text=cue, origin="heuristic"
        )
        for idx, cue in enumerate(ordered[:MAX_AUTO_QUERIES], 1)
    ]


def build_concordance(
    project: Project,
    bundle: StatusBundle,
    *,
    task: TranslationTask | None = None,
    source_queries: Sequence[str] = (),
    target_queries: Sequence[str] = (),
    source_regexes: Sequence[str] = (),
    target_regexes: Sequence[str] = (),
    auto: bool = False,
    scope: Literal["before-task", "all"] = "before-task",
    max_examples: int = DEFAULT_MAX_EXAMPLES,
) -> TranslationConcordanceReport:
    """Build grouped concordance evidence with one ordered record traversal."""
    if max_examples < 1 or max_examples > MAX_EXAMPLES:
        raise ValueError(f"max_examples must be between 1 and {MAX_EXAMPLES}")
    if scope == "before-task" and task is None:
        raise ValueError("before-task scope requires a task")

    queries: list[ConcordanceQuery] = []
    number = 1
    for text in source_queries:
        if text.strip():
            queries.append(
                ConcordanceQuery(
                    query_id=f"q-{number:04d}",
                    side="source",
                    text=text,
                    origin="explicit",
                )
            )
            number += 1
    for text in target_queries:
        if text.strip():
            queries.append(
                ConcordanceQuery(
                    query_id=f"q-{number:04d}",
                    side="target",
                    text=text,
                    origin="explicit",
                )
            )
            number += 1
    for text in source_regexes:
        queries.append(
            ConcordanceQuery(
                query_id=f"q-{number:04d}",
                side="source",
                text=text,
                mode="regex",
                origin="explicit",
            )
        )
        number += 1
    for text in target_regexes:
        queries.append(
            ConcordanceQuery(
                query_id=f"q-{number:04d}",
                side="target",
                text=text,
                mode="regex",
                origin="explicit",
            )
        )
        number += 1
    if auto:
        auto_items = _auto_queries(task, bundle) if task is not None else []
        queries.extend(auto_items)
    if not queries and not auto:
        raise ValueError("provide at least one query or enable auto")

    patterns = [_query_pattern(query) for query in queries]
    groups = [
        ConcordanceGroup(
            query_id=query.query_id,
            side=query.side,
            query=query.text,
            mode=query.mode,
            origin=query.origin,
        )
        for query in queries
    ]
    store = load_translation_store(project)
    store_records = store.records
    ordered_ids = [
        record_id
        for chapter in bundle.index.record_ids_by_chapter.values()
        for record_id in chapter
    ]
    # Chapter lists are ordered but can overlap in custom maps; preserve the
    # first occurrence.
    ordered_ids = list(dict.fromkeys(ordered_ids))
    task_ids = {item.id for item in task.records} if task is not None else set()
    cutoff = len(ordered_ids)
    if task is not None and task_ids:
        positions = [
            idx for idx, record_id in enumerate(ordered_ids) if record_id in task_ids
        ]
        cutoff = min(positions) if positions else len(ordered_ids)

    for index, record_id in enumerate(ordered_ids):
        if record_id in task_ids:
            continue
        if scope == "before-task" and index >= cutoff:
            continue
        stored = store_records.get(record_id)
        source_view = bundle.index.source_by_id.get(record_id)
        if stored is None or source_view is None:
            continue
        effective = effective_target_candidate(stored)
        if effective is None or not effective.target:
            continue
        source_text = source_view.source
        target_text = effective.target
        for group, query, pattern in zip(groups, queries, patterns, strict=True):
            haystack = source_text if query.side == "source" else target_text
            matches = list(pattern.finditer(haystack))
            if not matches:
                continue
            group.total_matches += 1
            if len(group.examples) >= max_examples:
                group.truncated = True
                continue
            group.examples.append(
                ConcordanceHit(
                    record_id=record_id,
                    chapter_id=bundle.index.record_to_chapter.get(record_id, ""),
                    chapter_title=(
                        chapter.title
                        if (
                            chapter := bundle.index.chapters_by_id.get(
                                bundle.index.record_to_chapter.get(record_id, "")
                            )
                        )
                        is not None
                        else ""
                    ),
                    source=source_text,
                    target=target_text,
                    effective_ref=(
                        getattr(effective, "review_ref", None)
                        or getattr(effective, "version_ref", None)
                        or ""
                    ),
                    matched_text=matches[0].group(0),
                    match_spans=[match.span() for match in matches],
                    precedes_task=index < cutoff,
                    query_origin=query.origin,
                )
            )
            group.rendered_examples = len(group.examples)

    source_payload = [
        (record_id, bundle.index.source_by_id[record_id].source)
        for record_id in ordered_ids
        if record_id in bundle.index.source_by_id
    ]
    store_payload = store.model_dump(mode="json")
    rendered_groups = [
        group for group in groups if group.total_matches or group.origin != "heuristic"
    ]
    return TranslationConcordanceReport(
        profile=project.profile or "",
        task_id=task.task_id if task else None,
        scope=scope,
        records_scanned=sum(
            1
            for index, record_id in enumerate(ordered_ids)
            if record_id not in task_ids and (scope == "all" or index < cutoff)
        ),
        queries=rendered_groups,
        source_sha256=_fingerprint(source_payload),
        store_sha256=_fingerprint(store_payload),
    )


def render_concordance_human(report: TranslationConcordanceReport) -> str:
    lines = [
        "Translation concordance",
        f"profile: {report.profile}",
        f"task: {report.task_id or 'none'}",
        f"scope: {report.scope}",
        f"records scanned: {report.records_scanned}",
        f"queries: {len(report.queries)}",
        "",
        report.policy_notice,
    ]
    for group in report.queries:
        lines.extend(
            [
                "",
                f"{group.side.upper()} {group.query!r}",
                f"matches: {group.total_matches}",
                f"examples: {group.rendered_examples}",
            ]
        )
        for hit in group.examples:
            lines.extend(
                [
                    f"  {hit.record_id} [{hit.effective_ref}]",
                    f"    source: {hit.source}",
                    f"    target: {hit.target}",
                ]
            )
        if group.truncated:
            lines.append("  (examples truncated)")
    return "\n".join(lines) + "\n"


def render_concordance_markdown(report: TranslationConcordanceReport) -> str:
    lines = [
        "# Translation Concordance",
        "",
        f"- Profile: `{report.profile}`",
        f"- Task: `{report.task_id or 'none'}`",
        f"- Scope: `{report.scope}`",
        f"- Records scanned: `{report.records_scanned}`",
        f"- Source fingerprint: `{report.source_sha256}`",
        f"- Store fingerprint: `{report.store_sha256}`",
        "",
        "> Binding policy wins over concordance evidence. Concordance evidence "
        "is read-only observed usage and is not approval.",
    ]
    for group in report.queries:
        lines.extend(
            [
                "",
                f"## {group.side.upper()} `{group.query}`",
                "",
                f"- Matches: {group.total_matches}",
                f"- Examples: {group.rendered_examples}",
                f"- Origin: `{group.origin}`",
            ]
        )
        for hit in group.examples:
            lines.extend(
                [
                    "",
                    f"### `{hit.record_id}` ({hit.effective_ref})",
                    "",
                    f"- Source: {hit.source}",
                    f"- Target: {hit.target}",
                ]
            )
    return "\n".join(lines) + "\n"
