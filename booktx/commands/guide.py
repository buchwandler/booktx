"""Root-level `booktx guide` command."""

from __future__ import annotations

from pathlib import Path

import typer

from booktx.cli_support import _load_runtime_or_exit, _project_status_snapshot
from booktx.human_guide import build_guide_result
from booktx.rendering_guide import print_guide_human, print_guide_json

guide_app = typer.Typer()


@guide_app.command(name="guide")
def guide_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory or profile root."),
    profile: str | None = typer.Option(
        None, "--profile", help="Translation profile name."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """Show the current lifecycle stage and the next human action."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=False)
    bundle = None
    if runtime.project.profile is not None:
        bundle = _project_status_snapshot(runtime.project)
    result = build_guide_result(runtime, bundle=bundle, project_arg=str(project_dir))
    if as_json:
        print_guide_json(result)
        return
    print_guide_human(result)


__all__ = ["guide_app"]
