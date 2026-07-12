"""Typer commands for source-record inspection (Phase 3 slice 2).

Thin command layer for ``source status / record / chapter``. Each command loads
the runtime/project via the shared CLI helper, delegates data work to
:mod:`booktx.workflows.source`, renders the result, and maps
:class:`booktx.errors.BooktxError` to a non-zero exit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from booktx.runtime import RuntimeContext
    from booktx.source_analysis import SourceAnalysisReport

import typer

from booktx.cli_support import (
    _die,
    _handle_booktx_error,
    _load_runtime_or_exit,
    _project_status_snapshot,
    console,
)
from booktx.errors import BooktxError
from booktx.workflows.source import (
    analyze_source,
    build_source_status_payload,
    collect_chapter_records,
    find_source_record,
    read_source_analysis,
)

source_app = typer.Typer(help="Inspect brokered source records without path leaks.")


def _validate_source_format(output_format: str) -> None:
    if output_format not in {"block", "text", "json"}:
        _die("--format must be block, text, or json")


@source_app.command(name="status")
def source_status_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory or profile root."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """Show a safe summary of extracted source state."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=False)
    proj = runtime.project
    payload = build_source_status_payload(proj)
    if as_json:
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return
    console.print(f"source: {payload['source']}")
    console.print(f"format: {payload['format']}")
    console.print(f"source language: {payload['source_language']}")
    console.print(f"records: {payload['records']}")
    console.print(f"chunks: {payload['chunks']}")
    console.print(f"chapters: {payload['chapters']}")


@source_app.command(name="record")
def source_record_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory or profile root."),
    record_ref: str = typer.Argument(..., help="Record id or record ref."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    output_format: str = typer.Option(
        "block",
        "--format",
        help="Output format: block, text, or json.",
    ),
) -> None:
    """Print one source record without exposing chunk paths."""
    _validate_source_format(output_format)
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=False)
    proj = runtime.project
    try:
        record = find_source_record(proj, record_ref)
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    payload = {"id": record.record_id, "source": record.source}
    if output_format == "json":
        console.print_json(json.dumps(payload, ensure_ascii=False))
    elif output_format == "text":
        console.print(f"{record.record_id}\t{record.source}")
    else:
        console.print(f">>> {record.record_id}")
        console.print(record.source)


@source_app.command(name="chapter")
def source_chapter_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory or profile root."),
    chapter_id: str = typer.Argument(..., help="Chapter id, e.g. 0001."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    output_format: str = typer.Option(
        "block",
        "--format",
        help="Output format: block, text, or json.",
    ),
) -> None:
    """Print all source records for one chapter without exposing chunk paths."""
    _validate_source_format(output_format)
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=False)
    proj = runtime.project
    bundle = _project_status_snapshot(proj)
    try:
        result = collect_chapter_records(bundle, chapter_id)
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    records = result.records
    if output_format == "json":
        console.print_json(
            json.dumps(
                {
                    "chapter_id": result.chapter_id,
                    "title": result.title,
                    "records": records,
                },
                ensure_ascii=False,
            )
        )
        return
    for item in records:
        if output_format == "text":
            console.print(f"{item['id']}\t{item['source']}")
        else:
            console.print(f">>> {item['id']}")
            console.print(item["source"])
            if item != records[-1]:
                console.print()


def _validate_analysis_format(output_format: str) -> None:
    if output_format not in {"human", "json"}:
        _die("--format must be human or json")


def _print_analysis_human(
    report: SourceAnalysisReport,
    *,
    stale: bool = False,
    hint: str = "",
) -> None:
    caps = report.capabilities
    cap_names = [
        name
        for name, on in (
            ("tokenizer", caps.tokenizer),
            ("sentence_boundaries", caps.sentence_boundaries),
            ("lemmatizer", caps.lemmatizer),
            ("pos", caps.pos),
            ("parser", caps.parser),
            ("noun_chunks", caps.noun_chunks),
            ("ner", caps.ner),
        )
        if on
    ]
    console.print(f"source language: {report.source_language}")
    console.print(f"engine: {report.settings.engine_resolved}")
    console.print(f"capabilities: {', '.join(cap_names) or '(none)'}")
    console.print(f"records: {report.record_count}")
    console.print(f"chapters: {report.chapter_count}")
    console.print(f"candidates: {len(report.candidates)}")
    console.print(f"analysis sha256: {report.analysis_sha256}")
    if stale:
        console.print(f"[yellow]stale:[/yellow] {hint}")
    if report.warnings:
        console.print("warnings:")
        for warning in report.warnings:
            console.print(f"  - {warning}")
    if report.candidates:
        console.print("top candidates:")
        for cand in report.candidates[:10]:
            console.print(
                f"  {cand.id} {cand.text!r} bucket={cand.review_bucket} "
                f"kind={cand.kind} count={cand.count} "
                f"chapters={cand.chapter_frequency} "
                f"action={cand.suggested_context_action} risk={cand.risk_score:.2f}"
            )


