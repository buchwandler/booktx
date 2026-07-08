"""CLI and workflow coverage for `booktx series`."""

from __future__ import annotations

from pathlib import Path

from ebooklib import epub
from typer.testing import CliRunner

from booktx.cli import app
from booktx.config import load_profile_project, load_project, load_source_project
from booktx.context import ContextQuestion, load_context, write_context
from booktx.source_analysis import (
    AnalysisCapabilities,
    SourceAnalysisReport,
    SourceAnalysisSettings,
    SourceCandidate,
    SourceStyleMetrics,
)
from booktx.source_analysis_context import prefill_contexts

runner = CliRunner()


def _make_epub_source(
    path: Path, *, title: str, chapter_title: str, paragraph: str
) -> Path:
    book = epub.EpubBook()
    book.set_identifier(f"{title}-id")
    book.set_title(title)
    book.set_language("en")
    chapter = epub.EpubHtml(title=chapter_title, file_name="ch1.xhtml", lang="en")
    chapter.content = (
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        f"<head><title>{chapter_title}</title></head><body>"
        f"<h1>{chapter_title}</h1><p>{paragraph}</p>"
        "</body></html>"
    )
    book.add_item(chapter)
    book.add_item(epub.EpubNav())
    book.add_item(epub.EpubNcx())
    book.toc = (chapter,)
    book.spine = ["nav", chapter]
    epub.write_epub(str(path), book, {})
    return path


def _answer_core(project_dir: Path, profile: str) -> None:
    answers = [
        ("Q001", "de-DE"),
        ("Q002", "balanced"),
        ("Q003", "neutral"),
        ("Q004", "natural dialogue"),
        ("Q005", "keep Apt names"),
        ("Q006", "translate world terms"),
        ("Q012", "error"),
    ]
    for qid, text in answers:
        result = runner.invoke(
            app,
            [
                "context",
                "answer",
                str(project_dir),
                "--profile",
                profile,
                qid,
                "--text",
                text,
            ],
        )
        assert result.exit_code == 0, result.output


def _ready_series_book(tmp_path: Path, name: str) -> tuple[Path, Path]:
    root = tmp_path / name
    source = _make_epub_source(
        tmp_path / f"{name}.epub",
        title=name,
        chapter_title="Chapter One",
        paragraph="Wasp-kinden marched with the Empire.",
    )
    assert (
        runner.invoke(
            app,
            [
                "init",
                str(root),
                "--source-file",
                str(source),
                "--source-lang",
                "en",
            ],
        ).exit_code
        == 0
    )
    assert runner.invoke(app, ["extract", str(root)]).exit_code == 0
    create = runner.invoke(
        app,
        [
            "profile",
            "create",
            str(root),
            "de_glm_5_2",
            "--target",
            "de",
            "--target-locale",
            "de-DE",
            "--model",
            "zai/glm-5.2@high",
        ],
    )
    assert create.exit_code == 0, create.output
    init_ctx = runner.invoke(
        app,
        [
            "context",
            "init",
            str(root),
            "--profile",
            "de_glm_5_2",
            "--non-interactive",
        ],
    )
    assert init_ctx.exit_code == 0, init_ctx.output
    _answer_core(root, "de_glm_5_2")
    add_term = runner.invoke(
        app,
        [
            "context",
            "reset-term",
            str(root),
            "--profile",
            "de_glm_5_2",
            "empire",
            "--target",
            "Imperium",
            "--forbid",
            "Reich",
            "--enforce",
            "error",
            "--create",
        ],
    )
    assert add_term.exit_code == 0, add_term.output
    ready = runner.invoke(
        app, ["context", "mark-ready", str(root), "--profile", "de_glm_5_2"]
    )
    assert ready.exit_code == 0, ready.output
    return root, source


def _series_prepare_args(
    *,
    target_book: Path,
    source_file: Path,
    from_book: Path | None = None,
    pack: Path | None = None,
    recipe: Path | None = None,
    write: bool = False,
) -> list[str]:
    args = [
        "series",
        "prepare",
        str(target_book),
        "--source-file",
        str(source_file),
        "--source-lang",
        "en",
        "--profile",
        "de_glm_5_2",
        "--series-id",
        "shadows-of-the-apt",
        "--title",
        "Shadows of the Apt German series context",
        "--target",
        "de",
        "--target-locale",
        "de-DE",
        "--model",
        "zai/glm-5.2@high",
        "--conflict",
        "replace",
        "--write-termbase",
        "--termbase-scope",
        "project",
    ]
    if from_book is not None:
        args.extend(["--from-book", str(from_book), "--from-profile", "de_glm_5_2"])
    if pack is not None:
        args.extend(["--pack", str(pack)])
    if recipe is not None:
        args.extend(["--recipe", str(recipe)])
    if write:
        args.append("--write")
    return args


