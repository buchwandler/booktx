"""Executable workflow capability boundaries for profile kinds."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from booktx.errors import _err

if TYPE_CHECKING:
    from booktx.config import Project

ProfileProtocol = Literal[
    "translation", "pass-through", "judge-compare", "judge-revise"
]


def profile_protocol(project: Project) -> ProfileProtocol:
    """Return the workflow protocol exposed by a project's profile."""
    cfg = project.profile_config
    if cfg is None or cfg.kind == "translation":
        return "translation"
    if cfg.kind == "pass-through":
        return "pass-through"
    if cfg.selection is not None and cfg.selection.purpose == "revise":
        return "judge-revise"
    return "judge-compare"


def require_translation_protocol(project: Project, *, command: str) -> None:
    """Reject translation workflow commands in selection profiles."""
    protocol = profile_protocol(project)
    if protocol not in {"judge-compare", "judge-revise"}:
        return
    raise _err(
        "selection_profile_translate_forbidden",
        f"{command} is unavailable in a selection profile; "
        "selection profiles require judge-decision provenance. "
        "Run: booktx judge status .",
    )
