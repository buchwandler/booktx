"""Typer commands for translation identity defaults (Phase 3 slice 1).

Thin command layer: each command parses options, loads the project via the
shared CLI helper, calls one workflow function in
:mod:`booktx.workflows.identity`, renders the result, and maps
:class:`booktx.errors.BooktxError` to a non-zero exit (handled inside the
shared ``_load_project_or_exit`` helper).

This module is imported by ``booktx/cli.py`` after the shared CLI helpers are
defined, so it may import private helpers (``console``, ``_die``-based
loaders, ``_resolve_project_value_args``, ``_print_identity``) from
:mod:`booktx.cli`. It must not import mutation helpers directly.
"""

from __future__ import annotations

from pathlib import Path

import typer

# Shared CLI helpers live in the neutral ``booktx.cli_support`` module so that
# command modules never need to import ``booktx.cli`` (which imports the command
# modules to register them, and would create a cycle).
from booktx.cli_support import (
    _load_project_or_exit,
    _print_identity,
    _resolve_project_value_args,
    console,
)
from booktx.workflows.identity import (
    clear_identity_field,
    resolve_identity_view,
    set_identity_defaults,
)

actor_app = typer.Typer(help="Manage translation actor defaults.")
harness_app = typer.Typer(help="Manage translation harness defaults.")
model_app = typer.Typer(help="Manage translation model defaults.")
identity_app = typer.Typer(
    help="Inspect resolved translation identity and project state."
)


# --- actor ------------------------------------------------------------------


@actor_app.command(name="whoami")
def actor_whoami(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
) -> None:
    """Show the resolved actor default for translation versioning."""
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    console.print(resolve_identity_view(proj).actor)


@actor_app.command(name="set")
def actor_set(
    arg1: str = typer.Argument(
        ..., help="Actor value, or project directory when using the legacy order."
    ),
    arg2: str | None = typer.Argument(
        None, help="Optional project directory or actor value."
    ),
    project: Path | None = typer.Option(None, "--project", help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
) -> None:
    """Persist the actor default used for new version tracks."""
    project_dir, actor = _resolve_project_value_args(
        arg1, arg2, value_name="actor", project_dir=project
    )
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    identity = set_identity_defaults(proj, actor=actor)
    console.print(identity.actor)


@actor_app.command(name="clear")
def actor_clear(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
) -> None:
    """Clear the stored actor default back to the local fallback."""
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    identity = clear_identity_field(proj, "actor")
    console.print(identity.actor)


# --- harness ----------------------------------------------------------------


@harness_app.command(name="whoami")
def harness_whoami(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
) -> None:
    """Show the resolved harness default for translation versioning."""
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    console.print(resolve_identity_view(proj).harness)


@harness_app.command(name="set")
def harness_set(
    arg1: str = typer.Argument(
        ..., help="Harness value, or project directory when using the legacy order."
    ),
    arg2: str | None = typer.Argument(
        None, help="Optional project directory or harness value."
    ),
    project: Path | None = typer.Option(None, "--project", help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
) -> None:
    """Persist the harness default used for new version tracks."""
    project_dir, harness = _resolve_project_value_args(
        arg1, arg2, value_name="harness", project_dir=project
    )
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    identity = set_identity_defaults(proj, harness=harness)
    console.print(identity.harness)


@harness_app.command(name="clear")
def harness_clear(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
) -> None:
    """Clear the stored harness default back to the local fallback."""
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    identity = clear_identity_field(proj, "harness")
    console.print(identity.harness)


# --- model ------------------------------------------------------------------


@model_app.command(name="whoami")
def model_whoami(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
) -> None:
    """Show the resolved model default for translation versioning."""
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    console.print(resolve_identity_view(proj).model)


@model_app.command(name="set")
def model_set(
    arg1: str = typer.Argument(
        ..., help="Model value, or project directory when using the legacy order."
    ),
    arg2: str | None = typer.Argument(
        None, help="Optional project directory or model value."
    ),
    project: Path | None = typer.Option(None, "--project", help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
) -> None:
    """Persist the model default used for new version tracks."""
    project_dir, model = _resolve_project_value_args(
        arg1, arg2, value_name="model", project_dir=project
    )
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    identity = set_identity_defaults(proj, model=model)
    console.print(identity.model)


@model_app.command(name="clear")
def model_clear(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
) -> None:
    """Clear the stored model default back to the local fallback."""
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    identity = clear_identity_field(proj, "model")
    console.print(identity.model)


# --- identity (alias of root whoami) ---------------------------------------


@identity_app.command(name="whoami")
def identity_whoami(
    project_dir: Path = typer.Argument(..., help="Project directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """Alias for the top-level whoami command."""
    _print_identity(project_dir, profile=profile, as_json=as_json)
