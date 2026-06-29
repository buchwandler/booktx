"""Shared CLI-layer helpers used by ``booktx/cli.py`` and the per-slice
command modules under ``booktx/commands/``.

This module exists to break the import cycle that would otherwise arise when
command modules need the CLI helpers (console, error-to-exit mapping, project
loading, legacy arg parsing) while ``booktx/cli.py`` imports the command
modules to register them. By factoring the shared helpers into this neutral
module:

- ``booktx/cli.py`` imports them at the top and imports command modules at
  the top (no cycle, no ``E402``).
- ``booktx/commands/*.py`` import them from here (never from ``booktx.cli``).

The boundary guard in ``tests/test_cli_command_boundary.py`` only scans
``booktx/commands/``; this module may import ``booktx.config`` /
``booktx.runtime`` / ``booktx.identity`` freely because it is *not* a command
module. Workflow functions under ``booktx/workflows/`` own the actual
mutations; the helpers here only do CLI concerns: rendering, exit-code
mapping, and runtime/project loading.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from rich.console import Console

from booktx.context import load_context
from booktx.errors import BooktxError
from booktx.identity import identity_payload
from booktx.runtime import RuntimeContext, resolve_runtime

if TYPE_CHECKING:
    # ``Project`` lives in ``booktx.config``; imported under TYPE_CHECKING so
    # this module never imports config at runtime (keeps import order simple).
    from booktx.config import Project
    from booktx.status import ProfilesOverview, StatusBundle

# Shared console instance for all CLI output.
console = Console()


def _isolated_mode_error() -> str:
    return (
        "command is not available in profile-root isolated mode.\n"
        "Run this from the project root for collaborative/admin workflows."
    )


def _reject_if_isolated(runtime: RuntimeContext) -> None:
    if runtime.mode.isolated_output:
        _die(_isolated_mode_error())


def _render_profiles_overview_human(overview: ProfilesOverview) -> None:
    console.print(f"project: {overview.project}")
    if overview.source:
        console.print(f"source: {overview.source}")
    if overview.source_records:
        console.print(f"source records: {overview.source_records}")
    if not overview.profiles:
        console.print("profiles: none")
        return
    console.print("profiles:")
    for item in overview.profiles:
        marker = "*" if item.active else " "
        coverage = (
            f"translated={item.translated_records}/{item.total_records}"
            if item.total_records
            else "translated=0/0"
        )
        console.print(
            f"  {marker} {item.profile}   kind={item.kind}  "
            f"target={item.target_locale or item.target_language}  "
            f"model={item.model or 'human'}  {coverage}"
        )
    if overview.active_profile:
        console.print()
        console.print(f"active profile: {overview.active_profile}")


def _load_context_status(proj: Project) -> tuple[bool, bool]:
    try:
        ctx = load_context(proj)
    except Exception as exc:  # noqa: BLE001
        _die(f"translation context is invalid: {exc}")
    return (ctx is not None, bool(ctx and ctx.ready))


def _project_status_snapshot(proj: Project) -> StatusBundle:
    """Build the typed status snapshot + runtime index for ``proj``.

    Thin wrapper over :func:`booktx.status.build_status_snapshot`; the CLI
    owns the invalid-context error UX here.
    """
    from booktx.status import build_status_snapshot

    context_exists, context_ready = _load_context_status(proj)
    return build_status_snapshot(
        proj, context_exists=context_exists, context_ready=context_ready
    )


def _die(message: str, code: int = 1) -> None:
    """Print an error and exit with ``code``."""
    console.print(f"[red]error:[/red] {message}")
    raise typer.Exit(code=code)


def _handle_booktx_error(exc: BooktxError) -> None:
    _die(str(exc))


def _load_runtime_or_exit(
    project_dir: Path,
    *,
    profile: str | None = None,
    require_profile: bool = False,
) -> RuntimeContext:
    try:
        return resolve_runtime(
            project_dir,
            profile=profile,
            require_profile=require_profile,
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        raise typer.Exit(code=1) from exc


def _resolve_project_value_args(
    arg1: str,
    arg2: str | None,
    *,
    value_name: str,
    project_dir: Path | None = None,
) -> tuple[Path, str]:
    """Accept VALUE, VALUE PROJECT_DIR, or PROJECT_DIR VALUE."""
    if project_dir is not None:
        if arg2 is not None:
            _die(f"--project cannot be combined with a second positional {value_name}")
        return project_dir.expanduser(), arg1

    if arg2 is None:
        return Path("."), arg1

    p1 = Path(arg1).expanduser()
    p2 = Path(arg2).expanduser()
    p1_is_project = (p1 / ".booktx" / "config.toml").is_file() or (
        p1 / ".booktx" / "source-config.toml"
    ).is_file()
    p2_is_project = (p2 / ".booktx" / "config.toml").is_file() or (
        p2 / ".booktx" / "source-config.toml"
    ).is_file()

    if p1_is_project and not p2_is_project:
        return p1, arg2
    if p2_is_project and not p1_is_project:
        return p2, arg1
    return p1, arg2


def _render_identity_human(payload: dict[str, Any]) -> None:
    context_payload = payload["context"]
    store_payload = payload["store"]
    context_state = {
        "ready": "READY",
        "not_ready": "NOT_READY",
        "missing": "MISSING",
        "invalid": "INVALID",
    }[str(context_payload["status"])]
    rows = [
        ("actor", payload["actor"]),
        ("harness", payload["harness"]),
        ("model", payload["model"]),
        ("active_version", payload["active_version"] or "none"),
        ("context", f"{context_state} {context_payload['path']}"),
        ("context_sha256", context_payload["sha256"] or "none"),
        ("source_sha256", payload["source_sha256"] or "none"),
        (
            "store_version",
            store_payload["version"]
            if store_payload["version"] is not None
            else "none",
        ),
        (
            "store_records",
            store_payload["record_count"]
            if store_payload["record_count"] is not None
            else "none",
        ),
    ]
    width = max(len(label) for label, _ in rows)
    console.print(f"booktx identity: {payload['project_dir']}", soft_wrap=True)
    for label, value in rows:
        console.print(f"{label + ':':<{width + 2}} {value}", soft_wrap=True)


def _print_identity(project_dir: Path, *, profile: str | None, as_json: bool) -> None:
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    payload = identity_payload(runtime.project, mode=runtime.mode)
    if as_json:
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return
    _render_identity_human(payload)


def _load_project_or_exit(
    project_dir: Path,
    *,
    profile: str | None = None,
    require_profile: bool = False,
) -> Project:
    return _load_runtime_or_exit(
        project_dir,
        profile=profile,
        require_profile=require_profile,
    ).project
