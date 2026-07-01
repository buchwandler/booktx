"""Tests for controlled same-book context sync across sibling profiles."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from booktx.cli import app
from booktx.config import create_profile, load_profile_project
from booktx.context import load_context
from booktx.context_sync import (
    ContextSyncError,
    apply_context_sync,
    discover_sync_targets,
    plan_context_sync,
)

runner = CliRunner(env={"COLUMNS": "120"})

DOC = """\
# One

The Wasp Empire has commenced its great war against the Lowlands.
"""

REQUIRED_ANSWERS = (
    ("Q001", "de-DE"),
    ("Q002", "balanced"),
    ("Q003", "neutral"),
    ("Q004", "natural dialogue"),
    ("Q005", "keep Apt names"),
    ("Q006", "translate world terms"),
    ("Q012", "error"),
)


def _make_project(tmp_path: Path) -> Path:
    project_dir = tmp_path / "book"
    src = tmp_path / "novel.md"
    src.write_text(DOC, encoding="utf-8")
    res = runner.invoke(
        app,
        [
            "init",
            str(project_dir),
            "--source-file",
            str(src),
            "--source-lang",
            "en",
        ],
    )
    assert res.exit_code == 0, res.output
    return project_dir


def _create_profiles(project_dir: Path) -> None:
    create_profile(project_dir, "de_source", target_language="de")
    create_profile(project_dir, "de_flash", target_language="de")
    create_profile(project_dir, "de_mimo", target_language="de")
    create_profile(project_dir, "fr_model", target_language="fr")
    create_profile(
        project_dir,
        "passthrough_en",
        target_language="en",
        kind="pass-through",
    )
    create_profile(project_dir, "de_selection", target_language="de", kind="selection")


def _ready_context(project_dir: Path, profile: str) -> None:
    res = runner.invoke(
        app,
        [
            "context",
            "init",
            str(project_dir),
            "--profile",
            profile,
            "--non-interactive",
        ],
    )
    assert res.exit_code == 0, res.output
    for qid, text in REQUIRED_ANSWERS:
        res = runner.invoke(
            app,
            [
                "context",
                "answer",
                str(project_dir),
                qid,
                "--profile",
                profile,
                "--text",
                text,
            ],
        )
        assert res.exit_code == 0, res.output
    res = runner.invoke(
        app, ["context", "mark-ready", str(project_dir), "--profile", profile]
    )
    assert res.exit_code == 0, res.output


def _set_term(
    project_dir: Path,
    profile: str,
    source: str,
    target: str,
    *forbidden: str,
) -> None:
    args = [
        "context",
        "reset-term",
        str(project_dir),
        source,
        "--profile",
        profile,
        "--target",
        target,
        "--create",
        "--enforce",
        "error",
    ]
    for value in forbidden:
        args.extend(["--forbid", value])
    res = runner.invoke(app, args)
    assert res.exit_code == 0, res.output


def _profile_context(project_dir: Path, profile: str):
    return load_context(load_profile_project(project_dir, profile))


def test_sync_discovers_same_language_targets_and_excludes_source_and_passthrough(
    tmp_path: Path,
):
    project_dir = _make_project(tmp_path)
    _create_profiles(project_dir)

    targets = discover_sync_targets(
        project_dir,
        source_profile="de_source",
        explicit_targets=[],
        all_compatible=True,
        same_locale=False,
        include_pass_through=False,
        include_selection=False,
    )

    assert targets == ["de_flash", "de_mimo"]


def test_sync_dry_run_does_not_mutate_contexts(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    _create_profiles(project_dir)
    _ready_context(project_dir, "de_source")
    _ready_context(project_dir, "de_flash")
    _set_term(project_dir, "de_source", "empire", "Imperium", "Reich")

    before = _profile_context(project_dir, "de_flash").model_dump(mode="json")

    plan = plan_context_sync(
        project_dir,
        source_profile="de_source",
        target_profiles=["de_flash"],
        all_compatible=False,
        sections={"glossary"},
        terms=[],
        question_ids=[],
        conflict="fail",
        same_locale=False,
        include_pass_through=False,
        include_selection=False,
        allow_not_ready=False,
        init_missing_context=False,
    )

    assert plan.blocked is False
    after = _profile_context(project_dir, "de_flash").model_dump(mode="json")
    assert after == before


def test_sync_selected_glossary_terms_only(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    _create_profiles(project_dir)
    _ready_context(project_dir, "de_source")
    _ready_context(project_dir, "de_flash")
    _set_term(project_dir, "de_source", "empire", "Imperium", "Reich")
    _set_term(project_dir, "de_source", "Lowlands", "Tieflande")

    plan = plan_context_sync(
        project_dir,
        source_profile="de_source",
        target_profiles=["de_flash"],
        all_compatible=False,
        sections={"glossary"},
        terms=["empire"],
        question_ids=[],
        conflict="fail",
        same_locale=False,
        include_pass_through=False,
        include_selection=False,
        allow_not_ready=False,
        init_missing_context=False,
    )
    apply_context_sync(plan, project_dir)

    ctx = _profile_context(project_dir, "de_flash")
    assert ctx is not None
    assert [entry.source for entry in ctx.glossary] == ["empire"]


def test_sync_missing_source_term_fails(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    _create_profiles(project_dir)
    _ready_context(project_dir, "de_source")
    _ready_context(project_dir, "de_flash")

    try:
        plan_context_sync(
            project_dir,
            source_profile="de_source",
            target_profiles=["de_flash"],
            all_compatible=False,
            sections={"glossary"},
            terms=["missing"],
            question_ids=[],
            conflict="fail",
            same_locale=False,
            include_pass_through=False,
            include_selection=False,
            allow_not_ready=False,
            init_missing_context=False,
        )
    except ContextSyncError as exc:
        assert exc.code == "sync_term_missing"
    else:  # pragma: no cover
        raise AssertionError("expected sync_term_missing")


def test_sync_conflict_fail_blocks_all_writes(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    _create_profiles(project_dir)
    for profile in ("de_source", "de_flash", "de_mimo"):
        _ready_context(project_dir, profile)
    _set_term(project_dir, "de_source", "empire", "Imperium", "Reich")
    _set_term(project_dir, "de_flash", "empire", "Kaiserreich")

    plan = plan_context_sync(
        project_dir,
        source_profile="de_source",
        target_profiles=["de_flash", "de_mimo"],
        all_compatible=False,
        sections={"glossary"},
        terms=["empire"],
        question_ids=[],
        conflict="fail",
        same_locale=False,
        include_pass_through=False,
        include_selection=False,
        allow_not_ready=False,
        init_missing_context=False,
    )

    assert plan.blocked is True
    assert plan.targets[0].conflicts == 1
    assert not _profile_context(project_dir, "de_mimo").glossary


def test_sync_conflict_replace_writes_all_nonblocked_targets(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    _create_profiles(project_dir)
    for profile in ("de_source", "de_flash", "de_mimo"):
        _ready_context(project_dir, profile)
    _set_term(project_dir, "de_source", "empire", "Imperium", "Reich")
    _set_term(project_dir, "de_flash", "empire", "Kaiserreich")

    plan = plan_context_sync(
        project_dir,
        source_profile="de_source",
        target_profiles=["de_flash", "de_mimo"],
        all_compatible=False,
        sections={"glossary"},
        terms=["empire"],
        question_ids=[],
        conflict="replace",
        same_locale=False,
        include_pass_through=False,
        include_selection=False,
        allow_not_ready=False,
        init_missing_context=False,
    )
    apply_context_sync(plan, project_dir)

    assert _profile_context(project_dir, "de_flash").glossary[0].target == "Imperium"
    assert _profile_context(project_dir, "de_mimo").glossary[0].target == "Imperium"


def test_sync_keep_local_preserves_target_decision(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    _create_profiles(project_dir)
    for profile in ("de_source", "de_flash", "de_mimo"):
        _ready_context(project_dir, profile)
    _set_term(project_dir, "de_source", "empire", "Imperium", "Reich")
    _set_term(project_dir, "de_flash", "empire", "Kaiserreich")

    plan = plan_context_sync(
        project_dir,
        source_profile="de_source",
        target_profiles=["de_flash", "de_mimo"],
        all_compatible=False,
        sections={"glossary"},
        terms=["empire"],
        question_ids=[],
        conflict="keep-local",
        same_locale=False,
        include_pass_through=False,
        include_selection=False,
        allow_not_ready=False,
        init_missing_context=False,
    )
    apply_context_sync(plan, project_dir)

    assert _profile_context(project_dir, "de_flash").glossary[0].target == "Kaiserreich"
    assert _profile_context(project_dir, "de_mimo").glossary[0].target == "Imperium"


def test_sync_clears_readiness_when_changed(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    _create_profiles(project_dir)
    _ready_context(project_dir, "de_source")
    _ready_context(project_dir, "de_flash")
    _set_term(project_dir, "de_source", "empire", "Imperium")

    plan = plan_context_sync(
        project_dir,
        source_profile="de_source",
        target_profiles=["de_flash"],
        all_compatible=False,
        sections={"glossary"},
        terms=["empire"],
        question_ids=[],
        conflict="fail",
        same_locale=False,
        include_pass_through=False,
        include_selection=False,
        allow_not_ready=False,
        init_missing_context=False,
    )
    apply_context_sync(plan, project_dir)

    assert _profile_context(project_dir, "de_flash").ready is False


def test_sync_preserves_readiness_when_noop(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    _create_profiles(project_dir)
    _ready_context(project_dir, "de_source")
    _ready_context(project_dir, "de_flash")
    _set_term(project_dir, "de_source", "empire", "Imperium")
    _set_term(project_dir, "de_flash", "empire", "Imperium")

    plan = plan_context_sync(
        project_dir,
        source_profile="de_source",
        target_profiles=["de_flash"],
        all_compatible=False,
        sections={"glossary"},
        terms=["empire"],
        question_ids=[],
        conflict="fail",
        same_locale=False,
        include_pass_through=False,
        include_selection=False,
        allow_not_ready=False,
        init_missing_context=False,
    )
    apply_context_sync(plan, project_dir)

    assert _profile_context(project_dir, "de_flash").ready is True


def test_sync_warns_for_existing_tasks(tmp_path: Path):
    project_dir = _make_project(tmp_path)
    _create_profiles(project_dir)
    assert runner.invoke(app, ["extract", str(project_dir)]).exit_code == 0
    for profile in ("de_source", "de_flash"):
        _ready_context(project_dir, profile)
    _set_term(project_dir, "de_source", "empire", "Imperium")
    task_res = runner.invoke(
        app,
        [
            "translate",
            "next",
            str(project_dir),
            "--profile",
            "de_flash",
            "--unit",
            "batch",
            "--max-words",
            "50",
            "--format",
            "block",
        ],
    )
    assert task_res.exit_code == 0, task_res.output

    plan = plan_context_sync(
        project_dir,
        source_profile="de_source",
        target_profiles=["de_flash"],
        all_compatible=False,
        sections={"glossary"},
        terms=["empire"],
        question_ids=[],
        conflict="fail",
        same_locale=False,
        include_pass_through=False,
        include_selection=False,
        allow_not_ready=False,
        init_missing_context=False,
    )

    warnings = [f for f in plan.targets[0].findings if f.action == "warning"]
    assert any("existing tasks" in f.message for f in warnings)