def test_series_prepare_write_creates_expected_artifacts(tmp_path: Path) -> None:
    book4, _ = _ready_series_book(tmp_path, "book4")
    book5 = tmp_path / "book5"
    book5_source = _make_epub_source(
        tmp_path / "book5.epub",
        title="book5",
        chapter_title="Chapter One",
        paragraph="Mosquito-kinden studied the Art beside the Empire.",
    )

    result = runner.invoke(
        app,
        _series_prepare_args(
            target_book=book5,
            source_file=book5_source,
            from_book=book4,
            write=True,
        ),
    )

    assert result.exit_code == 0, result.output
    assert (book5 / ".booktx" / "source-config.toml").is_file()
    assert list((book5 / ".booktx" / "chunks").glob("*.json"))
    assert (book5 / ".booktx" / "reports" / "chapter-audit.json").is_file()
    assert (book5 / "translations" / "de_glm_5_2" / "config.toml").is_file()
    assert (book5 / "translations" / "de_glm_5_2" / "context.json").is_file()
    assert (book5 / "translations" / "de_glm_5_2" / "context.md").is_file()
    context = load_context(load_project(book5, profile="de_glm_5_2"))
    assert context is not None
    assert not context.ready
    assert any(
        entry.source == "empire" and entry.target == "Imperium"
        for entry in context.glossary
    )
    assert (book5 / ".booktx" / "source-analysis.json").is_file()
    assert (book5 / "translations" / "de_glm_5_2" / "source-analysis.json").is_file()
    assert (book5 / ".booktx" / "reports" / "series-prepare.json").is_file()
    assert "Context is NOT READY because review is required." in result.output
    assert "booktx context mark-ready" in result.output
    assert "booktx agents write" in result.output


def test_series_prepare_dry_run_writes_nothing(tmp_path: Path) -> None:
    book4, _ = _ready_series_book(tmp_path, "book4")
    book5 = tmp_path / "book5"
    book5_source = _make_epub_source(
        tmp_path / "book5.epub",
        title="book5",
        chapter_title="Chapter One",
        paragraph="Mosquito-kinden studied the Art.",
    )

    result = runner.invoke(
        app,
        _series_prepare_args(
            target_book=book5,
            source_file=book5_source,
            from_book=book4,
            write=False,
        ),
    )

    assert result.exit_code == 0, result.output
    assert "Dry run. No files written." in result.output
    assert not (book5 / ".booktx").exists()


def test_series_prepare_is_idempotent(tmp_path: Path) -> None:
    book4, _ = _ready_series_book(tmp_path, "book4")
    book5 = tmp_path / "book5"
    book5_source = _make_epub_source(
        tmp_path / "book5.epub",
        title="book5",
        chapter_title="Chapter One",
        paragraph="Mosquito-kinden studied the Art.",
    )
    args = _series_prepare_args(
        target_book=book5,
        source_file=book5_source,
        from_book=book4,
        write=True,
    )

    first = runner.invoke(app, args)
    second = runner.invoke(app, args)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert "reused the existing source project" in second.output
    assert "reused existing extracted chunks" in second.output


def test_series_prepare_rejects_incompatible_existing_profile(tmp_path: Path) -> None:
    book4, _ = _ready_series_book(tmp_path, "book4")
    book5 = tmp_path / "book5"
    book5_source = _make_epub_source(
        tmp_path / "book5.epub",
        title="book5",
        chapter_title="Chapter One",
        paragraph="Mosquito-kinden studied the Art.",
    )
    assert (
        runner.invoke(
            app,
            [
                "init",
                str(book5),
                "--source-file",
                str(book5_source),
                "--source-lang",
                "en",
            ],
        ).exit_code
        == 0
    )
    assert runner.invoke(app, ["extract", str(book5)]).exit_code == 0
    create = runner.invoke(
        app,
        [
            "profile",
            "create",
            str(book5),
            "de_glm_5_2",
            "--target",
            "fr",
        ],
    )
    assert create.exit_code == 0, create.output

    result = runner.invoke(
        app,
        _series_prepare_args(
            target_book=book5,
            source_file=book5_source,
            from_book=book4,
            write=True,
        ),
    )

    assert result.exit_code != 0
    assert "replace-profile" in result.output
    assert not (book5 / ".booktx" / "source-analysis.json").exists()


