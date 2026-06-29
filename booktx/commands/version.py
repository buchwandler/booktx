"""Typer commands for translation-version management (Phase 3 slice 4).

Thin command layer for the ``version`` group (current / list / select /
set-label / fork-context / show) plus the no-subcommand callback. Commands
delegate to :mod:`booktx.workflows.version` and render results; they never
import ``booktx.config`` or ``booktx.translation_store`` directly.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from booktx.cli_support import (
    _handle_booktx_error,
    _load_project_or_exit,
    console,
)
from booktx.errors import BooktxError
from booktx.workflows.version import (
    fork_context,
    load_version_ledger,
    select_version,
    set_version_label,
    version_current_payload,
    version_show_payload,
)

version_app = typer.Typer(help="Inspect and manage translation version tracks.")


@version_app.callback(invoke_without_command=True)
def version_root(ctx: typer.Context) -> None:
    """Translation-version command group."""
    if ctx.invoked_subcommand is None:
        console.print(
            "[red]error:[/red] `booktx version` is a translation-version command "
            "group. Use `booktx --version` for the CLI package version, or "
            "`booktx version current PROJECT_DIR` for the active translation "
            "version.",
            soft_wrap=True,
        )
        raise typer.Exit(code=2)


@version_app.command(name="current")
def version_current(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """Show the current ledger-wide active version."""
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    payload = version_current_payload(proj)
    if as_json:
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return
    console.print(payload["active_version"] or "none")


@version_app.command(name="list")
def version_list(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
) -> None:
    """List all known major tracks and subversions."""
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    ledger = load_version_ledger(proj)
    if not ledger.tracks:
        console.print("no versions")
        return
    for track_id in sorted(ledger.tracks, key=int):
        track = ledger.tracks[track_id]
        active_marker = (
            "*"
            if ledger.active_version
            and ledger.active_version.startswith(f"{track.version}.")
            else " "
        )
        console.print(
            f"{active_marker} track {track.version}: {track.actor} / {track.harness} / "
            f"{track.model}{f' [{track.label}]' if track.label else ''}"
        )
        for sub_id in sorted(track.subversions, key=int):
            sub = track.subversions[sub_id]
            current_marker = (
                " (active)" if ledger.active_version == sub.version_ref else ""
            )
            scope_label = (
                f"baseline:{sub.baseline_sha256}"
                if sub.baseline_sha256 is not None
                else f"legacy-context:{sub.context_sha256}"
            )
            console.print(f"    {sub.version_ref}  {scope_label}{current_marker}")


@version_app.command(name="select")
def version_select(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    version_ref: str = typer.Argument(..., help="Version ref such as 1.2."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
) -> None:
    """Select the ledger-wide active version."""
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    ledger = select_version(proj, version_ref)
    console.print(ledger.active_version or "none")


@version_app.command(name="set-label")
def version_set_label(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    major_version: int = typer.Argument(..., help="Major track number."),
    label: str = typer.Argument(..., help="Human label for the track."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
) -> None:
    """Set the label for one major version track."""
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    ledger = set_version_label(proj, major_version, label)
    console.print(ledger.tracks[str(major_version)].label or "")


@version_app.command(name="fork-context")
def version_fork_context(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    note: str | None = typer.Option(None, "--note", help="Reason for the forced fork."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
) -> None:
    """Force a new subversion for the current track even when context hash matches."""
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    resolution = fork_context(proj, note=note)
    console.print(resolution.version_ref)


@version_app.command(name="show")
def version_show(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    selector: str = typer.Argument(..., help="Track number or dotted version ref."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """Show one major track or one specific dotted version entry."""
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    try:
        payload = version_show_payload(proj, selector)
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    if as_json:
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return
    console.print_json(json.dumps(payload, ensure_ascii=False, indent=2))
