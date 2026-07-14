from __future__ import annotations

import pytest

from booktx.command_catalog import (
    SUMMARY_OVERRIDES,
    CommandAudience,
    CommandDescriptor,
    descriptor_for_path,
)


def test_summary_overrides_are_strings() -> None:
    assert all(isinstance(value, str) for value in SUMMARY_OVERRIDES.values())


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
