"""Typer CLI for booktx."""

from __future__ import annotations

import sys

import typer

from booktx import __version__
from booktx.cli_support import _project_status_snapshot, console
from booktx.command_catalog import (
    REMOVED_ROOT_COMMANDS,
    apply_command_catalog,
)
from booktx.commands.agents import agents_app
from booktx.commands.context import context_app
from booktx.commands.epub import epub_app
from booktx.commands.glossary import glossary_app
from booktx.commands.guide import guide_app
from booktx.commands.identity import identity_app
from booktx.commands.judge import judge_app
from booktx.commands.profile import profile_app
from booktx.commands.review import review_app
from booktx.commands.root import doctor_app, root_app
from booktx.commands.series import series_app
from booktx.commands.source import source_app
from booktx.commands.termbase import termbase_app
from booktx.commands.translate import translate_app
from booktx.commands.version import version_app

root_app.registered_commands = [
    command_info
    for command_info in root_app.registered_commands
    if command_info.name not in REMOVED_ROOT_COMMANDS
]
context_app.registered_commands = [
    command_info
    for command_info in context_app.registered_commands
    if command_info.name is not None
]
identity_app.registered_commands = [
    command_info
    for command_info in identity_app.registered_commands
    if command_info.name != "whoami"
]

app = typer.Typer(name="booktx", invoke_without_command=True, add_completion=False)

# Root commands are mounted without a group name so the public command tree is
# unchanged while the implementation lives outside this assembly module.
app.add_typer(root_app)
app.add_typer(guide_app)
app.add_typer(context_app, name="context")
app.add_typer(translate_app, name="translate")
app.add_typer(source_app, name="source")
app.add_typer(doctor_app, name="doctor")
app.add_typer(review_app, name="review")
app.add_typer(judge_app, name="judge")
app.add_typer(series_app, name="series")
app.add_typer(version_app, name="version")
app.add_typer(profile_app, name="profile")
app.add_typer(epub_app, name="epub")
app.add_typer(glossary_app, name="glossary")
app.add_typer(termbase_app, name="termbase")
app.add_typer(identity_app, name="identity")
app.add_typer(agents_app, name="agents")
apply_command_catalog(app, root_app=root_app)


@app.callback()
def _main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Print booktx version and exit.",
        is_eager=True,
    ),
) -> None:
    """booktx root options."""
    if version:
        console.print(__version__)
        raise typer.Exit


def main() -> None:
    """Console-script entry point (used by pyproject [project.scripts])."""
    try:
        app()
    except typer.Exit as exc:
        sys.exit(exc.exit_code)


__all__ = ["app", "main", "_project_status_snapshot"]
