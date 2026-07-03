"""Typer commands for translation-profile management (Phase 3 slice 4).

Thin command layer for the ``profile`` group (create / list / show /
compare / migrate-current / create-pass-through) plus rendering helpers.
Commands delegate to :mod:`booktx.workflows.profile` and :mod:`booktx.cli_support`
and never import ``booktx.config`` or ``booktx.translation_store`` directly.
Profile-root redaction behavior is preserved.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from booktx.cli_support import (
    _die,
    _handle_booktx_error,
    _load_runtime_or_exit,
    _reject_if_isolated,
    _render_profiles_overview_human,
    console,
)
from booktx.errors import BooktxError
from booktx.workflows.profile import (
    build_profile_detail_payload,
    build_profiles_overview_payload,
    compare_profile_record,
    create_pass_through_workflow,
    create_profile_workflow,
    migrate_current_workflow,
)

profile_app = typer.Typer(help="Manage isolated translation profiles.")


@profile_app.command(name="create")
def profile_create_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile_name: str = typer.Argument(..., help="Translation profile name."),
    target: str = typer.Option(..., "--target", help="Target language code, e.g. de."),
    target_locale: str | None = typer.Option(
        None, "--target-locale", help="Target locale code, e.g. de-DE."
    ),
    model: str | None = typer.Option(None, "--model", help="Profile model label."),
    harness: str | None = typer.Option(
        None, "--harness", help="Profile harness label."
    ),
    actor: str | None = typer.Option(None, "--actor", help="Profile actor label."),
    output_filename: str | None = typer.Option(
        None, "--output-filename", help="Optional output filename override."
    ),
) -> None:
    try:
        project = create_profile_workflow(
            project_dir,
            profile_name,
            target_language=target,
            target_locale=target_locale,
            actor=actor,
            harness=harness,
            model=model,
            output_filename=output_filename,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(f"created profile: {project.profile}")


@profile_app.command(name="list")
def profile_list_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    runtime = _load_runtime_or_exit(project_dir, require_profile=False)
    if runtime.mode.isolated_output:
        profile_name = runtime.mode.profile_name or ""
        payload = {
            "isolated": True,
            "profiles": [
                {
                    "profile": profile_name,
                }
            ],
        }
        if as_json:
            console.print_json(json.dumps(payload, ensure_ascii=False))
            return
        console.print(
            "isolated mode: showing current profile only; "
            "run from project root for all profiles"  # noqa: E501
        )
        console.print(f"  {profile_name}")
        return
    overview = build_profiles_overview_payload(runtime.project.root)
    if as_json:
        console.print_json(
            json.dumps(overview.model_dump(mode="json"), ensure_ascii=False)
        )
        return
    _render_profiles_overview_human(overview)


@profile_app.command(name="show")
def profile_show_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile_name: str = typer.Argument(..., help="Translation profile name."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    runtime = _load_runtime_or_exit(project_dir, require_profile=False)
    if runtime.mode.isolated_output:
        current = runtime.mode.profile_name or ""
        # In profile-root mode, "." means the current profile; reject others.
        if profile_name != "." and profile_name != current:
            _die(
                f"profile {profile_name!r} is not accessible in isolated mode; "
                "only the current profile is available"
            )
        profile_name = current
    try:
        payload = build_profile_detail_payload(runtime.project.root, profile_name)
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    if runtime.mode.isolated_output:
        # Sanitize paths for isolated output.
        payload["path"] = "."
    if as_json:
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return
    console.print(f"profile: {payload['profile']}")
    console.print(f"kind: {payload['kind']}")
    console.print(f"path: {payload['path']}")
    console.print(f"target: {payload['target_locale']}")
    console.print(f"model: {payload['model']}")
    console.print(f"context: {'ready' if payload['context_ready'] else 'not ready'}")
    console.print(f"active version: {payload['active_version'] or 'none'}")
    console.print(
        f"records translated: {payload['records_translated']}/{payload['records_total']}"  # noqa: E501
    )
    console.print(
        f"chapters complete: {payload['chapters_complete']}/{payload['chapters_total']}"
    )


@profile_app.command(name="compare")
def profile_compare_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profiles: str = typer.Option(
        ..., "--profiles", help="Comma-separated profile names."
    ),
    record: str = typer.Option(
        ..., "--record", help="Record ref or canonical record id."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    runtime = _load_runtime_or_exit(project_dir, require_profile=False)
    _reject_if_isolated(runtime)
    try:
        payload = compare_profile_record(runtime.project.root, profiles, record)
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    if as_json:
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return
    console.print(f"record: {payload['record_ref']}")
    console.print(f"source: {payload['source']}")
    for item in payload["comparisons"]:
        provenance = item.get("selection_provenance")
        suffix = ""
        if (
            provenance
            and provenance.get("selected_profile")
            and provenance.get("selected_ref")
        ):
            suffix = (
                f" [copied from {provenance['selected_profile']} "
                f"{provenance['selected_ref']}]"
            )
        console.print(
            f"{item['profile']} ({item['target_locale'] or item['target_language']}): "
            f"{item['target'] or '<missing>'}{suffix}"
        )


@profile_app.command(name="migrate-current")
def profile_migrate_current_cmd(
    project_dir: Path = typer.Argument(..., help="Legacy project directory."),
    profile_name: str = typer.Argument(..., help="Target translation profile name."),
    target: str | None = typer.Option(
        None, "--target", help="Override target language."
    ),
    target_locale: str | None = typer.Option(
        None, "--target-locale", help="Target locale code, e.g. de-DE."
    ),
    actor: str | None = typer.Option(None, "--actor", help="Profile actor label."),
    harness: str | None = typer.Option(
        None, "--harness", help="Profile harness label."
    ),
    model: str | None = typer.Option(None, "--model", help="Profile model label."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the migration plan only."
    ),
) -> None:
    try:
        payload = migrate_current_workflow(
            project_dir,
            profile_name,
            target_language=target,
            target_locale=target_locale,
            actor=actor,
            harness=harness,
            model=model,
            dry_run=dry_run,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    if dry_run:
        console.print(
            f"dry-run: would migrate legacy project to profile {profile_name}"
        )
        moves = payload.get("moves", [])
        if isinstance(moves, list):
            for move in moves:
                console.print(f"{move}")
        return
    console.print(f"migrated profile: {payload['profile']}")
    if "migration_manifest" in payload:
        console.print(f"migration manifest: {payload['migration_manifest']}")
    console.print(f"next: booktx status {project_dir} --profile {profile_name}")
    console.print(
        "next: booktx translate next "
        f"{project_dir} --profile {profile_name} "
        "--unit batch --max-words 500 --format block"  # noqa: E501
    )


@profile_app.command(name="create-pass-through")
def profile_create_pass_through_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile_name: str = typer.Argument(..., help="Pass-through profile name."),
    output_filename: str | None = typer.Option(
        None, "--output-filename", help="Optional output filename override."
    ),
) -> None:
    """Create a pass-through profile whose target language equals the source language."""  # noqa: E501
    try:
        project = create_pass_through_workflow(
            project_dir,
            profile_name,
            output_filename=output_filename,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(f"created pass-through profile: {project.profile}")
