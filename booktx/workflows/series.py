"""Workflow helpers for preparing the next book in a translated series."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import tomli_w

try:  # pragma: no cover - Python 3.11+ uses stdlib
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

from booktx.cli_support import _isolated_mode_error
from booktx.config import (
    BooktxError,
    find_source_file,
    init_source_project,
    load_profile_config,
    load_profile_project,
    load_source_project,
    profile_dir,
    source_config_path,
)
from booktx.context import context_markdown_path, load_context, write_context
from booktx.epub_manifest import sha256_path
from booktx.errors import _err
from booktx.io_utils import write_json_text_atomic, write_text_atomic
from booktx.runtime import resolve_runtime
from booktx.tasks import project_relative
from booktx.versioning import resolve_identity
from booktx.workflows.context import (
    context_pack_import_has_failures,
    export_context_pack_workflow,
    import_context_pack_workflow,
    init_context_workflow,
    load_context_or_die,
    render_context_command,
)
from booktx.workflows.profile import create_profile_workflow
from booktx.workflows.root import audit_chapters_workflow, extract_project_workflow
from booktx.workflows.source import analyze_source

SERIES_RECIPE_SCHEMA = "booktx.series-recipe.v1"


@dataclass(frozen=True)
class SeriesRecipe:
    series_id: str
    title: str
    source_lang: str
    profile: str
    target: str
    target_locale: str
    model: str
    context_conflict: Literal["fail", "keep-local", "replace"]
    write_termbase: bool
    termbase_scope: Literal["project", "profile"]
    source_analysis_engine: Literal["auto", "spacy", "simple"]
    source_analysis_top: int
    source_analysis_min_count: int
    source_analysis_ngram_max: int
    include_advisory_prefill: bool

    def as_toml_payload(self) -> dict[str, object]:
        return {
            "schema": SERIES_RECIPE_SCHEMA,
            "series_id": self.series_id,
            "title": self.title,
            "source_lang": self.source_lang,
            "profile": self.profile,
            "target": self.target,
            "target_locale": self.target_locale,
            "model": self.model,
            "context_conflict": self.context_conflict,
            "write_termbase": self.write_termbase,
            "termbase_scope": self.termbase_scope,
            "source_analysis_engine": self.source_analysis_engine,
            "source_analysis_top": self.source_analysis_top,
            "source_analysis_min_count": self.source_analysis_min_count,
            "source_analysis_ngram_max": self.source_analysis_ngram_max,
            "include_advisory_prefill": self.include_advisory_prefill,
        }


@dataclass(frozen=True)
class SeriesRecipeWriteOptions:
    project_dir: Path
    profile: str
    series_id: str
    title: str
    output: Path
    conflict: Literal["fail", "keep-local", "replace"] = "replace"
    write_termbase: bool = True
    termbase_scope: Literal["project", "profile"] = "project"
    source_analysis_engine: Literal["auto", "spacy", "simple"] = "auto"
    source_analysis_top: int = 200
    source_analysis_min_count: int = 2
    source_analysis_ngram_max: int = 4
    include_advisory_prefill: bool = False


@dataclass(frozen=True)
class SeriesPrepareRequest:
    book: Path
    source_file: Path
    source_lang: str | None = None
    profile: str | None = None
    target: str | None = None
    target_locale: str | None = None
    model: str | None = None
    harness: str | None = None
    actor: str | None = None
    from_book: Path | None = None
    from_profile: str | None = None
    pack: Path | None = None
    recipe: Path | None = None
    series_id: str | None = None
    title: str | None = None
    conflict: Literal["fail", "keep-local", "replace"] | None = None
    write_termbase: bool | None = None
    termbase_scope: Literal["project", "profile"] | None = None
    source_analysis_engine: Literal["auto", "spacy", "simple"] | None = None
    source_analysis_top: int | None = None
    source_analysis_min_count: int | None = None
    source_analysis_ngram_max: int | None = None
    include_advisory_prefill: bool | None = None
    write: bool = False
    reextract: bool = False
    allow_existing_source: bool = False
    replace_profile: bool = False
    consolidate_imported_policy: bool = True


@dataclass(frozen=True)
class SeriesPrepareOptions:
    book: Path
    source_file: Path
    source_lang: str
    profile: str
    target: str
    target_locale: str | None
    model: str | None
    harness: str | None
    actor: str | None
    from_book: Path | None
    from_profile: str | None
    pack: Path | None
    recipe: Path | None
    series_id: str
    title: str
    conflict: Literal["fail", "keep-local", "replace"]
    write_termbase: bool
    termbase_scope: Literal["project", "profile"]
    source_analysis_engine: Literal["auto", "spacy", "simple"]
    source_analysis_top: int
    source_analysis_min_count: int
    source_analysis_ngram_max: int
    include_advisory_prefill: bool
    write: bool
    reextract: bool
    allow_existing_source: bool
    replace_profile: bool
    consolidate_imported_policy: bool


@dataclass(frozen=True)
class SeriesStepResult:
    name: str
    status: Literal["planned", "skipped", "written"]
    message: str = ""
    path: str | None = None


@dataclass
class SeriesPrepareResult:
    write: bool
    project_dir: Path
    profile: str
    context_ready: bool
    review_required: bool
    steps: list[SeriesStepResult]
    pack_path: Path
    source_name: str
    previous_context: str | None = None
    termbase_imported: str = "no"
    report_json: Path | None = None
    report_md: Path | None = None
    next_commands: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SeriesRecipeWriteResult:
    path: Path
    recipe: SeriesRecipe


def _reject_if_invoked_from_isolated_mode(book: Path) -> None:
    for candidate in (Path.cwd(), book):
        try:
            runtime = resolve_runtime(candidate, require_profile=False)
        except BooktxError:
            continue
        if runtime.mode.isolated_output:
            raise _err(
                "series_prepare_isolated_mode",
                _isolated_mode_error(),
            )


def _validate_prepare_request(request: SeriesPrepareRequest) -> None:
    if not request.source_file.is_file():
        raise _err(
            "series_prepare_missing_source",
            f"source file does not exist: {request.source_file}",
        )
    if (request.from_book is None) == (request.pack is None):
        if request.from_book is None:
            raise _err(
                "series_prepare_pack_source_missing",
                "pass exactly one of --from-book or --pack",
            )
        raise _err(
            "series_prepare_pack_source_conflict",
            "pass exactly one of --from-book or --pack",
        )
    if request.pack is not None and not request.pack.is_file():
        raise _err(
            "series_prepare_missing_pack", f"pack file does not exist: {request.pack}"
        )


def _read_series_recipe(path: Path) -> SeriesRecipe:
    try:
        with path.open("rb") as fh:
            payload = tomllib.load(fh)
    except FileNotFoundError as exc:
        raise _err("series_recipe_missing", f"recipe file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise _err("series_recipe_invalid", f"invalid series recipe: {exc}") from exc
    if payload.get("schema") != SERIES_RECIPE_SCHEMA:
        raise _err(
            "series_recipe_schema",
            f"recipe schema must be {SERIES_RECIPE_SCHEMA!r}",
        )
    try:
        return SeriesRecipe(
            series_id=str(payload["series_id"]),
            title=str(payload["title"]),
            source_lang=str(payload["source_lang"]),
            profile=str(payload["profile"]),
            target=str(payload["target"]),
            target_locale=str(payload["target_locale"]),
            model=str(payload.get("model") or ""),
            context_conflict=str(payload.get("context_conflict") or "fail"),  # type: ignore[arg-type]
            write_termbase=bool(payload.get("write_termbase", False)),
            termbase_scope=str(payload.get("termbase_scope") or "project"),  # type: ignore[arg-type]
            source_analysis_engine=str(payload.get("source_analysis_engine") or "auto"),  # type: ignore[arg-type]
            source_analysis_top=int(payload.get("source_analysis_top", 200)),
            source_analysis_min_count=int(payload.get("source_analysis_min_count", 2)),
            source_analysis_ngram_max=int(payload.get("source_analysis_ngram_max", 4)),
            include_advisory_prefill=bool(
                payload.get("include_advisory_prefill", False)
            ),
        )
    except KeyError as exc:
        raise _err(
            "series_recipe_invalid", f"recipe is missing {exc.args[0]!r}"
        ) from exc


def _resolve_prepare_options(request: SeriesPrepareRequest) -> SeriesPrepareOptions:
    _validate_prepare_request(request)
    recipe = _read_series_recipe(request.recipe) if request.recipe is not None else None
    return SeriesPrepareOptions(
        book=request.book.expanduser().resolve(),
        source_file=request.source_file.expanduser().resolve(),
        source_lang=request.source_lang or (recipe.source_lang if recipe else "en"),
        profile=request.profile or (recipe.profile if recipe else ""),
        target=request.target or (recipe.target if recipe else ""),
        target_locale=request.target_locale
        or (recipe.target_locale if recipe else None),
        model=request.model
        if request.model is not None
        else (recipe.model if recipe else None),
        harness=request.harness,
        actor=request.actor,
        from_book=request.from_book.expanduser().resolve()
        if request.from_book
        else None,
        from_profile=request.from_profile or (recipe.profile if recipe else None),
        pack=request.pack.expanduser().resolve() if request.pack else None,
        recipe=request.recipe.expanduser().resolve() if request.recipe else None,
        series_id=request.series_id or (recipe.series_id if recipe else ""),
        title=request.title or (recipe.title if recipe else ""),
        conflict=request.conflict or (recipe.context_conflict if recipe else "fail"),
        write_termbase=(
            request.write_termbase
            if request.write_termbase is not None
            else (recipe.write_termbase if recipe else False)
        ),
        termbase_scope=request.termbase_scope
        or (recipe.termbase_scope if recipe else "project"),
        source_analysis_engine=request.source_analysis_engine
        or (recipe.source_analysis_engine if recipe else "auto"),
        source_analysis_top=request.source_analysis_top
        if request.source_analysis_top is not None
        else (recipe.source_analysis_top if recipe else 200),
        source_analysis_min_count=request.source_analysis_min_count
        if request.source_analysis_min_count is not None
        else (recipe.source_analysis_min_count if recipe else 2),
        source_analysis_ngram_max=request.source_analysis_ngram_max
        if request.source_analysis_ngram_max is not None
        else (recipe.source_analysis_ngram_max if recipe else 4),
        include_advisory_prefill=(
            request.include_advisory_prefill
            if request.include_advisory_prefill is not None
            else (recipe.include_advisory_prefill if recipe else False)
        ),
        write=request.write,
        reextract=request.reextract,
        allow_existing_source=request.allow_existing_source,
        replace_profile=request.replace_profile,
        consolidate_imported_policy=request.consolidate_imported_policy,
    )


def _validate_prepare_options(options: SeriesPrepareOptions) -> None:
    _reject_if_invoked_from_isolated_mode(options.book)
    if not options.profile:
        raise _err("series_prepare_missing_profile", "--profile is required")
    if not options.target:
        raise _err("series_prepare_missing_target", "--target is required")
    if not options.series_id:
        raise _err("series_prepare_missing_series_id", "--series-id is required")
    if not options.title:
        raise _err("series_prepare_missing_title", "--title is required")
    if options.from_book is not None and not options.from_profile:
        raise _err(
            "series_prepare_previous_profile_missing",
            "--from-profile is required when --from-book is used",
        )
    if options.conflict not in {"fail", "keep-local", "replace"}:
        raise _err(
            "series_prepare_invalid_conflict",
            "--conflict must be fail, keep-local, or replace",
        )
    if options.termbase_scope not in {"project", "profile"}:
        raise _err(
            "series_prepare_invalid_termbase_scope",
            "--termbase-scope must be project or profile",
        )
    if options.source_analysis_engine not in {"auto", "spacy", "simple"}:
        raise _err(
            "series_prepare_invalid_engine",
            "--source-analysis-engine must be auto, spacy, or simple",
        )
    if options.from_book is not None:
        try:
            load_profile_project(options.from_book, options.from_profile or "")
        except BooktxError as exc:
            raise _err(
                "series_prepare_previous_profile_missing",
                f"previous book/profile missing: {exc}",
            ) from exc


def _maybe_load_existing_project(book: Path):
    legacy_path = book / ".booktx" / "config.toml"
    if source_config_path(book).is_file() or legacy_path.is_file():
        return load_source_project(book)
    return None


def _profile_conflicts(profile_project, options: SeriesPrepareOptions) -> list[str]:
    profile_cfg = load_profile_config(profile_project.root, options.profile)
    conflicts: list[str] = []
    if profile_cfg.kind != "translation":
        conflicts.append(f"kind={profile_cfg.kind}")
    if profile_cfg.target_language != options.target:
        conflicts.append(
            f"target={profile_cfg.target_language} expected {options.target}"
        )
    expected_locale = options.target_locale or options.target
    actual_locale = profile_cfg.target_locale or profile_cfg.target_language
    if actual_locale != expected_locale:
        conflicts.append(f"target_locale={actual_locale} expected {expected_locale}")
    resolved_identity = resolve_identity(profile_project)
    if options.model and resolved_identity.model != options.model:
        conflicts.append(f"model={resolved_identity.model} expected {options.model}")
    if options.actor and resolved_identity.actor != options.actor:
        conflicts.append(f"actor={resolved_identity.actor} expected {options.actor}")
    if options.harness and resolved_identity.harness != options.harness:
        conflicts.append(
            f"harness={resolved_identity.harness} expected {options.harness}"
        )
    return conflicts


def _relative(path: Path, root: Path) -> str:
    return project_relative(path, root)


def _series_pack_path(options: SeriesPrepareOptions) -> Path:
    return (
        options.book
        / ".booktx"
        / "series-packs"
        / f"{options.series_id}.{options.target}.{options.profile}.json"
    )


def _build_next_commands(options: SeriesPrepareOptions) -> list[str]:
    book_arg = options.book.as_posix()
    return [
        f"booktx context questionnaire {book_arg} --profile {options.profile} --stdout",
        f"booktx context status {book_arg} --profile {options.profile}",
        f"booktx context render {book_arg} --profile {options.profile} --write",
        f"booktx context mark-ready {book_arg} --profile {options.profile}",
        f"booktx agents write {book_arg} --mode isolated --profile {options.profile}",
    ]


def _write_prepare_reports(
    options: SeriesPrepareOptions,
    *,
    steps: list[SeriesStepResult],
    pack_path: Path,
    source_name: str,
    previous_context: str | None,
    termbase_imported: str,
    chapter_audit_blocked: bool,
) -> tuple[Path, Path]:
    reports_dir = options.book / ".booktx" / "reports"
    report_json = reports_dir / "series-prepare.json"
    report_md = reports_dir / "series-prepare.md"
    next_commands = _build_next_commands(options)
    payload: dict[str, Any] = {
        "schema": "booktx.series-prepare-report.v1",
        "project": options.book.name,
        "project_dir": str(options.book),
        "profile": options.profile,
        "source": source_name,
        "previous_context": previous_context,
        "context_pack": _relative(pack_path, options.book)
        if pack_path.is_relative_to(options.book)
        else str(pack_path),
        "write": True,
        "context_ready": False,
        "review_required": True,
        "chapter_audit_blocked": chapter_audit_blocked,
        "termbase_imported": termbase_imported,
        "steps": [
            {
                "name": step.name,
                "status": step.status,
                "message": step.message,
                "path": step.path,
            }
            for step in steps
        ],
        "next_commands": next_commands,
    }
    source_extracted = (
        "yes"
        if any(step.name == "extract" and step.status == "written" for step in steps)
        else "reused"
    )

    lines = [
        "# Series prepare review",
        "",
        f"Project: {options.book.name}",
        f"Profile: {options.profile}",
        f"Source: {source_name}",
    ]
    if previous_context:
        lines.append(f"Previous context: {previous_context}")
    lines.extend(
        [
            f"Context pack: {payload['context_pack']}",
            "",
            "## Status",
            "",
            f"- Source extracted: {source_extracted}",
            "- Chapter audit: blocking errors found"
            if chapter_audit_blocked
            else "- Chapter audit: no blocking errors",
            "- Profile ready: yes",
            "- Context pack imported: yes",
            f"- Termbase imported: {termbase_imported}",
            "- Source analysis written: yes",
            "- Profile source-analysis snapshot: yes",
            "- Context rendered: yes",
            "- Context ready: no, human review required",
            "",
            "## New review work",
            "",
            "Run:",
            "",
            "```bash",
            next_commands[0],
            next_commands[1],
            "```",
            "",
            "After approving/answering new questions:",
            "",
            "```bash",
            next_commands[2],
            next_commands[3],
            next_commands[4],
            "```",
            "",
        ]
    )
    write_json_text_atomic(
        report_json, json.dumps(payload, indent=2, ensure_ascii=False)
    )
    write_text_atomic(report_md, "\n".join(lines))
    return report_json, report_md


def _ensure_source_matches(project, options: SeriesPrepareOptions) -> None:
    if options.allow_existing_source:
        return
    source = find_source_file(project)
    if project.config.source_language != options.source_lang:
        raise _err(
            "series_prepare_source_mismatch",
            "existing source language does not match --source-lang; "
            "pass --allow-existing-source to reuse it",
        )
    if source.name != options.source_file.name:
        raise _err(
            "series_prepare_source_mismatch",
            "existing source file name does not match --source-file; "
            "pass --allow-existing-source to reuse it",
        )
    if sha256_path(source) != sha256_path(options.source_file):
        raise _err(
            "series_prepare_source_mismatch",
            "existing source file content does not match --source-file; "
            "pass --allow-existing-source to reuse it",
        )


def _render_recipe_payload(recipe: SeriesRecipe) -> str:
    return tomli_w.dumps(recipe.as_toml_payload())


def build_series_recipe(options: SeriesRecipeWriteOptions) -> SeriesRecipeWriteResult:
    if options.conflict not in {"fail", "keep-local", "replace"}:
        raise _err(
            "series_recipe_invalid_conflict",
            "--conflict must be fail, keep-local, or replace",
        )
    if options.termbase_scope not in {"project", "profile"}:
        raise _err(
            "series_recipe_invalid_termbase_scope",
            "--termbase-scope must be project or profile",
        )
    project = load_profile_project(options.project_dir, options.profile)
    profile_cfg = load_profile_config(options.project_dir, options.profile)
    identity = resolve_identity(project)
    recipe = SeriesRecipe(
        series_id=options.series_id,
        title=options.title,
        source_lang=project.source_config.source_language,
        profile=options.profile,
        target=profile_cfg.target_language,
        target_locale=profile_cfg.target_locale or profile_cfg.target_language,
        model=identity.model,
        context_conflict=options.conflict,
        write_termbase=options.write_termbase,
        termbase_scope=options.termbase_scope,
        source_analysis_engine=options.source_analysis_engine,
        source_analysis_top=options.source_analysis_top,
        source_analysis_min_count=options.source_analysis_min_count,
        source_analysis_ngram_max=options.source_analysis_ngram_max,
        include_advisory_prefill=options.include_advisory_prefill,
    )
    write_text_atomic(
        options.output.expanduser().resolve(), _render_recipe_payload(recipe)
    )
    return SeriesRecipeWriteResult(
        path=options.output.expanduser().resolve(),
        recipe=recipe,
    )


def _plan_prepare(options: SeriesPrepareOptions) -> SeriesPrepareResult:
    steps: list[SeriesStepResult] = []
    pack_path = options.pack or _series_pack_path(options)
    previous_context = None
    if options.from_book is not None:
        previous_context = f"{options.from_book.name}/{options.from_profile}"
        steps.append(
            SeriesStepResult(
                name="pack",
                status="planned",
                message=(
                    "would export a context pack from "
                    f"{previous_context} to {_relative(pack_path, options.book)}"
                ),
                path=str(pack_path),
            )
        )
    else:
        steps.append(
            SeriesStepResult(
                name="pack",
                status="planned",
                message=f"would use pack {options.pack}",
                path=str(options.pack),
            )
        )
    project = _maybe_load_existing_project(options.book)
    if project is None:
        steps.append(
            SeriesStepResult(
                name="project",
                status="planned",
                message="would initialize a new source project",
            )
        )
        steps.append(
            SeriesStepResult(
                name="extract",
                status="planned",
                message="would extract source chunks and write shared manifests",
            )
        )
    else:
        _ensure_source_matches(project, options)
        steps.append(
            SeriesStepResult(
                name="project",
                status="skipped",
                message="would reuse the existing source project",
            )
        )
        if project.chunks() and not options.reextract:
            steps.append(
                SeriesStepResult(
                    name="extract",
                    status="skipped",
                    message="would reuse existing extracted chunks",
                )
            )
        else:
            steps.append(
                SeriesStepResult(
                    name="extract",
                    status="planned",
                    message="would extract source chunks and write shared manifests",
                )
            )
    steps.append(
        SeriesStepResult(
            name="chapter-audit",
            status="planned",
            message="would run the EPUB chapter audit and stop on blocking errors",
        )
    )
    if (
        project is not None
        and (options.book / "translations" / options.profile / "config.toml").is_file()
    ):
        profile_project = load_profile_project(options.book, options.profile)
        conflicts = _profile_conflicts(profile_project, options)
        if conflicts and not options.replace_profile:
            raise _err(
                "series_prepare_profile_conflict",
                "target profile exists with incompatible settings: "
                + ", ".join(conflicts)
                + ". Pass --replace-profile to recreate it.",
            )
        steps.append(
            SeriesStepResult(
                name="profile",
                status="skipped" if not conflicts else "planned",
                message="would reuse the existing profile"
                if not conflicts
                else "would replace the incompatible profile",
            )
        )
    else:
        steps.append(
            SeriesStepResult(
                name="profile",
                status="planned",
                message="would create the target translation profile",
            )
        )
    steps.extend(
        [
            SeriesStepResult(
                name="context-import",
                status="planned",
                message=(
                    "would import the series context pack with "
                    f"--conflict {options.conflict}"
                ),
            ),
            SeriesStepResult(
                name="source-analysis",
                status="planned",
                message=(
                    "would run source analysis with "
                    f"engine={options.source_analysis_engine} "
                    f"top={options.source_analysis_top} "
                    f"min_count={options.source_analysis_min_count} "
                    f"ngram_max={options.source_analysis_ngram_max}"
                ),
            ),
            SeriesStepResult(
                name="context-prefill",
                status="planned",
                message=(
                    "would prefill context questions from source analysis"
                    + (
                        " with imported-policy consolidation"
                        if options.consolidate_imported_policy
                        else ""
                    )
                ),
            ),
            SeriesStepResult(
                name="context-render",
                status="planned",
                message="would render context.md and stop for human review",
            ),
        ]
    )
    return SeriesPrepareResult(
        write=False,
        project_dir=options.book,
        profile=options.profile,
        context_ready=False,
        review_required=True,
        steps=steps,
        pack_path=pack_path,
        source_name=options.source_file.name,
        previous_context=previous_context,
        termbase_imported=(
            f"yes, {options.termbase_scope} scope" if options.write_termbase else "no"
        ),
        next_commands=_build_next_commands(options),
    )


def prepare_series_book(request: SeriesPrepareRequest) -> SeriesPrepareResult:
    options = _resolve_prepare_options(request)
    _validate_prepare_options(options)
    if not options.write:
        return _plan_prepare(options)

    steps: list[SeriesStepResult] = []
    pack_path = options.pack or _series_pack_path(options)
    previous_context = None
    if options.from_book is not None:
        previous_context = f"{options.from_book.name}/{options.from_profile}"
        previous_runtime = resolve_runtime(
            options.from_book,
            profile=options.from_profile,
            require_profile=True,
        )
        summary = export_context_pack_workflow(
            previous_runtime.project,
            previous_runtime,
            series_id=options.series_id,
            title=options.title,
            output=pack_path,
            questions="approved",
            no_style=False,
            no_global_rules=False,
            no_glossary=False,
            allow_not_ready=False,
            force=True,
        )
        steps.append(
            SeriesStepResult(
                name="pack",
                status="written",
                message=f"exported context pack from {previous_context}",
                path=str(summary["path"]),
            )
        )
    else:
        steps.append(
            SeriesStepResult(
                name="pack",
                status="skipped",
                message=f"using provided pack {options.pack}",
                path=str(options.pack),
            )
        )

    project = _maybe_load_existing_project(options.book)
    if project is None:
        project = init_source_project(
            options.book,
            source_language=options.source_lang,
            source_file=options.source_file,
        )
        steps.append(
            SeriesStepResult(
                name="project",
                status="written",
                message="initialized a new source project",
            )
        )
    else:
        _ensure_source_matches(project, options)
        steps.append(
            SeriesStepResult(
                name="project",
                status="skipped",
                message="reused the existing source project",
            )
        )

    if project.chunks() and not options.reextract:
        steps.append(
            SeriesStepResult(
                name="extract",
                status="skipped",
                message="reused existing extracted chunks",
            )
        )
    else:
        extract_result = extract_project_workflow(project, force_rechunk=False)
        steps.append(
            SeriesStepResult(
                name="extract",
                status="written",
                message=(
                    f"extracted {extract_result.chunk_count} chunk(s) and "
                    f"{extract_result.record_count} record(s)"
                ),
                path=(
                    str(extract_result.chapter_audit_report)
                    if extract_result.chapter_audit_report is not None
                    else None
                ),
            )
        )

    chapter_audit = audit_chapters_workflow(project)
    if chapter_audit.report_path is not None:
        steps.append(
            SeriesStepResult(
                name="chapter-audit",
                status="written",
                message=(
                    "wrote EPUB chapter audit"
                    if not chapter_audit.has_blocking_errors
                    else "wrote EPUB chapter audit with blocking errors"
                ),
                path=str(chapter_audit.report_path),
            )
        )
    else:
        steps.append(
            SeriesStepResult(
                name="chapter-audit",
                status="skipped",
                message="chapter audit skipped for non-EPUB source",
            )
        )
    if chapter_audit.has_blocking_errors:
        raise _err(
            "series_prepare_chapter_audit_failed",
            "EPUB chapter audit reported blocking findings; inspect "
            f"{chapter_audit.report_path} and fix the source before continuing",
        )

    profile_path = profile_dir(project, options.profile)
    if (profile_path / "config.toml").is_file():
        profile_project = load_profile_project(project.root, options.profile)
        conflicts = _profile_conflicts(profile_project, options)
        if conflicts:
            if not options.replace_profile:
                raise _err(
                    "series_prepare_profile_conflict",
                    "target profile exists with incompatible settings: "
                    + ", ".join(conflicts)
                    + ". Pass --replace-profile to recreate it.",
                )
            shutil.rmtree(profile_path, ignore_errors=False)
            profile_project = create_profile_workflow(
                project.root,
                options.profile,
                target_language=options.target,
                target_locale=options.target_locale,
                actor=options.actor,
                harness=options.harness,
                model=options.model,
            )
            steps.append(
                SeriesStepResult(
                    name="profile",
                    status="written",
                    message="recreated the target profile",
                )
            )
        else:
            steps.append(
                SeriesStepResult(
                    name="profile",
                    status="skipped",
                    message="reused the existing profile",
                )
            )
    else:
        profile_project = create_profile_workflow(
            project.root,
            options.profile,
            target_language=options.target,
            target_locale=options.target_locale,
            actor=options.actor,
            harness=options.harness,
            model=options.model,
        )
        steps.append(
            SeriesStepResult(
                name="profile",
                status="written",
                message="created the target profile",
            )
        )

    if load_context(profile_project) is None:
        init_context_workflow(
            profile_project,
            force=False,
            non_interactive=True,
            seed=None,
            seed_file=None,
        )
        steps.append(
            SeriesStepResult(
                name="context-init",
                status="written",
                message="initialized profile context",
                path=str(context_markdown_path(profile_project)),
            )
        )
    else:
        steps.append(
            SeriesStepResult(
                name="context-init",
                status="skipped",
                message="reused existing profile context",
                path=str(context_markdown_path(profile_project)),
            )
        )

    runtime = resolve_runtime(
        options.book, profile=options.profile, require_profile=True
    )
    _pack, preflight, _ = import_context_pack_workflow(
        runtime,
        file=pack_path,
        write=False,
        init_missing_context=True,
        conflict=options.conflict,
        write_termbase=False,
        termbase_scope=options.termbase_scope,
    )
    if context_pack_import_has_failures(preflight):
        raise _err(
            "series_prepare_import_conflict",
            "context import preflight reported conflicts or errors; resolve them "
            "with `booktx context import-pack ...` or adjust --conflict",
        )
    _pack, imported, _ = import_context_pack_workflow(
        runtime,
        file=pack_path,
        write=True,
        init_missing_context=True,
        conflict=options.conflict,
        write_termbase=options.write_termbase,
        termbase_scope=options.termbase_scope,
    )
    termbase_imported = (
        f"yes, {options.termbase_scope} scope" if options.write_termbase else "no"
    )
    steps.append(
        SeriesStepResult(
            name="context-import",
            status="written",
            message=(
                f"imported context pack ({imported.added} add, "
                f"{imported.updated} update, {imported.skipped} skip)"
            ),
            path=str(pack_path),
        )
    )

    analysis = analyze_source(
        project,
        engine_requested=options.source_analysis_engine,
        min_count=options.source_analysis_min_count,
        ngram_max=options.source_analysis_ngram_max,
        top=options.source_analysis_top,
        write=True,
        sync_profiles=True,
    )
    if analysis.failed_syncs:
        failed = ", ".join(item.profile for item in analysis.failed_syncs)
        raise _err(
            "series_prepare_source_analysis_sync_failed",
            "source-analysis snapshot sync failed for profile(s): " + failed,
        )
    steps.append(
        SeriesStepResult(
            name="source-analysis",
            status="written",
            message="wrote canonical source analysis and refreshed profile snapshots",
            path=str(project.booktx_dir / "source-analysis.json"),
        )
    )
    from booktx.source_analysis_context import clear_context_readiness, prefill_contexts

    prefill = prefill_contexts(
        project,
        analysis.report,
        profiles=[options.profile],
        write=True,
        include_advisory=options.include_advisory_prefill,
        gate_readiness=False,
        consolidate_imported_policy=options.consolidate_imported_policy,
    )
    if prefill.blocked:
        raise _err(
            "series_prepare_prefill_failed",
            "context prefill failed; inspect source-analysis prefill output",
        )
    steps.append(
        SeriesStepResult(
            name="context-prefill",
            status="written",
            message="prefilled context review questions from source analysis",
        )
    )

    profile_project = load_profile_project(project.root, options.profile)
    ctx = load_context_or_die(profile_project)
    clear_context_readiness(ctx)
    write_context(profile_project, ctx)
    render_context_command(
        profile_project,
        ctx,
        write=True,
        stdout=False,
        force_discard_md_only=False,
    )
    steps.append(
        SeriesStepResult(
            name="context-render",
            status="written",
            message="rendered context.md and left context not ready for review",
            path=str(context_markdown_path(profile_project)),
        )
    )

    report_json, report_md = _write_prepare_reports(
        options,
        steps=steps,
        pack_path=pack_path,
        source_name=find_source_file(project).name,
        previous_context=previous_context,
        termbase_imported=termbase_imported,
        chapter_audit_blocked=False,
    )
    steps.append(
        SeriesStepResult(
            name="reports",
            status="written",
            message="wrote series prepare reports",
            path=str(report_md),
        )
    )

    return SeriesPrepareResult(
        write=True,
        project_dir=options.book,
        profile=options.profile,
        context_ready=False,
        review_required=True,
        steps=steps,
        pack_path=pack_path,
        source_name=find_source_file(project).name,
        previous_context=previous_context,
        termbase_imported=termbase_imported,
        report_json=report_json,
        report_md=report_md,
        next_commands=_build_next_commands(options),
    )


__all__ = [
    "SERIES_RECIPE_SCHEMA",
    "SeriesPrepareRequest",
    "SeriesPrepareResult",
    "SeriesRecipeWriteOptions",
    "SeriesRecipeWriteResult",
    "build_series_recipe",
    "prepare_series_book",
]
