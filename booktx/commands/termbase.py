"""Typer commands for the translation preference dictionary."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from booktx.cli_support import _handle_booktx_error, console
from booktx.errors import BooktxError
from booktx.workflows.termbase import (
    termbase_add_workflow,
    termbase_audit_workflow,
    termbase_export_workflow,
    termbase_import_workflow,
    termbase_promote_candidate_workflow,
    termbase_promote_context_workflow,
    termbase_scan_source_workflow,
    termbase_status_workflow,
    termbase_validate_entry_workflow,
    termbase_write_review_workflow,
)

termbase_app = typer.Typer(help="Manage the translation preference dictionary.")


@termbase_app.command(name="status")
def termbase_status_cmd(
    project_dir: Path | None = typer.Argument(
        None, help="Optional project directory or profile root."
    ),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    scope: str | None = typer.Option(
        None, "--scope", help="global|project|profile|effective."
    ),
    language: str | None = typer.Option(
        None, "--language", help="Language shard key, e.g. de or de-DE."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    try:
        payload = termbase_status_workflow(
            project_dir,
            profile=profile,
            scope=scope,
            language=language,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    if as_json:
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return
    console.print(f"scope: {payload['scope']}")
    console.print(f"language keys: {', '.join(payload['language_keys'])}")
    console.print(f"target locale: {payload['target_locale']}")
    console.print(f"active entries: {payload['active_entries']}")
    console.print(f"disabled entries: {payload['disabled_entries']}")
    console.print(
        "conflicts: "
        + (", ".join(payload["conflicts"]) if payload["conflicts"] else "none")
    )
    for layer in payload["layers"]:
        state = "present" if layer["exists"] else "missing"
        console.print(
            f"{layer['scope']} {layer['language_key']}: {state} {layer['path']} "
            f"({layer['entry_count']} entries)",
            soft_wrap=True,
            markup=False,
        )


@termbase_app.command(name="add")
def termbase_add_cmd(
    project_dir: Path | None = typer.Argument(
        None, help="Optional project directory or profile root."
    ),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    scope: str | None = typer.Option(None, "--scope", help="global|project|profile."),
    language: str | None = typer.Option(
        None, "--language", help="Destination language shard key."
    ),
    entry_file: Path | None = typer.Option(
        None, "--file", help="Structured termbase entry JSON file."
    ),
    entry_id: str | None = typer.Option(None, "--id", help="Stable termbase entry id."),
    kind: str = typer.Option("flat_term", "--kind", help="Termbase entry kind."),
    source: str = typer.Option(..., "--source", help="Primary source cue."),
    source_variant: list[str] = typer.Option(
        [], "--source-variant", help="Repeatable source variants."
    ),
    source_regex: str | None = typer.Option(
        None, "--source-regex", help="Optional source regex cue."
    ),
    case_sensitive: bool = typer.Option(
        False, "--case-sensitive", help="Match the source term case-sensitively."
    ),
    preferred: list[str] = typer.Option(
        [], "--preferred", help="Repeatable preferred targets."
    ),
    allowed: list[str] = typer.Option(
        [], "--allowed", help="Repeatable allowed targets."
    ),
    forbid: list[str] = typer.Option(
        [], "--forbid", help="Repeatable forbidden targets."
    ),
    forbid_regex: list[str] = typer.Option(
        [], "--forbid-regex", help="Repeatable forbidden target regexes."
    ),
    preferred_policy: str = typer.Option(
        "off", "--preferred-policy", help="off|advisory|required."
    ),
    sense: str = typer.Option("", "--sense", help="Short sense summary."),
    rationale: str = typer.Option("", "--rationale", help="Entry rationale."),
    severity: str = typer.Option("warn", "--severity", help="info|warn|error."),
    approve: bool = typer.Option(
        False, "--approve", help="Create the entry as approved."
    ),
) -> None:
    try:
        payload = termbase_add_workflow(
            project_dir,
            profile=profile,
            scope=scope,
            language=language,
            entry_file=entry_file,
            entry_id=entry_id,
            kind=kind,
            source=source,
            source_variants=source_variant,
            source_regex=source_regex,
            case_sensitive=case_sensitive,
            preferred=preferred,
            allowed=allowed,
            forbidden=forbid,
            forbidden_regex=forbid_regex,
            preferred_policy=preferred_policy,
            sense=sense,
            rationale=rationale,
            severity=severity,
            approve=approve,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(
        f"termbase entry {payload['entry_id']} -> {payload['status']} "
        f"({payload['scope']} {payload['language_key']})"
    )
    console.print(payload["path"], soft_wrap=True, markup=False)


@termbase_app.command(name="validate-entry")
def termbase_validate_entry_cmd(
    input_path: Path = typer.Option(
        ..., "--input", help="Structured termbase entry JSON file."
    ),
) -> None:
    try:
        payload = termbase_validate_entry_workflow(input_path)
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print("valid termbase entry")
    console.print_json(json.dumps(payload["entry"], ensure_ascii=False))


@termbase_app.command(name="export")
def termbase_export_cmd(
    project_dir: Path | None = typer.Argument(
        None, help="Optional project directory or profile root."
    ),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    scope: str | None = typer.Option(
        None, "--scope", help="global|project|profile|effective."
    ),
    language: str | None = typer.Option(None, "--language", help="Language shard key."),
    output: Path | None = typer.Option(None, "--output", help="Output file path."),
    stdout: bool = typer.Option(False, "--stdout", help="Write the export to stdout."),
    export_format: str = typer.Option("shard", "--format", help="shard|bundle."),
) -> None:
    try:
        payload = termbase_export_workflow(
            project_dir,
            profile=profile,
            scope=scope,
            language=language,
            output=output,
            stdout=stdout,
            export_format=export_format,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    if "stdout" in payload:
        console.print(payload["stdout"], soft_wrap=True, markup=False, end="")
        return
    console.print(f"exported termbase: {payload['path']}", soft_wrap=True, markup=False)


@termbase_app.command(name="import")
def termbase_import_cmd(
    project_dir: Path | None = typer.Argument(
        None, help="Optional project directory or profile root."
    ),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    scope: str | None = typer.Option(None, "--scope", help="global|project|profile."),
    language: str | None = typer.Option(
        None, "--language", help="Destination language shard key."
    ),
    input_path: Path = typer.Option(..., "--input", help="Input shard or bundle JSON."),
    mode: str = typer.Option("dry-run", "--mode", help="dry-run|merge|replace."),
    on_conflict: str = typer.Option(
        "fail", "--on-conflict", help="fail|skip|overwrite|newer."
    ),
    approve_drafts: bool = typer.Option(
        False, "--approve-drafts", help="Promote imported drafts to approved."
    ),
    import_format: str = typer.Option("auto", "--format", help="auto|shard|bundle."),
) -> None:
    try:
        payload = termbase_import_workflow(
            project_dir,
            profile=profile,
            scope=scope,
            language=language,
            input_path=input_path,
            mode=mode,
            on_conflict=on_conflict,
            approve_drafts=approve_drafts,
            import_format=import_format,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(
        f"import {payload['mode']}: added={payload['added']} "
        f"updated={payload['updated']}"
    )
    if "backup_path" in payload:
        console.print(f"backup: {payload['backup_path']}", soft_wrap=True, markup=False)


@termbase_app.command(name="scan-source")
def termbase_scan_source_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory or profile root."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    language: str | None = typer.Option(None, "--language", help="Language shard key."),
    chapter: str | None = typer.Option(None, "--chapter", help="Optional chapter id."),
    entry: list[str] = typer.Option(
        [], "--entry", help="Repeatable termbase entry id filter."
    ),
    jsonl: bool = typer.Option(
        False, "--jsonl", help="Emit one JSON object per match."
    ),
) -> None:
    try:
        payload = termbase_scan_source_workflow(
            project_dir,
            profile=profile,
            language=language,
            chapter=chapter,
            entry_ids=entry,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    if jsonl:
        for match in payload["matches"]:
            console.print(
                json.dumps(match, ensure_ascii=False), soft_wrap=True, markup=False
            )
        return
    console.print(
        f"scanned {payload['records_scanned']} records, "
        f"{payload['matched_records']} matched records"
    )
    for match in payload["matches"]:
        if match["shadowed"]:
            continue
        console.print(
            f"{match['record_id']} {match['entry_id']} {match['source_span']}",
            soft_wrap=True,
            markup=False,
        )


@termbase_app.command(name="audit")
def termbase_audit_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory or profile root."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    language: str | None = typer.Option(None, "--language", help="Language shard key."),
    chapter: str | None = typer.Option(None, "--chapter", help="Optional chapter id."),
    entry: list[str] = typer.Option(
        [], "--entry", help="Repeatable termbase entry id filter."
    ),
    include_clean_matches: bool = typer.Option(
        False,
        "--include-clean-matches",
        help="Include clean matched records in output.",
    ),
    jsonl: bool = typer.Option(
        False, "--jsonl", help="Emit one JSON object per emitted result."
    ),
) -> None:
    try:
        payload = termbase_audit_workflow(
            project_dir,
            profile=profile,
            language=language,
            chapter=chapter,
            entry_ids=entry,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    emitted = [
        match
        for match in payload["matches"]
        if include_clean_matches or match["status"] != "clean"
    ]
    if jsonl:
        for match in emitted:
            console.print(
                json.dumps(match, ensure_ascii=False), soft_wrap=True, markup=False
            )
        return
    console.print(
        "source matched: "
        f"{payload['source_matched_records']}  "
        f"audited: {payload['audited_records']}  "
        f"clean: {payload['clean_records']}  "
        f"findings: {payload['finding_count']}"
    )
    for match in emitted:
        console.print(
            f"{match['record_id']} [{match['status']}/{match['severity']}] "
            f"{match['entry_id']}",
            soft_wrap=True,
            markup=False,
        )


@termbase_app.command(name="promote-candidate")
def termbase_promote_candidate_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    candidate_id: str = typer.Argument(..., help="Source-analysis candidate id."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    scope: str = typer.Option(
        "profile", "--scope", help="Destination scope: project or profile."
    ),
    preferred: list[str] = typer.Option(
        [], "--preferred", help="Repeatable preferred targets."
    ),
    preferred_policy: str = typer.Option(
        "required", "--preferred-policy", help="off|advisory|required."
    ),
    severity: str = typer.Option("error", "--severity", help="info|warn|error."),
    approve: bool = typer.Option(False, "--approve", help="Create as approved."),
    write: bool = typer.Option(False, "--write", help="Commit the promotion."),
    language: str | None = typer.Option(None, "--language", help="Language shard key."),
) -> None:
    try:
        payload = termbase_promote_candidate_workflow(
            project_dir,
            profile=profile,
            candidate_id=candidate_id,
            scope=scope,
            preferred=preferred,
            preferred_policy=preferred_policy,
            severity=severity,
            approve=approve,
            write=write,
            language=language,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    action = "promoted" if write else "would promote"
    console.print(
        f"{action} {candidate_id} to {payload['entry_id']} "
        f"({payload['scope']} {payload['language_key']}, {payload['status']})"
    )
    console.print(payload["path"], soft_wrap=True, markup=False)


@termbase_app.command(name="promote-context")
def termbase_promote_context_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory or profile root."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    language: str | None = typer.Option(None, "--language", help="Language shard key."),
    entry_id: str = typer.Option(..., "--entry", help="Termbase entry id."),
    as_advisory: bool = typer.Option(
        False, "--as-advisory", help="Promote as an advisory glossary entry."
    ),
    as_binding: bool = typer.Option(
        False, "--as-binding", help="Promote as a binding glossary entry."
    ),
    as_question: bool = typer.Option(
        False, "--as-question", help="Promote as a required context question."
    ),
) -> None:
    try:
        message = termbase_promote_context_workflow(
            project_dir,
            profile=profile,
            language=language,
            entry_id=entry_id,
            as_advisory=as_advisory,
            as_binding=as_binding,
            as_question=as_question,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(message)


@termbase_app.command(name="write-review")
def termbase_write_review_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory or profile root."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    language: str | None = typer.Option(None, "--language", help="Language shard key."),
    entry: list[str] = typer.Option(
        [], "--entry", help="Repeatable termbase entry id filter."
    ),
    pass_number: int = typer.Option(..., "--pass", help="Quality-review pass number."),
    include_clean_matches: bool = typer.Option(
        False,
        "--include-clean-matches",
        help="Include clean source matches in the review task.",
    ),
) -> None:
    try:
        payload = termbase_write_review_workflow(
            project_dir,
            profile=profile,
            language=language,
            entry_ids=entry,
            pass_number=pass_number,
            include_clean_matches=include_clean_matches,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(f"review task: {payload['review_task_id']}")
    console.print(f"records: {payload['record_count']}")
    console.print(f"read:   {payload['source_block']}", soft_wrap=True, markup=False)
    console.print(f"edit:   {payload['ingest_block']}", soft_wrap=True, markup=False)