def test_series_prepare_supports_pack_mode(tmp_path: Path) -> None:
    book4, _ = _ready_series_book(tmp_path, "book4")
    pack = tmp_path / "series-context.json"
    export = runner.invoke(
        app,
        [
            "context",
            "export-pack",
            str(book4),
            "--profile",
            "de_glm_5_2",
            "--series-id",
            "shadows-of-the-apt",
            "--title",
            "Shadows of the Apt German series context",
            "--output",
            str(pack),
        ],
    )
    assert export.exit_code == 0, export.output
    book5 = tmp_path / "book5"
    book5_source = _make_epub_source(
        tmp_path / "book5.epub",
        title="book5",
        chapter_title="Chapter One",
        paragraph="Mosquito-kinden studied the Art.",
    )

    result = runner.invoke(
        app,
        _series_prepare_args(
            target_book=book5,
            source_file=book5_source,
            pack=pack,
            write=True,
        ),
    )

    assert result.exit_code == 0, result.output
    assert (book5 / ".booktx" / "reports" / "series-prepare.json").is_file()


def test_series_prepare_rejects_pack_and_from_book_together(tmp_path: Path) -> None:
    book4, _ = _ready_series_book(tmp_path, "book4")
    pack = tmp_path / "series-context.json"
    pack.write_text("{}", encoding="utf-8")
    book5_source = _make_epub_source(
        tmp_path / "book5.epub",
        title="book5",
        chapter_title="Chapter One",
        paragraph="Mosquito-kinden studied the Art.",
    )

    result = runner.invoke(
        app,
        _series_prepare_args(
            target_book=tmp_path / "book5",
            source_file=book5_source,
            from_book=book4,
            pack=pack,
            write=False,
        ),
    )

    assert result.exit_code != 0
    assert "exactly one of --from-book or --pack" in result.output


def test_series_prepare_rejects_profile_root_invocation(
    tmp_path: Path, monkeypatch
) -> None:
    book4, _ = _ready_series_book(tmp_path, "book4")
    profile_root = book4 / "translations" / "de_glm_5_2"
    monkeypatch.chdir(profile_root)
    pack = tmp_path / "series-context.json"
    export = runner.invoke(
        app,
        [
            "context",
            "export-pack",
            str(book4),
            "--profile",
            "de_glm_5_2",
            "--series-id",
            "shadows-of-the-apt",
            "--title",
            "Shadows of the Apt German series context",
            "--output",
            str(pack),
        ],
    )
    assert export.exit_code == 0, export.output
    source = _make_epub_source(
        tmp_path / "book5.epub",
        title="book5",
        chapter_title="Chapter One",
        paragraph="Mosquito-kinden studied the Art.",
    )

    result = runner.invoke(
        app,
        _series_prepare_args(
            target_book=tmp_path / "book5",
            source_file=source,
            pack=pack,
            write=False,
        ),
    )

    assert result.exit_code != 0
    assert "profile-root isolated mode" in result.output


