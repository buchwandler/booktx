"""Selection-purpose helpers shared across judge status, acceptance, task
rendering, validation, build, and agent rendering.

This module is intentionally dependency-light: it imports only
:mod:`booktx.errors` (which has no ``booktx`` imports of its own) so that
low-level modules such as :mod:`booktx.validate` and :mod:`booktx.build` can use
it without creating import cycles through :mod:`booktx.workflows.judge` or
:mod:`booktx.judge_store`. The shared purpose-aware source resolution lives here
so status, next, record, continue, and all project-root judge planning paths use
one rule set.

The two selection purposes are:

- ``"compare"`` (default): multi-source candidate comparison. Existing source
  resolution, ``accept-identical``, ``sweep-identical``, and
  ``prefill-policy-fixes`` behavior is preserved.
- ``"revise"``: single-source judge revision. Exactly one source is configured;
  every record requires an explicit ``copy``/``edited`` decision, and the
  project-root ``--sources`` override is forbidden.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from booktx.errors import _err

if TYPE_CHECKING:
    from booktx.config import Project

__all__ = [
    "SelectionPurpose",
    "parse_sources_csv",
    "selection_purpose",
    "is_revision_selection_profile",
    "configured_revision_source",
    "resolve_judge_sources_for_purpose",
    "require_selection_profile",
]


SelectionPurpose = Literal["compare", "revise"]


def parse_sources_csv(raw: str | None) -> list[str]:
    """Split a ``--sources`` CSV into a de-duplicated, order-preserving list.

    Mirrors :func:`booktx.judge_store.parse_sources_csv` so this module never
    needs to import :mod:`booktx.judge_store` (which would pull in validation).
    """
    if raw is None:
        return []
    seen: set[str] = set()
    values: list[str] = []
    for item in raw.split(","):
        value = item.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
    return values


def require_selection_profile(project: Project) -> None:
    """Fail through the existing selection-profile guard when config is missing.

    A missing selection config continues to fail here with
    ``judge_profile_kind`` rather than being silently treated as compare.
    """
    cfg = project.profile_config
    if cfg is None or cfg.kind != "selection" or cfg.selection is None:
        raise _err(
            "judge_profile_kind",
            "judge workflows require a selection profile",
        )


def selection_purpose(project: Project) -> SelectionPurpose:
    """Return the effective selection purpose (defaults to ``"compare"``).

    A profile whose selection config is absent (e.g. a non-selection profile, or
    a selection profile read before guards ran) reads as compare so the helper
    never raises purely from a missing purpose field.
    """
    cfg = project.profile_config
    if cfg is None or cfg.selection is None:
        return "compare"
    return cfg.selection.purpose


def is_revision_selection_profile(project: Project) -> bool:
    """True when the selection profile is configured for single-source revision."""
    return selection_purpose(project) == "revise"


def configured_revision_source(project: Project) -> str:
    """Return the single configured revise source.

    The :class:`booktx.models.SelectionConfig` validator already guarantees
    exactly one source for ``purpose=revise``; this helper only reads it. Call
    :func:`require_selection_profile` first to enforce the guard.
    """
    require_selection_profile(project)
    cfg = project.profile_config
    assert cfg is not None and cfg.selection is not None
    return cfg.selection.sources[0]


def resolve_judge_sources_for_purpose(
    project: Project,
    sources_csv: str | None,
) -> list[str]:
    """Resolve judge sources under the profile's selection purpose.

    - compare: the explicit ``--sources`` list when present, otherwise the
      configured source list (existing project-root collaborative override
      behavior). Raises ``judge_sources_missing`` when neither is set.
    - revise: exactly the configured single source. A non-empty ``--sources``
      value is accepted only when it equals the configured one-element list
      exactly; any other value (replacement, extension, or a different source)
      raises ``judge_revision_sources_override``.

    Use the same resolver in status, next, record, continue, and any
    project-root judge planning path. Snapshot/profile-root mode continues using
    the configured snapshot source (validated elsewhere against the snapshot).
    """
    require_selection_profile(project)
    cfg = project.profile_config
    assert cfg is not None and cfg.selection is not None

    if cfg.selection.purpose != "revise":
        explicit = parse_sources_csv(sources_csv)
        if explicit:
            return explicit
        if not cfg.selection.sources:
            raise _err(
                "judge_sources_missing",
                "no source profiles configured; create the selection profile "
                "with sources",
            )
        return list(cfg.selection.sources)

    configured = cfg.selection.sources[0]
    explicit = parse_sources_csv(sources_csv)
    if explicit and explicit != [configured]:
        raise _err(
            "judge_revision_sources_override",
            "selection.purpose=revise pins the single configured source ",
            f"{configured!r}; --sources cannot replace or extend it",
        )
    return [configured]
