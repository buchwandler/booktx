"""Typer commands for repeated series setup workflows."""

from __future__ import annotations

from pathlib import Path

import typer

from booktx.cli_support import _handle_booktx_error, console
from booktx.errors import BooktxError
from booktx.workflows.series import (
    SeriesPrepareRequest,
    SeriesRecipeWriteOptions,
    build_series_recipe,
    prepare_series_book,
)

series_app = typer.Typer(help="Prepare the next book in a translated series.")
recipe_app = typer.Typer(help="Manage reusable series setup recipes.")
series_app.add_typer(recipe_app, name="recipe")


def _render_prepare_result(result) -> None:
    if result.write:
        console.print(
            f"Prepared {result.project_dir.name} for profile {result.profile}."
        )
    else:
        console.print("Dry run. No files written.")
        console.print(
            f"Would prepare {result.project_dir.name} for profile {result.profile}."
        )
    for step in result.steps:
        console.print(f"- {step.name}: {step.status} - {step.message}")
    if result.report_md is not None:
        console.print(f"report: {result.report_md}")
    if result.report_json is not None:
        console.print(f"report json: {result.report_json}")
    if result.context_ready:
        console.print("Context is ready.")
    else:
        console.print("Context is NOT READY because review is required.")
    console.print("Next commands:")
    for command in result.next_commands:
        console.print(f"  {command}")


