from __future__ import annotations

import pytest
import typer

import booktx.command_catalog as command_catalog
from booktx.command_catalog import (
    SUMMARY_OVERRIDES,
    CommandAudience,
    CommandDescriptor,
    apply_command_catalog,
    descriptor_for_path,
    validate_command_catalog,
)


def test_summary_overrides_are_strings() -> None:
    assert all(isinstance(value, str) for value in SUMMARY_OVERRIDES.values())
    validate_command_catalog()


def test_all_summary_overrides_render_help() -> None:
    for path in SUMMARY_OVERRIDES:
        rendered = descriptor_for_path(path).render_help()
        assert isinstance(rendered, str)
        assert rendered


def test_command_descriptor_rejects_non_string_summary() -> None:
    with pytest.raises(TypeError, match=r"CommandDescriptor\.summary must be str"):
        CommandDescriptor(
            path="broken",
            audience=CommandAudience.MAINTENANCE,
            stage="test",
            summary=("broken",),  # type: ignore[arg-type]
        )


def test_invalid_optional_summary_warns_and_preserves_native_help(monkeypatch) -> None:
    monkeypatch.setitem(SUMMARY_OVERRIDES, "translate todo-doctor", ("broken summary",))
    monkeypatch.setattr(command_catalog, "_catalog_warning_emitted", False)
    root = typer.Typer()

    @root.command(name="todo-doctor")
    def todo_doctor() -> None:
        """Native todo-doctor help."""

    app = typer.Typer()
    app.add_typer(root, name="translate")
    with pytest.warns(RuntimeWarning, match="Using native command help"):
        apply_command_catalog(app, root_app=typer.Typer())

    assert descriptor_for_path("translate todo-doctor").summary.startswith("Run the")
    with pytest.raises(TypeError, match="invalid command summaries"):
        validate_command_catalog()


@pytest.mark.parametrize(
    ("field_name", "kwargs"),
    [
        ("replacement", {"replacement": ("alias",)}),
        ("help_panel", {"help_panel": ("panel",)}),
        ("next_human_action", {"next_human_action": ("step",)}),
        ("next_agent_action", {"next_agent_action": ("step",)}),
        ("example", {"example": ("cmd",)}),
    ],
)
def test_command_descriptor_rejects_non_string_optional_text_fields(
    field_name: str, kwargs: dict[str, object]
) -> None:
    with pytest.raises(
        TypeError, match=rf"CommandDescriptor\.{field_name} must be str \| None"
    ):
        CommandDescriptor(
            path="broken",
            audience=CommandAudience.MAINTENANCE,
            stage="test",
            summary="broken",
            **kwargs,
        )