def test_series_recipe_write_and_prepare_with_recipe(tmp_path: Path) -> None:
    book4, _ = _ready_series_book(tmp_path, "book4")
    recipe = tmp_path / "series.toml"
    write_recipe = runner.invoke(
        app,
        [
            "series",
            "recipe",
            "write",
            str(book4),
            "--profile",
            "de_glm_5_2",
            "--series-id",
            "shadows-of-the-apt",
            "--title",
            "Shadows of the Apt German series context",
            "--output",
            str(recipe),
        ],
    )
    assert write_recipe.exit_code == 0, write_recipe.output
    recipe_text = recipe.read_text("utf-8")
    assert 'schema = "booktx.series-recipe.v1"' in recipe_text
    assert "write_termbase = true" in recipe_text

    book5 = tmp_path / "book5"
    book5_source = _make_epub_source(
        tmp_path / "book5.epub",
        title="book5",
        chapter_title="Chapter One",
        paragraph="Mosquito-kinden studied the Art.",
    )
    result = runner.invoke(
        app,
        [
            "series",
            "prepare",
            str(book5),
            "--source-file",
            str(book5_source),
            "--from-book",
            str(book4),
            "--recipe",
            str(recipe),
            "--write",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (book5 / ".booktx" / "reports" / "series-prepare.json").is_file()


def test_prefill_consolidates_imported_policy_questions(tmp_path: Path) -> None:
    source = tmp_path / "novel.md"
    source.write_text("# One\n\nMosquito-kinden met Wasp-kinden.\n", encoding="utf-8")
    root = tmp_path / "novel"
    assert (
        runner.invoke(
            app,
            [
                "init",
                str(root),
                "--source-file",
                str(source),
                "--source-lang",
                "en",
            ],
        ).exit_code
        == 0
    )
    assert runner.invoke(app, ["extract", str(root)]).exit_code == 0
    assert (
        runner.invoke(
            app,
            [
                "profile",
                "create",
                str(root),
                "de_glm_5_2",
                "--target",
                "de",
                "--target-locale",
                "de-DE",
                "--model",
                "zai/glm-5.2@high",
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            [
                "context",
                "init",
                str(root),
                "--profile",
                "de_glm_5_2",
                "--non-interactive",
            ],
        ).exit_code
        == 0
    )

    profile_project = load_profile_project(root, "de_glm_5_2")
    context = load_context(profile_project)
    assert context is not None
    context.questions.append(
        ContextQuestion(
            id="S001",
            topic="source-analysis binding glossary",
            question="Review binding glossary candidates: Wasp-kinden, Ant-kinden",
            answer="Reuse the existing -kinden policy.",
            status="answered",
            required=False,
            origin="source_analysis",
            answer_source="imported",
            approved_by="context-pack:shadows-of-the-apt",
            approved_at="2026-01-01T00:00:00Z",
        )
    )
    write_context(profile_project, context)

    report = SourceAnalysisReport(
        identity_ruleset_version="1",
        analysis_ruleset_version="1",
        source_sha256="source",
        extracted_input_sha256="extract",
        chapter_map_sha256="chapters",
        analysis_sha256="analysis",
        source_language="en",
        generated_at="2026-01-01T00:00:00Z",
        settings=SourceAnalysisSettings(
            engine_requested="auto",
            engine_resolved="simple",
            spacy_model=None,
            spacy_version=None,
            model_version=None,
            min_count=2,
            ngram_max=4,
            top=200,
            include_common=False,
        ),
        capabilities=AnalysisCapabilities(
            tokenizer=False,
            sentence_boundaries=False,
            lemmatizer=False,
            pos=False,
            parser=False,
            noun_chunks=False,
            ner=False,
        ),
        record_count=2,
        chapter_count=1,
        candidates=[
            SourceCandidate(
                id="C001",
                text="Wasp-kinden",
                normalized="Wasp-kinden",
                kind="hyphenated_term",
                count=2,
                record_frequency=1,
                chapter_frequency=1,
                score=5.0,
                uncommon_score=5.0,
                suggested_context_action="review_for_binding_glossary",
                review_bucket="binding_glossary",
                risk_score=5.0,
            ),
            SourceCandidate(
                id="C002",
                text="Mosquito-kinden",
                normalized="Mosquito-kinden",
                kind="hyphenated_term",
                count=2,
                record_frequency=1,
                chapter_frequency=1,
                score=5.0,
                uncommon_score=5.0,
                suggested_context_action="review_for_binding_glossary",
                review_bucket="binding_glossary",
                risk_score=5.0,
            ),
        ],
        style_metrics=SourceStyleMetrics(
            record_count_with_dialogue=0,
            dialogue_record_ratio=0.0,
            quote_counts={},
            em_dash_count=0,
            emphasis_count=0,
            sentence_count=1,
            average_sentence_words=5.0,
            capability_warnings=[],
        ),
    )

    result = prefill_contexts(
        load_source_project(root),
        report,
        profiles=["de_glm_5_2"],
        write=True,
        consolidate_imported_policy=True,
    )

    assert not result.blocked
    updated = load_context(profile_project)
    assert updated is not None
    new_questions = [
        question
        for question in updated.questions
        if question.topic == "source-analysis binding glossary"
        and question.answer_source != "imported"
    ]
    assert len(new_questions) == 1
    assert "Mosquito-kinden" in new_questions[0].question
    assert "Wasp-kinden" not in new_questions[0].question
    assert "Covered by imported policy" in new_questions[0].recommendation_reason