@source_app.command(name="analyze")
def source_analyze_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory (project root)."),
    engine: str = typer.Option(
        "auto", "--engine", help="Analysis engine: auto, spacy, or simple."
    ),
    spacy_model: str | None = typer.Option(
        None, "--spacy-model", help="Explicit spaCy model."
    ),
    top: int = typer.Option(200, "--top", help="Global candidate limit after merging."),
    min_count: int = typer.Option(2, "--min-count", help="Minimum corpus count."),
    ngram_max: int = typer.Option(
        4, "--ngram-max", help="Maximum phrase length (1..4)."
    ),
    include_common: bool = typer.Option(
        False, "--include-common", help="Include common words as candidates."
    ),
    output_format: str = typer.Option(
        "human", "--format", help="Output format: human or json."
    ),
    write: bool = typer.Option(
        False, "--write", help="Write canonical JSON and Markdown."
    ),
    sync_profiles: bool = typer.Option(
        False,
        "--sync-profiles",
        help="Refresh all profile snapshots (requires --write).",
    ),
) -> None:
    """Analyze extracted source evidence (project root only; dry run by default)."""
    if engine not in {"auto", "spacy", "simple"}:
        _die("--engine must be auto, spacy, or simple")
    _validate_analysis_format(output_format)
    runtime = _load_runtime_or_exit(project_dir, require_profile=False)
    # Analyze/write/sync are collaborative project-root workflows.
    if runtime.mode.isolated_output:
        _die("source analyze is a project-root command; run it from the project root.")
    proj = runtime.project
    try:
        result = analyze_source(
            proj,
            engine_requested=engine,
            spacy_model=spacy_model,
            min_count=min_count,
            ngram_max=ngram_max,
            top=top,
            include_common=include_common,
            write=write,
            sync_profiles=sync_profiles,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    report = result.report
    if output_format == "json":
        payload = report.model_dump(by_alias=True, mode="json")
        if write:
            payload["_written"] = {
                "canonical_json": result.canonical_json_written,
                "canonical_md": result.canonical_md_written,
                "synced_profiles": result.refreshed_profiles,
            }
        console.print_json(json.dumps(payload, ensure_ascii=False))
    else:
        _print_analysis_human(report)
        if write:
            console.print(f"canonical json written: {result.canonical_json_written}")
            console.print(f"canonical markdown written: {result.canonical_md_written}")
            if result.canonical_md_error:
                console.print(f"[red]markdown error:[/red] {result.canonical_md_error}")
            if sync_profiles:
                console.print(f"snapshots refreshed: {len(result.refreshed_profiles)}")
                for sync in result.synced:
                    mark = "ok" if sync.json_written and not sync.error else "FAIL"
                    console.print(f"  [{mark}] {sync.profile}")
    if write and sync_profiles and result.failed_syncs:
        failed = ", ".join(s.profile for s in result.failed_syncs)
        _die(
            f"source-analysis snapshot sync failed for profile(s): {failed}; "
            f"{len(result.refreshed_profiles)} snapshot(s) refreshed."
        )


@source_app.command(name="analysis")
def source_analysis_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory or profile root."),
    output_format: str = typer.Option(
        "human", "--format", help="Output format: human or json."
    ),
) -> None:
    """Read source-analysis evidence (canonical or current profile snapshot)."""
    _validate_analysis_format(output_format)
    runtime = _load_runtime_or_exit(project_dir, require_profile=False)
    proj = runtime.project
    try:
        read = read_source_analysis(proj, isolated=runtime.mode.isolated_output)
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    if read.missing:
        _die(read.hint or "no source-analysis evidence found")
        return
    if read.report is not None:
        report = read.report
    else:
        assert read.snapshot is not None
        report = read.snapshot.report
    if output_format == "json":
        console.print_json(
            json.dumps(
                report.model_dump(by_alias=True, mode="json"), ensure_ascii=False
            )
        )
    else:
        _print_analysis_human(report, stale=read.stale, hint=read.hint)


