"""Binding glossary commands backed by the canonical termbase."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from booktx.cli_support import _handle_booktx_error, console
from booktx.errors import BooktxError
from booktx.workflows.glossary import (
    glossary_add_workflow,
    glossary_audit_workflow,
    glossary_export_workflow,
    glossary_import_workflow,
    glossary_mandate_workflow,
    glossary_remove_workflow,
    glossary_reset_workflow,
    glossary_status_workflow,
)

glossary_app = typer.Typer(
    help="Manage binding glossary entries backed by the canonical termbase."
)


@glossary_app.command(name="status")
def glossary_status_cmd(
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
        payload = glossary_status_workflow(
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


@glossary_app.command(name="add")
def glossary_add_cmd(
    project_dir: Path | None = typer.Argument(
        None, help="Optional project directory or profile root."
    ),
    source: str = typer.Argument(
        "", help="Primary source term when adding a flat glossary entry."
    ),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    scope: str | None = typer.Option(None, "--scope", help="global|project|profile."),
    language: str | None = typer.Option(
        None, "--language", help="Destination language shard key."
    ),
    entry_file: Path | None = typer.Option(
        None, "--file", help="Structured glossary/termbase entry JSON file."
    ),
    source_variant: list[str] = typer.Option(
        [], "--source-variant", help="Repeatable source variants."
    ),
    target: str = typer.Option("", "--target", help="Approved target term."),
    target_variant: list[str] = typer.Option(
        [], "--target-variant", help="Repeatable approved target variants."
    ),
    forbid: list[str] = typer.Option(
        [], "--forbid", help="Repeatable forbidden target terms."
    ),
    require_target: bool = typer.Option(
        False,
        "--require-target",
        help="Require the approved target in matching records.",
    ),
    enforce: str = typer.Option("warn", "--enforce", help="warn|error."),
    case_sensitive: bool = typer.Option(
        False, "--case-sensitive", help="Match the source term case-sensitively."
    ),
    note: str = typer.Option("", "--note", help="Short glossary note or instruction."),
) -> None:
    if entry_file is None and not source:
        raise typer.BadParameter("source is required unless --file is used")
    try:
        payload = glossary_add_workflow(
            project_dir,
            profile=profile,
            scope=scope,
            language=language,
            entry_file=entry_file,
            source=source,
            source_variants=source_variant,
            target=target,
            target_variants=target_variant,
            forbidden=forbid,
            require_target=require_target,
            enforce=enforce,
            case_sensitive=case_sensitive,
            notes=note,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(
        f"glossary entry {payload['entry_id']} -> {payload['status']} "
        f"({payload['scope']} {payload['language_key']})"
    )
    console.print(payload["path"], soft_wrap=True, markup=False)


@glossary_app.command(name="export")
def glossary_export_cmd(
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
        payload = glossary_export_workflow(
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
    console.print(f"exported glossary: {payload['path']}", soft_wrap=True, markup=False)


@glossary_app.command(name="remove")
def glossary_remove_cmd(
    project_dir: Path | None = typer.Argument(
        None, help="Optional project directory or profile root."
    ),
    source: str = typer.Argument(..., help="Source term to remove."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    missing_ok: bool = typer.Option(
        False, "--missing-ok", help="Succeed when the entry is already absent."
    ),
) -> None:
    try:
        message = glossary_remove_workflow(
            project_dir,
            profile=profile,
            source=source,
            missing_ok=missing_ok,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(message)


@glossary_app.command(name="reset")
def glossary_reset_cmd(
    project_dir: Path | None = typer.Argument(
        None, help="Optional project directory or profile root."
    ),
    source: str = typer.Argument(..., help="Source term to replace."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    target: str | None = typer.Option(None, "--target", help="Approved target term."),
    forbid: list[str] = typer.Option(
        [], "--forbid", help="Repeatable forbidden targets."
    ),
    category: str | None = typer.Option(None, "--category", help="Glossary category."),
    note: str = typer.Option("", "--note", help="Glossary note or rationale."),
    enforce: str = typer.Option("warn", "--enforce", help="warn|error."),
    source_variant: list[str] = typer.Option(
        [], "--source-variant", help="Repeatable source variants."
    ),
    target_variant: list[str] = typer.Option(
        [], "--target-variant", help="Repeatable approved target variants."
    ),
    require_target: bool = typer.Option(
        False,
        "--require-target",
        help="Require the approved target in matching records.",
    ),
    case_sensitive: bool = typer.Option(
        False, "--case-sensitive", help="Match the source term case-sensitively."
    ),
    create: bool = typer.Option(
        False, "--create", help="Create the entry when it does not exist."
    ),
) -> None:
    try:
        message = glossary_reset_workflow(
            project_dir,
            profile=profile,
            source=source,
            target=target,
            forbid=forbid,
            category=category,
            notes=note,
            enforce=enforce,
            source_variants=source_variant,
            target_variants=target_variant,
            require_target=require_target,
            case_sensitive=case_sensitive,
            create=create,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(message)


@glossary_app.command(name="mandate")
def glossary_mandate_cmd(
    project_dir: Path | None = typer.Argument(
        None, help="Optional project directory or profile root."
    ),
    source: str = typer.Argument(..., help="Source term to make binding."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    target: str | None = typer.Option(None, "--target", help="Approved target term."),
    forbid: list[str] = typer.Option(
        [], "--forbid", help="Repeatable forbidden targets."
    ),
    category: str | None = typer.Option(None, "--category", help="Glossary category."),
    note: str = typer.Option("", "--note", help="Glossary note or rationale."),
    enforce: str = typer.Option("error", "--enforce", help="warn|error."),
    source_variant: list[str] = typer.Option(
        [], "--source-variant", help="Repeatable source variants."
    ),
    target_variant: list[str] = typer.Option(
        [], "--target-variant", help="Repeatable approved target variants."
    ),
    case_sensitive: bool = typer.Option(
        False, "--case-sensitive", help="Match the source term case-sensitively."
    ),
) -> None:
    try:
        message = glossary_mandate_workflow(
            project_dir,
            profile=profile,
            source=source,
            target=target,
            forbid=forbid,
            category=category,
            notes=note,
            enforce=enforce,
            source_variants=source_variant,
            target_variants=target_variant,
            case_sensitive=case_sensitive,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(message)


@glossary_app.command(name="audit")
def glossary_audit_cmd(
    project_dir: Path | None = typer.Argument(
        None, help="Optional project directory or profile root."
    ),
    source: str = typer.Argument(..., help="Source term to audit."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    chapter: str | None = typer.Option(None, "--chapter", help="Optional chapter id."),
    include_inactive: bool = typer.Option(
        False,
        "--include-inactive",
        help="Include inactive historical versions in the audit.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    try:
        payload = glossary_audit_workflow(
            project_dir,
            profile=profile,
            source=source,
            chapter=chapter,
            include_inactive=include_inactive,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    if as_json:
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return
    console.print(f"source: {payload['source']}")
    console.print(f"records with matches: {payload['records_with_matches']}")
    console.print(f"findings: {payload['finding_count']}")


@glossary_app.command(name="import")
def glossary_import_cmd(
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
        payload = glossary_import_workflow(
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
    msg = (
        f"import {payload['mode']}: added={payload['added']} "
        f"updated={payload['updated']}"
    )
    console.print(msg)