@series_app.command(name="prepare")
def series_prepare_cmd(
    book: Path = typer.Argument(..., help="New or existing booktx project directory."),
    source_file: Path = typer.Option(
        ..., "--source-file", help="Source EPUB/Markdown file."
    ),
    source_lang: str | None = typer.Option(
        None, "--source", "--source-lang", help="Source language code."
    ),
    from_book: Path | None = typer.Option(
        None, "--from-book", help="Previous book project root."
    ),
    from_profile: str | None = typer.Option(
        None, "--from-profile", help="Profile to export from in --from-book mode."
    ),
    pack: Path | None = typer.Option(
        None, "--pack", help="Existing series context pack to import."
    ),
    recipe: Path | None = typer.Option(
        None, "--recipe", help="Reusable series recipe TOML file."
    ),
    profile: str | None = typer.Option(None, "--profile", help="Target profile name."),
    series_id: str | None = typer.Option(
        None, "--series-id", help="Series identifier for the context pack/recipe."
    ),
    title: str | None = typer.Option(
        None, "--title", help="Series context title for exported/imported policy."
    ),
    target: str | None = typer.Option(None, "--target", help="Target language code."),
    target_locale: str | None = typer.Option(
        None, "--target-locale", help="Target locale, for example de-DE."
    ),
    model: str | None = typer.Option(None, "--model", help="Profile model label."),
    harness: str | None = typer.Option(
        None, "--harness", help="Profile harness label."
    ),
    actor: str | None = typer.Option(None, "--actor", help="Profile actor label."),
    conflict: str | None = typer.Option(
        None,
        "--conflict",
        help="Context import conflict policy: fail, keep-local, or replace.",
    ),
    write_termbase: bool | None = typer.Option(
        None,
        "--write-termbase/--no-write-termbase",
        help="Write termbase entries from the imported pack.",
    ),
    termbase_scope: str | None = typer.Option(
        None, "--termbase-scope", help="Where pack termbase entries should be written."
    ),
    source_analysis_engine: str | None = typer.Option(
        None, "--source-analysis-engine", help="Source analysis engine."
    ),
    source_analysis_top: int | None = typer.Option(
        None, "--source-analysis-top", help="Maximum merged source-analysis candidates."
    ),
    source_analysis_min_count: int | None = typer.Option(
        None, "--source-analysis-min-count", help="Minimum corpus count."
    ),
    source_analysis_ngram_max: int | None = typer.Option(
        None, "--source-analysis-ngram-max", help="Maximum phrase length."
    ),
    include_advisory_prefill: bool | None = typer.Option(
        None,
        "--include-advisory-prefill/--no-include-advisory-prefill",
        help="Also prefill advisory glossary entries from source analysis.",
    ),
    write: bool = typer.Option(False, "--write", help="Execute the workflow."),
    reextract: bool = typer.Option(
        False, "--reextract", help="Force source extraction even when chunks exist."
    ),
    allow_existing_source: bool = typer.Option(
        False,
        "--allow-existing-source",
        help="Reuse an existing project even when source file/lang differ.",
    ),
    replace_profile: bool = typer.Option(
        False,
        "--replace-profile",
        help="Recreate an incompatible existing target profile.",
    ),
    consolidate_imported_policy: bool = typer.Option(
        True,
        "--consolidate-imported-policy/--no-consolidate-imported-policy",
        help="Reduce duplicate imported-policy review questions during prefill.",
    ),
) -> None:
    try:
        result = prepare_series_book(
            SeriesPrepareRequest(
                book=book,
                source_file=source_file,
                source_lang=source_lang,
                profile=profile,
                target=target,
                target_locale=target_locale,
                model=model,
                harness=harness,
                actor=actor,
                from_book=from_book,
                from_profile=from_profile,
                pack=pack,
                recipe=recipe,
                series_id=series_id,
                title=title,
                conflict=conflict,  # type: ignore[arg-type]
                write_termbase=write_termbase,
                termbase_scope=termbase_scope,  # type: ignore[arg-type]
                source_analysis_engine=source_analysis_engine,  # type: ignore[arg-type]
                source_analysis_top=source_analysis_top,
                source_analysis_min_count=source_analysis_min_count,
                source_analysis_ngram_max=source_analysis_ngram_max,
                include_advisory_prefill=include_advisory_prefill,
                write=write,
                reextract=reextract,
                allow_existing_source=allow_existing_source,
                replace_profile=replace_profile,
                consolidate_imported_policy=consolidate_imported_policy,
            )
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    _render_prepare_result(result)


@recipe_app.command(name="write")
def series_recipe_write_cmd(
    project_dir: Path = typer.Argument(..., help="Prepared booktx project root."),
    profile: str = typer.Option(
        ..., "--profile", help="Profile to export defaults from."
    ),
    series_id: str = typer.Option(..., "--series-id", help="Series identifier."),
    title: str = typer.Option(..., "--title", help="Series context title."),
    output: Path = typer.Option(..., "--output", help="Recipe TOML output path."),
    conflict: str = typer.Option(
        "replace", "--conflict", help="Default context import conflict policy."
    ),
    write_termbase: bool = typer.Option(
        True,
        "--write-termbase/--no-write-termbase",
        help="Default pack termbase write behavior.",
    ),
    termbase_scope: str = typer.Option(
        "project", "--termbase-scope", help="Default termbase write scope."
    ),
    source_analysis_engine: str = typer.Option(
        "auto", "--source-analysis-engine", help="Default source analysis engine."
    ),
    source_analysis_top: int = typer.Option(
        200, "--source-analysis-top", help="Default source-analysis top limit."
    ),
    source_analysis_min_count: int = typer.Option(
        2, "--source-analysis-min-count", help="Default source-analysis min count."
    ),
    source_analysis_ngram_max: int = typer.Option(
        4, "--source-analysis-ngram-max", help="Default source-analysis ngram max."
    ),
    include_advisory_prefill: bool = typer.Option(
        False,
        "--include-advisory-prefill/--no-include-advisory-prefill",
        help="Default source-analysis advisory prefill behavior.",
    ),
) -> None:
    try:
        result = build_series_recipe(
            SeriesRecipeWriteOptions(
                project_dir=project_dir,
                profile=profile,
                series_id=series_id,
                title=title,
                output=output,
                conflict=conflict,  # type: ignore[arg-type]
                write_termbase=write_termbase,
                termbase_scope=termbase_scope,  # type: ignore[arg-type]
                source_analysis_engine=source_analysis_engine,  # type: ignore[arg-type]
                source_analysis_top=source_analysis_top,
                source_analysis_min_count=source_analysis_min_count,
                source_analysis_ngram_max=source_analysis_ngram_max,
                include_advisory_prefill=include_advisory_prefill,
            )
        )
    except BooktxError as exc:
        _handle_booktx_error(exc)
        return
    console.print(f"wrote series recipe: {result.path}")


__all__ = ["series_app"]