def _candidate_disposition_command(
    project_dir: Path,
    candidate_id: str,
    *,
    disposition: Literal["ignored", "reviewed"],
    reason: str,
    decided_by: str,
    write: bool,
) -> None:
    runtime = _load_runtime_or_exit(project_dir, require_profile=False)
    if runtime.mode.isolated_output:
        _die(f"source {disposition} is a project-root command")
    from booktx.source_analysis import read_canonical_report
    from booktx.source_analysis_context import set_disposition

    report = read_canonical_report(runtime.project)
    if report is None:
        _die("no canonical source analysis; run `booktx source analyze . --write`")
    assert report is not None
    try:
        decision, changed = set_disposition(
            runtime.project,
            report,
            candidate_id=candidate_id,
            disposition=disposition,
            reason=reason,
            decided_by=decided_by,
            write=write,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(
        f"{'would write' if not write else 'wrote'} {decision.disposition} "
        f"decision for {decision.candidate_id}; changed={str(changed).lower()}"
    )


@source_app.command(name="ignore-candidate")
def source_ignore_candidate_cmd(
    project_dir: Path = typer.Argument(..., help="Project root."),
    candidate_id: str = typer.Argument(..., help="Stable candidate id."),
    reason: str = typer.Option("", "--reason", help="Review rationale."),
    decided_by: str = typer.Option("cli", "--decided-by", help="Decision provenance."),
    write: bool = typer.Option(False, "--write", help="Persist the decision."),
) -> None:
    """Ignore one source candidate (dry run by default)."""
    _candidate_disposition_command(
        project_dir,
        candidate_id,
        disposition="ignored",
        reason=reason,
        decided_by=decided_by,
        write=write,
    )


@source_app.command(name="review-candidate")
def source_review_candidate_cmd(
    project_dir: Path = typer.Argument(..., help="Project root."),
    candidate_id: str = typer.Argument(..., help="Stable candidate id."),
    reason: str = typer.Option("", "--reason", help="Review rationale."),
    decided_by: str = typer.Option("cli", "--decided-by", help="Decision provenance."),
    write: bool = typer.Option(False, "--write", help="Persist the decision."),
) -> None:
    """Mark one source candidate reviewed (dry run by default)."""
    _candidate_disposition_command(
        project_dir,
        candidate_id,
        disposition="reviewed",
        reason=reason,
        decided_by=decided_by,
        write=write,
    )


def _load_project_root_runtime(project_dir: Path, command_name: str) -> RuntimeContext:
    runtime = _load_runtime_or_exit(project_dir, require_profile=False)
    if runtime.mode.isolated_output:
        _die(f"source {command_name} is a project-root command")
    return runtime


@source_app.command(name="interview-plan")
def source_interview_plan_cmd(
    project_dir: Path = typer.Argument(..., help="Project root."),
    profile: str = typer.Option(..., "--profile", help="Target profile."),
    write: bool = typer.Option(False, "--write", help="Write source-interview.json."),
) -> None:
    """Create or preview a profile-local source-policy interview ledger."""
    runtime = _load_project_root_runtime(project_dir, "interview-plan")
    from booktx.workflows.source_interview import interview_plan

    try:
        result = interview_plan(runtime.project, profile=profile, write=write)
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(
        f"{'wrote' if result.written else 'planned'} "
        f"{len(result.ledger.items)} interview item(s) for {profile}"
    )
    console.print(f"ledger: {result.path}")
    if not write:
        console.print("Dry run. Re-run with --write to apply.")


@source_app.command(name="interview-status")
def source_interview_status_cmd(
    project_dir: Path = typer.Argument(..., help="Project root."),
    profile: str = typer.Option(..., "--profile", help="Target profile."),
    fail_if_open: bool = typer.Option(
        False, "--fail-if-open", help="Exit non-zero when open items remain."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """Report profile source-policy interview progress."""
    runtime = _load_project_root_runtime(project_dir, "interview-status")
    from booktx.workflows.source_interview import interview_status

    try:
        payload = interview_status(
            runtime.project, profile=profile, fail_if_open=fail_if_open
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    if as_json:
        console.print_json(json.dumps(payload, ensure_ascii=False))
    else:
        console.print(f"profile: {payload['profile']}")
        console.print(f"missing: {str(payload['missing']).lower()}")
        console.print(f"stale: {str(payload['stale']).lower()}")
        counts = payload["counts"]
        assert isinstance(counts, dict)
        console.print(" ".join(f"{k}={v}" for k, v in counts.items()))
        console.print(f"open: {payload['open']}")
    if payload.get("fail"):
        _die("source interview has open items")


@source_app.command(name="interview-next")
def source_interview_next_cmd(
    project_dir: Path = typer.Argument(..., help="Project root."),
    profile: str = typer.Option(..., "--profile", help="Target profile."),
    output_format: str = typer.Option("markdown", "--format", help="markdown or json."),
) -> None:
    """Print the next source-policy interview question card."""
    if output_format not in {"markdown", "json"}:
        _die("--format must be markdown or json")
    runtime = _load_project_root_runtime(project_dir, "interview-next")
    from booktx.workflows.source_interview import interview_next

    try:
        ledger, item, card = interview_next(runtime.project, profile=profile)
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    if output_format == "json":
        console.print_json(
            json.dumps(
                {
                    "profile": ledger.profile,
                    "item": item.model_dump(mode="json"),
                    "card": card,
                },
                ensure_ascii=False,
            )
        )
    else:
        console.print(card)


@source_app.command(name="interview-answer")
def source_interview_answer_cmd(
    project_dir: Path = typer.Argument(..., help="Project root."),
    candidate_id: str = typer.Argument(..., help="Candidate id."),
    profile: str = typer.Option(..., "--profile", help="Target profile."),
    target: str | None = typer.Option(None, "--target", help="Approved target."),
    forbid: list[str] | None = typer.Option(
        None, "--forbid", help="Forbidden target. Repeatable."
    ),
    rationale: str = typer.Option("", "--rationale", help="Decision rationale."),
    storage: str = typer.Option(
        "context", "--storage", help="context, termbase, or both."
    ),
    write: bool = typer.Option(
        False, "--write", help="Persist approved policy and ledger status."
    ),
) -> None:
    """Persist an approved source-policy interview answer."""
    if storage not in {"context", "termbase", "both"}:
        _die("--storage must be context, termbase, or both")
    runtime = _load_project_root_runtime(project_dir, "interview-answer")
    from booktx.workflows.source_interview import interview_answer

    try:
        item = interview_answer(
            runtime.project,
            profile=profile,
            candidate_id=candidate_id,
            target=target,
            forbid=forbid or [],
            rationale=rationale,
            storage=cast(Literal["context", "termbase", "both"], storage),
            write=write,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(
        f"{'stored' if write else 'would store'} {item.candidate_id} "
        f"status={item.status}"
    )


@source_app.command(name="interview-skip")
def source_interview_skip_cmd(
    project_dir: Path = typer.Argument(..., help="Project root."),
    candidate_id: str = typer.Argument(..., help="Candidate id."),
    profile: str = typer.Option(..., "--profile", help="Target profile."),
    disposition: str = typer.Option(
        "ignored", "--disposition", help="ignored, reviewed, or deferred."
    ),
    reason: str = typer.Option("", "--reason", help="Decision rationale."),
    write: bool = typer.Option(False, "--write", help="Persist skip decision."),
) -> None:
    """Skip or defer one source-policy interview item."""
    if disposition not in {"ignored", "reviewed", "deferred"}:
        _die("--disposition must be ignored, reviewed, or deferred")
    runtime = _load_project_root_runtime(project_dir, "interview-skip")
    from booktx.workflows.source_interview import interview_skip

    try:
        item = interview_skip(
            runtime.project,
            profile=profile,
            candidate_id=candidate_id,
            disposition=cast(Literal["ignored", "reviewed", "deferred"], disposition),
            reason=reason,
            write=write,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(
        f"{'updated' if write else 'would update'} {item.candidate_id} "
        f"status={item.status}"
    )
