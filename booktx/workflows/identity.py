"""Domain workflow functions for translation identity defaults.

These functions own the identity (actor / harness / model) read and mutation
logic that previously lived inline in ``booktx/cli.py``. The thin Typer
commands in :mod:`booktx.commands.identity` delegate here.

Workflows may import the lower-level config / context / versioning modules
freely; the command layer must not.
"""

from __future__ import annotations

from booktx.config import (
    Project,
    identity_path,
    load_identity,
    write_identity,
)
from booktx.models import TranslationIdentity
from booktx.versioning import default_identity, resolve_identity


def resolve_identity_view(proj: Project) -> TranslationIdentity:
    """Return the fully resolved identity (read-only, no writes)."""
    return resolve_identity(proj)


def set_identity_defaults(
    proj: Project,
    *,
    actor: str | None = None,
    harness: str | None = None,
    model: str | None = None,
) -> TranslationIdentity:
    """Resolve and persist identity defaults, returning the resolved identity.

    Only the fields passed (non-None) override the stored value; the rest are
    resolved from existing storage or the local fallback.
    """
    identity = resolve_identity(proj, actor=actor, harness=harness, model=model)
    write_identity(proj, identity)
    return identity


def clear_identity_field(proj: Project, field_name: str) -> TranslationIdentity:
    """Reset one identity field to its local fallback.

    When all three fields fall back to defaults, the on-disk identity file is
    removed so future reads use the local fallback directly.
    """
    current = load_identity(proj)
    fallback = default_identity()
    identity = TranslationIdentity(
        actor=current.actor if current is not None else fallback.actor,
        harness=current.harness if current is not None else fallback.harness,
        model=current.model if current is not None else fallback.model,
    )
    setattr(identity, field_name, getattr(fallback, field_name))
    if identity == fallback and identity_path(proj).is_file():
        identity_path(proj).unlink()
        return fallback
    write_identity(proj, identity)
    return identity
