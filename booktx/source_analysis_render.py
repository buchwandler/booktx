"""Markdown rendering helpers extracted from :mod:`booktx.source_analysis`."""

from __future__ import annotations

from booktx.source_analysis import (
    _BUCKET_ORDER,
    AnalysisCapabilities,
    SourceAnalysisReport,
    SourceCandidate,
    SourceReviewBucket,
)


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


def _markdown_bucket_title(bucket: SourceReviewBucket) -> str:
    return {
        "binding_glossary": "## Review first: binding glossary decisions",
        "name_policy": "## Review names and titles",
        "invented_or_rare": "## Possible invented / rare terms",
        "domain_phrase": "## Maybe review later",
        "maybe": "## Maybe review later",
        "style_signal": "## Style signals",
        "no_action": "## Suppressed / no action candidates",
    }[bucket]


def _markdown_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _candidate_example(candidate: SourceCandidate) -> str:
    if not candidate.examples:
        return ""
    return _markdown_cell(candidate.examples[0].snippet)


def _candidate_command(candidate: SourceCandidate) -> str:
    if candidate.review_bucket == "binding_glossary":
        return (
            f"`booktx context promote-candidate . {candidate.id} --profile PROFILE "
            '--target "TARGET" --require-target --enforce error --write`'
        )
    if candidate.review_bucket in {"name_policy", "invented_or_rare"}:
        return (
            f"`booktx context promote-candidate . {candidate.id} "
            "--profile PROFILE --as-question --write`"
        )
    if candidate.review_bucket == "no_action":
        return (
            f"`booktx source ignore-candidate . {candidate.id} "
            '--reason "ordinary vocabulary" --write`'
        )
    return (
        f"`booktx source review-candidate . {candidate.id} "
        '--reason "checked; no glossary decision needed" --write`'
    )


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

    by_bucket: dict[SourceReviewBucket, list[SourceCandidate]] = {
        bucket: [] for bucket in _BUCKET_ORDER
    }
    for candidate in report.candidates:
        by_bucket[candidate.review_bucket].append(candidate)
    rendered_any = False
    for bucket in _BUCKET_ORDER:
        if bucket == "no_action":
            continue
        bucket_candidates = by_bucket[bucket]
        if not bucket_candidates:
            continue
        rendered_any = True
        lines.append(_markdown_bucket_title(bucket))
        lines.append("")
        lines.append(
            "| ID | Candidate | Type | Count | Chapters | Why | Example | "
            "Suggested command |"
        )
        lines.append("|---|---|---|---:|---:|---|---|---|")
        for cand in bucket_candidates:
            lines.append(
                f"| {cand.id} | {_markdown_cell(cand.text)} | {cand.kind} | "
                f"{cand.count} | {cand.chapter_frequency} | "
                f"{_markdown_cell(cand.reason or cand.kind)} | "
                f"{_candidate_example(cand)} | {_candidate_command(cand)} |"
            )
        lines.append("")
    if not rendered_any:
        lines.append("_No review candidates above the current thresholds._")
        lines.append("")

    lines.append("## Suppressed/no-action summary")
    lines.append("")
    if report.suppressed_counts:
        for reason, count in sorted(
            report.suppressed_counts.items(), key=lambda item: (-item[1], item[0])
        ):
            lines.append(f"- {count} suppressed as `{reason}`")
    else:
        lines.append("- no suppressed candidates recorded")
    if by_bucket["no_action"]:
        lines.append(
            f"- {len(by_bucket['no_action'])} no-action candidate(s) kept in "
            "JSON because `--include-common` was enabled"
        )
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

    return "\n".join(lines)
