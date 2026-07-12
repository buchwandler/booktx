"""CLI tests for profile-root isolated mode."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from booktx.cli import app

runner = CliRunner()

DOC = """\
# One

First sentence. Second sentence.
"""


def _make_project(tmp_path: Path) -> tuple[Path, Path]:
    src = tmp_path / "book.md"
    src.write_text(DOC, encoding="utf-8")
    project_dir = tmp_path / "book"
    init_res = runner.invoke(
        app,
        ["init", str(project_dir), "--target", "de", "--source-file", str(src)],
    )
    assert init_res.exit_code == 0, init_res.output
    create_res = runner.invoke(
        app,
        ["profile", "create", str(project_dir), "fr_default", "--target", "fr"],
    )
    assert create_res.exit_code == 0, create_res.output
    extract_res = runner.invoke(app, ["extract", str(project_dir)])
    assert extract_res.exit_code == 0, extract_res.output
    init_ctx = runner.invoke(
        app,
        [
            "context",
            "init",
            str(project_dir),
            "--profile",
            "de_default",
            "--non-interactive",
        ],
    )
    assert init_ctx.exit_code == 0, init_ctx.output
    ready_ctx = runner.invoke(
        app,
        [
            "context",
            "mark-ready",
            str(project_dir),
            "--profile",
            "de_default",
            "--force",
            "--reason",
            "test setup",
        ],
    )
    assert ready_ctx.exit_code == 0, ready_ctx.output
    profile_root = project_dir / "translations" / "de_default"
    return project_dir, profile_root


def test_whoami_from_profile_root_redacts_project_root(monkeypatch, tmp_path: Path):
    project_dir, profile_root = _make_project(tmp_path)
    monkeypatch.chdir(profile_root)

    res = runner.invoke(app, ["whoami", "."])

    assert res.exit_code == 0, res.output
    assert "booktx identity: ." in res.output
    assert "context-history" not in res.output
    assert "fr_default" not in res.output
    assert str(project_dir) not in res.output
    assert "../" not in res.output
    assert "READY context.json" in res.output


def test_status_from_profile_root_redacts_project_root(monkeypatch, tmp_path: Path):
    project_dir, profile_root = _make_project(tmp_path)
    monkeypatch.chdir(profile_root)

    res = runner.invoke(app, ["status", "."])

    assert res.exit_code == 0, res.output
    assert "booktx status — ." in res.output
    assert "fr_default" not in res.output
    assert str(project_dir) not in res.output
    assert "../" not in res.output


def test_context_status_from_profile_root_uses_profile_local_path(
    monkeypatch, tmp_path: Path
):
    project_dir, profile_root = _make_project(tmp_path)
    monkeypatch.chdir(profile_root)

    res = runner.invoke(app, ["context", "status", "."])

    assert res.exit_code == 0, res.output
    assert "context: context.md" in res.output
    assert "fr_default" not in res.output
    assert str(project_dir) not in res.output
    assert "../" not in res.output


def test_profile_commands_are_blocked_in_profile_root_mode(monkeypatch, tmp_path: Path):
    _, profile_root = _make_project(tmp_path)
    monkeypatch.chdir(profile_root)

    # profile list and profile show . are now allowed in isolated mode.
    # Other cross-profile commands remain blocked.
    blocked_args = (
        ["profile", "show", ".", "fr_default"],
        [
            "profile",
            "compare",
            ".",
            "--profiles",
            "de_default,fr_default",
            "--record",
            "0001-000001",
        ],
        ["pass-through", ".", "--profile", "passthrough_en"],
    )
    for args in blocked_args:
        res = runner.invoke(app, args)
        assert res.exit_code != 0
        assert "../" not in res.output
        assert str(profile_root.parent.parent) not in res.output


def test_context_sync_is_blocked_in_profile_root_mode(monkeypatch, tmp_path: Path):
    project_dir, profile_root = _make_project(tmp_path)
    monkeypatch.chdir(profile_root)

    res = runner.invoke(
        app,
        [
            "context",
            "sync",
            ".",
            "--from",
            "de_default",
            "--all-compatible",
        ],
    )

    assert res.exit_code != 0, res.output
    assert "project root" in res.output
    assert "../" not in res.output
    assert str(project_dir) not in res.output


def test_judge_admin_commands_blocked_in_profile_root(monkeypatch, tmp_path: Path):
    project_dir, profile_root = _make_project(tmp_path)
    # Create a selection profile so the profile_root isn't the only option
    from booktx.config import create_profile as cp

    cp(project_dir, "de_judge", target_language="de", kind="selection")
    monkeypatch.chdir(profile_root)
    # create-profile blocked
    res = runner.invoke(
        app,
        [
            "judge",
            "create-profile",
            ".",
            "de_extra",
            "--target",
            "de",
            "--sources",
            "de_default",
        ],
    )
    assert res.exit_code != 0
    assert "project root" in res.output or "not available" in res.output
    # sync-sources blocked (no --profile; auto-resolves to de_default profile root)
    res = runner.invoke(app, ["judge", "sync-sources", "."])
    assert res.exit_code != 0
    assert "project root" in res.output or "not available" in res.output
    # prepare-isolation blocked (use correct profile for runtime resolution)
    res = runner.invoke(
        app, ["judge", "prepare-isolation", ".", "--profile", "de_default"]
    )
    assert res.exit_code != 0
    assert "project root" in res.output or "not available" in res.output
    assert "../" not in res.output
    assert str(project_dir) not in res.output


def test_judge_allowed_commands_in_profile_root(monkeypatch, tmp_path: Path):
    """judge status/next/insert are allowed for selection profiles in profile-root."""
    project_dir, profile_root = _make_project(tmp_path)
    from booktx.config import (
        create_profile as cp,
    )
    from booktx.config import (
        load_profile_config as lpc,
    )
    from booktx.config import (
        write_profile_config as wp,
    )
    from booktx.models import SelectionConfig

    cp(project_dir, "de_judge", target_language="de", kind="selection")
    cfg = lpc(project_dir, "de_judge")
    cfg.selection = SelectionConfig(sources=["de_default"])
    wp(project_dir, cfg)
    # The de_judge profile root has no snapshot yet; status should still work
    # and report missing snapshot without leaking paths.
    judge_root = project_dir / "translations" / "de_judge"
    monkeypatch.chdir(judge_root)
    res = runner.invoke(app, ["judge", "status", "."])
    # status succeeds even without snapshot (reports missing)
    assert "../" not in res.output
    assert str(project_dir) not in res.output
    assert "translations/de_judge" not in res.output
    assert "--profile" not in res.output
    # snapshot is missing so next fails; but not with "project root" blocker
    res2 = runner.invoke(
        app, ["judge", "next", ".", "--unit", "chapter", "--chapter", "0001"]
    )
    if res2.exit_code != 0:
        # Should fail with snapshot/context issue, not isolation blocker
        assert "not available in profile-root" not in res2.output
        assert "../" not in res2.output
        assert str(project_dir) not in res2.output


def test_profile_list_from_profile_root_shows_current_profile_only(
    monkeypatch, tmp_path: Path
):
    project_dir, profile_root = _make_project(tmp_path)
    monkeypatch.chdir(profile_root)
    res = runner.invoke(app, ["profile", "list", "."])
    assert res.exit_code == 0, res.output
    assert "de_default" in res.output
    assert "fr_default" not in res.output
    assert str(project_dir) not in res.output
    assert "../" not in res.output
    json_res = runner.invoke(app, ["profile", "list", ".", "--json"])
    assert json_res.exit_code == 0, json_res.output
    assert "fr_default" not in json_res.output
    assert str(project_dir) not in json_res.output
    assert "translations/de_default" not in json_res.output


def test_profile_show_current_from_profile_root_defaults_to_current(
    monkeypatch, tmp_path: Path
):
    project_dir, profile_root = _make_project(tmp_path)
    monkeypatch.chdir(profile_root)
    res = runner.invoke(app, ["profile", "show", ".", "."])
    assert res.exit_code == 0, res.output
    assert "de_default" in res.output
    assert str(project_dir) not in res.output
    assert "../" not in res.output
    assert "translations/de_default" not in res.output


def test_profile_show_other_from_profile_root_is_blocked(monkeypatch, tmp_path: Path):
    project_dir, profile_root = _make_project(tmp_path)
    monkeypatch.chdir(profile_root)
    res = runner.invoke(app, ["profile", "show", ".", "fr_default"])
    assert res.exit_code != 0, res.output
    assert "../" not in res.output
    assert str(project_dir) not in res.output


def test_mode_source_and_doctor_commands_work_from_profile_root(
    monkeypatch, tmp_path: Path
):
    project_dir, profile_root = _make_project(tmp_path)
    monkeypatch.chdir(profile_root)

    mode_res = runner.invoke(app, ["mode", "."])
    status_res = runner.invoke(app, ["source", "status", "."])
    record_res = runner.invoke(app, ["source", "record", ".", "0001-000001"])
    chapter_res = runner.invoke(
        app, ["source", "chapter", ".", "0001", "--format", "block"]
    )
    doctor_res = runner.invoke(app, ["doctor", "isolation", "."])

    assert mode_res.exit_code == 0, mode_res.output
    assert "mode: profile-root" in mode_res.output
    assert "profiles visible: no" in mode_res.output
    assert "cross-profile access: no" in mode_res.output
    assert "safe for model evaluation: yes" in mode_res.output

    assert status_res.exit_code == 0, status_res.output
    assert "source: available" in status_res.output
    assert ".booktx/chunks" not in status_res.output

    assert record_res.exit_code == 0, record_res.output
    assert ">>> 0001-000001" in record_res.output
    assert str(project_dir) not in record_res.output
    assert "../" not in record_res.output

    assert chapter_res.exit_code == 0, chapter_res.output
    assert ">>> 0001-000001" in chapter_res.output
    assert str(project_dir) not in chapter_res.output
    assert "../" not in chapter_res.output

    assert doctor_res.exit_code == 0, doctor_res.output
    assert "isolation: PASS" in doctor_res.output
    assert "mode: profile-root" in doctor_res.output
    assert "cross-profile commands: blocked" in doctor_res.output
    assert "path redaction: PASS" in doctor_res.output


def test_translate_next_from_profile_root_keeps_output_and_artifacts_local(
    monkeypatch, tmp_path: Path
):
    project_dir, profile_root = _make_project(tmp_path)
    monkeypatch.chdir(profile_root)

    res = runner.invoke(
        app,
        [
            "translate",
            "next",
            ".",
            "--unit",
            "batch",
            "--max-words",
            "20",
            "--json",
        ],
    )

    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["ingest_path"].startswith("ingest/")
    assert payload["block_ingest_path"].startswith("ingest/")
    assert payload["source_block_path"].startswith("tasks/")
    assert payload["context_view_path"].startswith("context-history/")
    assert "translations/" not in res.output
    assert "fr_default" not in res.output
    assert "../" not in res.output
    assert str(project_dir) not in res.output

    artifact_paths = [
        profile_root / "tasks" / f"{payload['task_id']}.json",
        profile_root / "tasks" / f"{payload['task_id']}.source.block.txt",
        profile_root / "ingest" / f"{payload['task_id']}.json",
        profile_root / "ingest" / f"{payload['task_id']}.block.txt",
    ]
    for artifact in artifact_paths:
        assert artifact.is_file()
        text = artifact.read_text("utf-8")
        assert str(project_dir) not in text
        assert "../" not in text
        assert ".booktx/chunks" not in text
        assert "fr_default" not in text


def test_validate_and_build_work_from_profile_root_without_path_leaks(
    monkeypatch, tmp_path: Path
):
    _, profile_root = _make_project(tmp_path)
    monkeypatch.chdir(profile_root)

    next_res = runner.invoke(
        app,
        ["translate", "next", ".", "--unit", "batch", "--max-words", "20", "--json"],
    )
    assert next_res.exit_code == 0, next_res.output
    payload = json.loads(next_res.output)
    ingest_path = profile_root / payload["ingest_path"]
    template = json.loads(ingest_path.read_text("utf-8"))
    template["records"] = [
        {"id": record["id"], "target": record["source"]}
        for record in payload["records"]
    ]
    ingest_path.write_text(json.dumps(template), encoding="utf-8")

    insert_res = runner.invoke(
        app,
        [
            "translate",
            "insert",
            ".",
            "--task-id",
            payload["task_id"],
            "--json-file",
            payload["ingest_path"],
        ],
    )
    assert insert_res.exit_code == 0, insert_res.output

    validate_res = runner.invoke(app, ["validate", "."])
    build_res = runner.invoke(app, ["build", "."])

    assert validate_res.exit_code == 0, validate_res.output
    assert "report: reports/" in validate_res.output
    assert "../" not in validate_res.output
    assert "/tmp/" not in validate_res.output

    assert build_res.exit_code == 0, build_res.output
    assert "output/" in build_res.output
    assert "../" not in build_res.output
    assert "/tmp/" not in build_res.output


def test_profile_root_mode_does_not_leak_sibling_profile_translations(
    monkeypatch, tmp_path: Path
):
    project_dir, profile_root = _make_project(tmp_path)
    sibling_ctx_init = runner.invoke(
        app,
        [
            "context",
            "init",
            str(project_dir),
            "--profile",
            "fr_default",
            "--non-interactive",
        ],
    )
    assert sibling_ctx_init.exit_code == 0, sibling_ctx_init.output
    sibling_ctx_ready = runner.invoke(
        app,
        [
            "context",
            "mark-ready",
            str(project_dir),
            "--profile",
            "fr_default",
            "--force",
            "--reason",
            "test setup",
        ],
    )
    assert sibling_ctx_ready.exit_code == 0, sibling_ctx_ready.output

    sibling_next = runner.invoke(
        app,
        [
            "translate",
            "next",
            str(project_dir),
            "--profile",
            "fr_default",
            "--unit",
            "batch",
            "--max-words",
            "20",
            "--json",
        ],
    )
    assert sibling_next.exit_code == 0, sibling_next.output
    sibling_payload = json.loads(sibling_next.output)
    sibling_ingest = project_dir / sibling_payload["ingest_path"]
    sibling_template = json.loads(sibling_ingest.read_text("utf-8"))
    sibling_template["records"] = [
        {"id": record["id"], "target": f"LEAK-CHECK-{idx}"}
        for idx, record in enumerate(sibling_payload["records"], start=1)
    ]
    sibling_ingest.write_text(json.dumps(sibling_template), encoding="utf-8")
    sibling_insert = runner.invoke(
        app,
        [
            "translate",
            "insert",
            str(project_dir),
            "--profile",
            "fr_default",
            "--task-id",
            sibling_payload["task_id"],
            "--json-file",
            str(sibling_ingest),
        ],
    )
    assert sibling_insert.exit_code == 0, sibling_insert.output

    monkeypatch.chdir(profile_root)

    next_res = runner.invoke(
        app,
        ["translate", "next", ".", "--unit", "batch", "--max-words", "20", "--json"],
    )
    list_res = runner.invoke(app, ["translate", "list", ".", "--chapter", "1"])

    assert next_res.exit_code == 0, next_res.output
    assert list_res.exit_code == 0, list_res.output
    assert "LEAK-CHECK" not in next_res.output
    assert "LEAK-CHECK" not in list_res.output
    assert "fr_default" not in next_res.output
    assert "fr_default" not in list_res.output


def test_todo_next_from_profile_root_keeps_paths_and_commands_local(
    monkeypatch, tmp_path: Path
):
    project_dir, profile_root = _make_project(tmp_path)
    monkeypatch.chdir(profile_root)

    res = runner.invoke(
        app,
        [
            "translate",
            "todo-next",
            ".",
            "--chapters",
            "1",
            "--batch-words",
            "20",
            "--write",
        ],
    )

    assert res.exit_code == 0, res.output
    assert "markdown: todos/" in res.output
    assert "json: todos/" in res.output
    assert "--profile" not in res.output
    assert "translations/" not in res.output
    assert str(project_dir) not in res.output
    assert "../" not in res.output

    todo_md = next((profile_root / "todos").glob("*.md"))
    text = todo_md.read_text("utf-8")
    assert "booktx translate todo-status . --todo-id" in text
    assert "booktx translate todo-resume . --todo-id" in text
    assert "--profile" not in text
    assert "translations/" not in text
    assert str(project_dir) not in text
    assert "../" not in text


def test_todo_resume_from_profile_root_writes_local_task_and_submit_hints(
    monkeypatch, tmp_path: Path
):
    project_dir, profile_root = _make_project(tmp_path)
    monkeypatch.chdir(profile_root)

    create = runner.invoke(
        app,
        [
            "translate",
            "todo-next",
            ".",
            "--chapters",
            "1",
            "--batch-words",
            "20",
            "--write",
            "--json",
        ],
    )
    assert create.exit_code == 0, create.output
    todo_id = json.loads(create.output)["todo_id"]

    resume = runner.invoke(
        app,
        ["translate", "todo-resume", ".", "--todo-id", todo_id, "--format", "block"],
    )
    assert resume.exit_code == 0, resume.output
    assert "Source file: tasks/" in resume.output
    assert "Durable block template: ingest/" in resume.output
    assert (
        "Submit durable file with: booktx translate insert . --task-id" in resume.output
    )
    assert " --file ingest/" in resume.output
    assert "--profile" not in resume.output
    assert "translations/" not in resume.output
    assert str(project_dir) not in resume.output
    assert "../" not in resume.output

    block = next((profile_root / "ingest").glob("*.block.txt"))
    text = block.read_text("utf-8")
    assert "# source: tasks/" in text
    assert "# submit: booktx translate insert . --task-id" in text
    assert " --file ingest/" in text
    assert "--profile" not in text
    assert "translations/" not in text
    assert str(project_dir) not in text
    assert "../" not in text


def test_todo_status_from_profile_root_uses_local_next_command(
    monkeypatch, tmp_path: Path
):
    project_dir, profile_root = _make_project(tmp_path)
    monkeypatch.chdir(profile_root)

    create = runner.invoke(
        app,
        [
            "translate",
            "todo-next",
            ".",
            "--chapters",
            "1",
            "--batch-words",
            "20",
            "--write",
            "--json",
        ],
    )
    assert create.exit_code == 0, create.output
    todo_id = json.loads(create.output)["todo_id"]

    status = runner.invoke(app, ["translate", "todo-status", ".", "--todo-id", todo_id])
    assert status.exit_code == 0, status.output
    assert "next: booktx translate todo-resume . --todo-id" in status.output
    assert "--profile" not in status.output
    assert "translations/" not in status.output
    assert str(project_dir) not in status.output
    assert "../" not in status.output


# ---------------------------------------------------------------------------
# Phase 0 isolation: commands fixed/tested in Phase 0 must stay redacted when
# invoked from profile-root mode. Output must never include the parent project
# path, an absolute path, a sibling profile name, or "../".
# ---------------------------------------------------------------------------


def _assert_no_leak(res, project_dir, sibling="fr_default"):
    assert res.exit_code == 0, res.output
    assert "../" not in res.output
    assert str(project_dir) not in res.output
    assert sibling not in res.output


def test_review_configure_show_isolated_from_profile_root(monkeypatch, tmp_path: Path):
    project_dir, profile_root = _make_project(tmp_path)
    # Enable quality review for the active (de_default) profile.
    enable = runner.invoke(
        app,
        [
            "review",
            "configure",
            str(project_dir),
            "--profile",
            "de_default",
            "--enable",
            "--pass",
            "1",
        ],
    )
    assert enable.exit_code == 0, enable.output
    monkeypatch.chdir(profile_root)
    res = runner.invoke(app, ["review", "configure", ".", "--show"])
    _assert_no_leak(res, project_dir)


def test_review_status_isolated_from_profile_root(monkeypatch, tmp_path: Path):
    project_dir, profile_root = _make_project(tmp_path)
    enable = runner.invoke(
        app,
        [
            "review",
            "configure",
            str(project_dir),
            "--profile",
            "de_default",
            "--enable",
            "--pass",
            "1",
        ],
    )
    assert enable.exit_code == 0, enable.output
    monkeypatch.chdir(profile_root)
    res = runner.invoke(app, ["review", "status", "."])
    _assert_no_leak(res, project_dir)


def test_translate_search_isolated_from_profile_root(monkeypatch, tmp_path: Path):
    project_dir, profile_root = _make_project(tmp_path)
    monkeypatch.chdir(profile_root)
    # search returns 0 with "found N matches"; output must stay redacted.
    res = runner.invoke(app, ["translate", "search", ".", "--source", "First"])
    _assert_no_leak(res, project_dir)


def test_epub_inspect_grep_extract_isolated_from_profile_root(
    monkeypatch, tmp_path: Path
):
    from booktx.config import load_project

    project_dir, profile_root = _make_project(tmp_path)
    # Populate the profile-local output dir with xhtml.
    proj = load_project(project_dir, profile="de_default")
    assert proj.output_dir is not None
    proj.output_dir.mkdir(parents=True, exist_ok=True)
    (proj.output_dir / "chapter_1.xhtml").write_text(
        "<html><body><p>Alice ran fast.</p></body></html>", encoding="utf-8"
    )
    monkeypatch.chdir(profile_root)
    for args in (
        ["epub", "inspect", "."],
        ["epub", "grep", ".", "Alice"],
        ["epub", "extract-text", "."],
    ):
        res = runner.invoke(app, args)
        _assert_no_leak(res, project_dir)


# ---------------------------------------------------------------------------
# booktx agents write / status / clean
# ---------------------------------------------------------------------------


AGENTS = "AGENTS.md"


def _agents_file(project_dir: Path, profile: str | None = None) -> Path:
    if profile is None:
        return project_dir / AGENTS
    return project_dir / "translations" / profile / AGENTS


def _assert_isolated_content_safe(path: Path, project_dir: Path) -> None:
    """The generated isolated file must never leak parent/sibling identity."""
    text = path.read_text("utf-8")
    assert "../" not in text
    assert "translations/" not in text
    assert "--profile" not in text
    assert "fr_default" not in text  # sibling profile name
    assert str(project_dir) not in text


def _seed_isolated_agents(project_dir: Path, profile: str) -> Path:
    """Write a managed isolated AGENTS.md directly, bypassing cross-profile cleanup.

    Sequential `agents write --mode isolated` calls remove each other's files, so
    tests that need several managed profile files present at once seed them directly.
    """
    from booktx.agents_md import render_agents_md, write_managed_agents_md
    from booktx.config import (
        load_profile_config,
        load_source_project,
        profile_dir,
        project_source_id_or_unavailable,
    )

    source_project = load_source_project(project_dir)
    source_id = project_source_id_or_unavailable(source_project)
    cfg = load_profile_config(source_project, profile)
    target = profile_dir(project_dir, profile) / AGENTS
    text = render_agents_md(
        mode="isolated",
        profile=profile,
        source_id=source_id,
        target_locale=cfg.target_locale or cfg.target_language,
    )
    write_managed_agents_md(target, text)
    return target


def test_agents_write_isolated_from_project_root(tmp_path: Path):
    project_dir, _ = _make_project(tmp_path)
    # Seed a managed collaborative root file to prove it is removed.
    collab = runner.invoke(
        app, ["agents", "write", str(project_dir), "--mode", "collaborative"]
    )
    assert collab.exit_code == 0, collab.output
    assert _agents_file(project_dir).is_file()

    res = runner.invoke(
        app,
        [
            "agents",
            "write",
            str(project_dir),
            "--mode",
            "isolated",
            "--profile",
            "de_default",
        ],
    )
    assert res.exit_code == 0, res.output
    target = _agents_file(project_dir, "de_default")
    assert target.is_file()
    # Managed project-root AGENTS.md is removed.
    assert not _agents_file(project_dir).exists()
    _assert_isolated_content_safe(target, project_dir)


def test_agents_write_isolated_from_profile_root(monkeypatch, tmp_path: Path):
    project_dir, profile_root = _make_project(tmp_path)
    monkeypatch.chdir(profile_root)
    res = runner.invoke(app, ["agents", "write", ".", "--mode", "isolated"])
    assert res.exit_code == 0, res.output
    target = profile_root / AGENTS
    assert target.is_file()
    assert "written: AGENTS.md" in res.output
    assert str(project_dir) not in res.output
    assert "../" not in res.output
    assert "translations/" not in res.output
    _assert_isolated_content_safe(target, project_dir)


def test_agents_write_collaborative_from_project_root_removes_profile_files(
    tmp_path: Path,
):
    project_dir, _ = _make_project(tmp_path)
    # Seed managed isolated files in both profiles directly (sequential writes
    # would otherwise remove each other's files).
    _seed_isolated_agents(project_dir, "de_default")
    _seed_isolated_agents(project_dir, "fr_default")
    assert _agents_file(project_dir, "de_default").is_file()
    assert _agents_file(project_dir, "fr_default").is_file()

    res = runner.invoke(
        app, ["agents", "write", str(project_dir), "--mode", "collaborative"]
    )
    assert res.exit_code == 0, res.output
    assert _agents_file(project_dir).is_file()
    # All managed profile-local files are removed.
    assert not _agents_file(project_dir, "de_default").exists()
    assert not _agents_file(project_dir, "fr_default").exists()


def test_agents_write_collaborative_from_profile_root_rejected_sanitized(
    monkeypatch, tmp_path: Path
):
    project_dir, profile_root = _make_project(tmp_path)
    monkeypatch.chdir(profile_root)
    res = runner.invoke(app, ["agents", "write", ".", "--mode", "collaborative"])
    assert res.exit_code != 0
    assert "../" not in res.output
    assert str(project_dir) not in res.output
    assert "translations/" not in res.output


def test_agents_write_unmanaged_target_requires_replace_flag(tmp_path: Path):
    project_dir, _ = _make_project(tmp_path)

    # Collaborative root target, unmanaged.
    root_file = _agents_file(project_dir)
    root_file.write_text("# user harness file\n", encoding="utf-8")
    blocked = runner.invoke(
        app, ["agents", "write", str(project_dir), "--mode", "collaborative"]
    )
    assert blocked.exit_code != 0
    assert root_file.read_text() == "# user harness file\n"
    replaced = runner.invoke(
        app,
        [
            "agents",
            "write",
            str(project_dir),
            "--mode",
            "collaborative",
            "--replace-unmanaged",
        ],
    )
    assert replaced.exit_code == 0, replaced.output
    assert "<!-- booktx-agents-md" in root_file.read_text("utf-8")

    # Isolated profile target, unmanaged, with no ancestor conflict.
    profile_file = _agents_file(project_dir, "de_default")
    profile_file.parent.mkdir(parents=True, exist_ok=True)
    profile_file.write_text("# user profile file\n", encoding="utf-8")
    # Remove the now-managed root file so there is no ancestor conflict.
    root_file.unlink()
    blocked_p = runner.invoke(
        app,
        [
            "agents",
            "write",
            str(project_dir),
            "--mode",
            "isolated",
            "--profile",
            "de_default",
        ],
    )
    assert blocked_p.exit_code != 0
    assert profile_file.read_text() == "# user profile file\n"
    replaced_p = runner.invoke(
        app,
        [
            "agents",
            "write",
            str(project_dir),
            "--mode",
            "isolated",
            "--profile",
            "de_default",
            "--replace-unmanaged",
        ],
    )
    assert replaced_p.exit_code == 0, replaced_p.output
    assert "<!-- booktx-agents-md" in profile_file.read_text("utf-8")


def test_agents_collaborative_write_skips_unmanaged_profile_file(tmp_path: Path):
    project_dir, _ = _make_project(tmp_path)
    sibling = _agents_file(project_dir, "fr_default")
    sibling.parent.mkdir(parents=True, exist_ok=True)
    sibling.write_text("# user file in sibling profile\n", encoding="utf-8")

    res = runner.invoke(
        app, ["agents", "write", str(project_dir), "--mode", "collaborative"]
    )
    assert res.exit_code == 0, res.output
    assert "skipped" in res.output
    # The unmanaged sibling file is left untouched.
    assert sibling.read_text() == "# user file in sibling profile\n"
    assert _agents_file(project_dir).is_file()


def test_agents_status_project_root_json_uses_project_relative_paths(tmp_path: Path):
    import json as _json

    project_dir, _ = _make_project(tmp_path)
    runner.invoke(
        app,
        [
            "agents",
            "write",
            str(project_dir),
            "--mode",
            "isolated",
            "--profile",
            "de_default",
        ],
    )
    res = runner.invoke(app, ["agents", "status", str(project_dir), "--json"])
    assert res.exit_code == 0, res.output
    payload = _json.loads(res.output)
    by_path = {entry["path"]: entry for entry in payload}
    assert "AGENTS.md" in by_path  # project-root entry
    assert by_path["AGENTS.md"]["scope"] == "project"
    assert "translations/de_default/AGENTS.md" in by_path
    de = by_path["translations/de_default/AGENTS.md"]
    assert de["scope"] == "profile"
    assert de["profile"] == "de_default"
    assert de["state"] == "managed-valid"
    assert de["mode"] == "isolated"
    assert de["source_id"].startswith("sha256:")
    assert de["stale"] is False
    # No absolute paths leak into JSON.
    assert str(project_dir) not in res.output


def test_agents_status_profile_root_reports_only_local(monkeypatch, tmp_path: Path):
    import json as _json

    project_dir, profile_root = _make_project(tmp_path)
    runner.invoke(
        app,
        [
            "agents",
            "write",
            str(project_dir),
            "--mode",
            "isolated",
            "--profile",
            "de_default",
        ],
    )
    monkeypatch.chdir(profile_root)

    human = runner.invoke(app, ["agents", "status", "."])
    assert human.exit_code == 0, human.output
    assert "AGENTS.md (profile de_default)" in human.output
    assert "fr_default" not in human.output
    assert "project)" not in human.output
    assert str(project_dir) not in human.output
    assert "../" not in human.output
    assert "translations/" not in human.output

    json_res = runner.invoke(app, ["agents", "status", ".", "--json"])
    assert json_res.exit_code == 0, json_res.output
    payload = _json.loads(json_res.output)
    assert len(payload) == 1
    entry = payload[0]
    assert entry["path"] == "AGENTS.md"
    assert entry["scope"] == "profile"
    assert entry["profile"] == "de_default"
    assert "fr_default" not in json_res.output
    assert "translations/" not in json_res.output
    assert str(project_dir) not in json_res.output


def test_agents_clean_matrix(tmp_path: Path):
    project_dir, _ = _make_project(tmp_path)

    # Seed managed root (collaborative) + isolated files for both profiles directly.
    runner.invoke(app, ["agents", "write", str(project_dir), "--mode", "collaborative"])
    _seed_isolated_agents(project_dir, "de_default")
    _seed_isolated_agents(project_dir, "fr_default")
    assert _agents_file(project_dir).is_file()
    assert _agents_file(project_dir, "de_default").is_file()
    assert _agents_file(project_dir, "fr_default").is_file()

    # --mode collaborative cleans only the collaborative root file.
    collab_clean = runner.invoke(
        app, ["agents", "clean", str(project_dir), "--mode", "collaborative"]
    )
    assert collab_clean.exit_code == 0, collab_clean.output
    assert not _agents_file(project_dir).exists()
    assert _agents_file(project_dir, "de_default").is_file()

    # --mode isolated --profile de_default cleans only that profile file.
    iso_clean = runner.invoke(
        app,
        [
            "agents",
            "clean",
            str(project_dir),
            "--mode",
            "isolated",
            "--profile",
            "de_default",
        ],
    )
    assert iso_clean.exit_code == 0, iso_clean.output
    assert not _agents_file(project_dir, "de_default").exists()
    assert _agents_file(project_dir, "fr_default").is_file()

    # --mode all --profile is rejected as ambiguous.
    amb = runner.invoke(
        app,
        [
            "agents",
            "clean",
            str(project_dir),
            "--mode",
            "all",
            "--profile",
            "de_default",
        ],
    )
    assert amb.exit_code != 0

    # --mode collaborative --profile is rejected.
    cp = runner.invoke(
        app,
        [
            "agents",
            "clean",
            str(project_dir),
            "--mode",
            "collaborative",
            "--profile",
            "de_default",
        ],
    )
    assert cp.exit_code != 0

    # --mode all removes every remaining managed file.
    all_clean = runner.invoke(
        app, ["agents", "clean", str(project_dir), "--mode", "all"]
    )
    assert all_clean.exit_code == 0, all_clean.output
    assert not _agents_file(project_dir, "fr_default").exists()

    # Absent files are a successful no-op (idempotent).
    again = runner.invoke(app, ["agents", "clean", str(project_dir), "--mode", "all"])
    assert again.exit_code == 0, again.output
    assert "deleted: (none)" in again.output

    # Mode filtering: clean --mode collaborative never touches profile files,
    # even when they are managed with a different mode.
    _seed_isolated_agents(project_dir, "de_default")
    wrong = runner.invoke(
        app, ["agents", "clean", str(project_dir), "--mode", "collaborative"]
    )
    assert wrong.exit_code == 0, wrong.output
    assert _agents_file(project_dir, "de_default").is_file()


def test_agents_clean_profile_root_matrix(monkeypatch, tmp_path: Path):
    project_dir, profile_root = _make_project(tmp_path)
    monkeypatch.chdir(profile_root)
    runner.invoke(
        app,
        [
            "agents",
            "write",
            str(project_dir),
            "--mode",
            "isolated",
            "--profile",
            "de_default",
        ],
    )
    monkeypatch.chdir(profile_root)

    # isolated clean deletes the local file.
    iso = runner.invoke(app, ["agents", "clean", ".", "--mode", "isolated"])
    assert iso.exit_code == 0, iso.output
    assert not (profile_root / AGENTS).exists()

    # collaborative clean from profile root is rejected, sanitized.
    runner.invoke(
        app,
        [
            "agents",
            "write",
            str(project_dir),
            "--mode",
            "isolated",
            "--profile",
            "de_default",
        ],
    )
    monkeypatch.chdir(profile_root)
    collab = runner.invoke(app, ["agents", "clean", ".", "--mode", "collaborative"])
    assert collab.exit_code != 0
    assert str(project_dir) not in collab.output
    assert "../" not in collab.output

    # --profile is rejected in profile root.
    prof = runner.invoke(
        app, ["agents", "clean", ".", "--mode", "isolated", "--profile", "de_default"]
    )
    assert prof.exit_code != 0
    assert str(project_dir) not in prof.output


def test_agents_isolated_blocked_by_unmanaged_ancestor(tmp_path: Path, monkeypatch):
    project_dir, profile_root = _make_project(tmp_path)
    # Unmanaged project-root AGENTS.md blocks isolated preparation.
    _agents_file(project_dir).write_text("# user root file\n", encoding="utf-8")
    res = runner.invoke(
        app,
        [
            "agents",
            "write",
            str(project_dir),
            "--mode",
            "isolated",
            "--profile",
            "de_default",
        ],
    )
    assert res.exit_code != 0
    assert _agents_file(project_dir).read_text() == "# user root file\n"
    assert not _agents_file(project_dir, "de_default").exists()

    # From the profile root, the same conflict is reported without leaking parent.
    monkeypatch.chdir(profile_root)
    res2 = runner.invoke(app, ["agents", "write", ".", "--mode", "isolated"])
    assert res2.exit_code != 0
    assert str(project_dir) not in res2.output
    assert "../" not in res2.output
    assert "translations/" not in res2.output


def test_agents_write_is_idempotent(tmp_path: Path):
    project_dir, _ = _make_project(tmp_path)
    for _ in range(3):
        res = runner.invoke(
            app,
            [
                "agents",
                "write",
                str(project_dir),
                "--mode",
                "isolated",
                "--profile",
                "de_default",
            ],
        )
        assert res.exit_code == 0, res.output
    target = _agents_file(project_dir, "de_default")
    assert target.is_file()
    assert not _agents_file(project_dir).exists()
    assert not _agents_file(project_dir, "fr_default").exists()


def test_agents_target_write_failure_performs_no_cleanup(monkeypatch, tmp_path: Path):
    project_dir, _ = _make_project(tmp_path)
    # Seed a managed collaborative root file plus a managed sibling profile file.
    # Direct seeding avoids isolated-write cleanup removing the root file.
    runner.invoke(app, ["agents", "write", str(project_dir), "--mode", "collaborative"])
    _seed_isolated_agents(project_dir, "fr_default")
    root_before = _agents_file(project_dir).read_text("utf-8")
    sibling_before = _agents_file(project_dir, "fr_default").read_text("utf-8")

    def boom(*args, **kwargs):
        raise RuntimeError("simulated target write failure")

    monkeypatch.setattr("booktx.workflows.agents.write_managed_agents_md", boom)
    res = runner.invoke(
        app,
        [
            "agents",
            "write",
            str(project_dir),
            "--mode",
            "isolated",
            "--profile",
            "de_default",
        ],
    )
    assert res.exit_code != 0
    # No cleanup ran: the seeded managed files are untouched.
    assert _agents_file(project_dir).read_text("utf-8") == root_before
    assert _agents_file(project_dir, "fr_default").read_text("utf-8") == sibling_before


def test_agents_cleanup_failure_is_recoverable(monkeypatch, tmp_path: Path):
    project_dir, _ = _make_project(tmp_path)
    # Seed a managed collaborative root file that isolated cleanup must remove.
    runner.invoke(app, ["agents", "write", str(project_dir), "--mode", "collaborative"])

    import booktx.workflows.agents as wagents

    real = wagents.delete_managed_agents_md
    calls = {"n": 0}

    def flaky_delete(path, *, expected_mode=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulated cleanup failure")
        return real(path, expected_mode=expected_mode)

    monkeypatch.setattr(wagents, "delete_managed_agents_md", flaky_delete)
    res = runner.invoke(
        app,
        [
            "agents",
            "write",
            str(project_dir),
            "--mode",
            "isolated",
            "--profile",
            "de_default",
        ],
    )
    # Cleanup failed -> nonzero, but the new target was written.
    assert res.exit_code != 0
    assert _agents_file(project_dir, "de_default").is_file()
    # The root file was not deleted yet.
    assert _agents_file(project_dir).is_file()

    # Rerun without the flaky patch completes cleanup.
    monkeypatch.undo()
    res2 = runner.invoke(
        app,
        [
            "agents",
            "write",
            str(project_dir),
            "--mode",
            "isolated",
            "--profile",
            "de_default",
        ],
    )
    assert res2.exit_code == 0, res2.output
    assert _agents_file(project_dir, "de_default").is_file()
    assert not _agents_file(project_dir).exists()


def test_agents_marker_refresh_failure_leaves_state_unchanged(
    monkeypatch, tmp_path: Path
):
    project_dir, _ = _make_project(tmp_path)
    marker_path = project_dir / "translations" / "de_default" / ".booktx-profile.json"
    assert marker_path.is_file()
    marker_before = marker_path.read_text("utf-8")
    # Seed a managed root file to prove it is not cleaned up either.
    runner.invoke(app, ["agents", "write", str(project_dir), "--mode", "collaborative"])
    root_before = _agents_file(project_dir).read_text("utf-8")

    def boom(*args, **kwargs):
        raise OSError("simulated marker write failure")

    monkeypatch.setattr("booktx.workflows.agents.write_profile_root_marker", boom)
    res = runner.invoke(
        app,
        [
            "agents",
            "write",
            str(project_dir),
            "--mode",
            "isolated",
            "--profile",
            "de_default",
        ],
    )
    assert res.exit_code != 0
    # Marker is unchanged (atomic refresh aborted before any mutation).
    assert marker_path.read_text("utf-8") == marker_before
    # Target was never written and no cleanup ran.
    assert not _agents_file(project_dir, "de_default").exists()
    assert _agents_file(project_dir).read_text("utf-8") == root_before


def test_agents_isolated_template_distinguishes_bounded_and_complete_book(
    tmp_path: Path,
):
    project_dir, _ = _make_project(tmp_path)
    res = runner.invoke(
        app,
        [
            "agents",
            "write",
            str(project_dir),
            "--mode",
            "isolated",
            "--profile",
            "de_default",
        ],
    )
    assert res.exit_code == 0, res.output
    text = _agents_file(project_dir, "de_default").read_text("utf-8")
    # Bounded-todo completion does NOT mandate a whole-book build by default.
    assert (
        "Do not run a whole-book build merely because the bounded todo finished" in text
    )
    # Complete-book completion includes validate/build and conditional review.
    assert "booktx validate . --fail-on-warnings" in text
    assert "booktx build . --require-complete" in text
    assert "--require-reviewed" in text
