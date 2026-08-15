"""Optional local linguistic-quality backends."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

BackendSeverity = Literal["warn", "error"]


@dataclass(frozen=True, slots=True)
class BackendFinding:
    """Backend-neutral finding returned before booktx policy resolution."""

    rule: str
    message: str
    severity: BackendSeverity = "warn"
    excerpt: str = ""
    category: str = ""


class LinguisticBackend(Protocol):
    """Contract for a local, deterministic linguistic checker."""

    @property
    def identity(self) -> str: ...

    def audit(
        self,
        *,
        locale: str,
        record_id: str,
        source: str,
        target: str,
        ignored_terms: tuple[str, ...] = (),
        ignored_categories: tuple[str, ...] = (),
    ) -> list[BackendFinding]: ...


__all__ = ["BackendFinding", "LinguisticBackend"]
