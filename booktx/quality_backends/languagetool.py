"""Explicit local LanguageTool command adapter.

This adapter intentionally shells out to a caller-configured executable. It
never downloads LanguageTool, discovers a public endpoint, or silently falls
back to another backend.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass

from booktx.quality_backends import BackendFinding, BackendSeverity


class LanguageToolUnavailable(RuntimeError):
    """Raised when the explicitly configured local command cannot run."""


@dataclass(frozen=True, slots=True)
class LocalLanguageToolBackend:
    """Run a pinned local LanguageTool CLI process for one target record."""

    command: str
    expected_version: str | None = None
    timeout_seconds: float = 10.0

    @property
    def identity(self) -> str:
        return f"languagetool-local:{self.command}:{self.expected_version or ''}"

    def _argv(self) -> list[str]:
        argv = shlex.split(self.command)
        if not argv:
            raise LanguageToolUnavailable("local LanguageTool command is empty")
        return argv

    def version(self) -> str:
        try:
            result = subprocess.run(
                [*self._argv(), "--version"],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LanguageToolUnavailable(
                f"configured local LanguageTool is unavailable: {exc}"
            ) from exc
        if result.returncode != 0:
            raise LanguageToolUnavailable(
                "configured local LanguageTool --version failed: "
                + (result.stderr.strip() or result.stdout.strip())
            )
        version = (result.stdout or result.stderr).strip()
        if self.expected_version and self.expected_version not in version:
            raise LanguageToolUnavailable(
                f"local LanguageTool version {version!r} does not contain pinned "
                f"version {self.expected_version!r}"
            )
        return version

    def audit(
        self,
        *,
        locale: str,
        record_id: str,
        source: str,
        target: str,
        ignored_terms: tuple[str, ...] = (),
        ignored_categories: tuple[str, ...] = (),
    ) -> list[BackendFinding]:
        del record_id, source
        # Check availability on every invocation. This makes an error-mode
        # backend fail clearly instead of degrading to a silent no-op.
        self.version()
        try:
            result = subprocess.run(
                [*self._argv(), "--language", locale, "--json"],
                input=target,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LanguageToolUnavailable(
                f"configured local LanguageTool audit failed: {exc}"
            ) from exc
        if result.returncode != 0:
            raise LanguageToolUnavailable(
                "configured local LanguageTool audit failed: "
                + (result.stderr.strip() or result.stdout.strip())
            )
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise LanguageToolUnavailable(
                "configured local LanguageTool returned invalid JSON"
            ) from exc

        findings: list[BackendFinding] = []
        for match in payload.get("matches", []):
            category_data = match.get("rule", {}).get("category", {})
            category = str(
                category_data.get("id")
                or category_data.get("name")
                or match.get("rule", {}).get("id", "")
            )
            if category in ignored_categories:
                continue
            offset = int(match.get("offset", 0))
            length = int(match.get("length", 0))
            excerpt = target[max(0, offset - 24) : offset + length + 24].strip()
            if any(term and term in excerpt for term in ignored_terms):
                continue
            issue_type = str(match.get("rule", {}).get("issueType", ""))
            severity: BackendSeverity = (
                "error" if issue_type in {"misspelling", "grammar"} else "warn"
            )
            rule_id = str(match.get("rule", {}).get("id", "languagetool"))
            findings.append(
                BackendFinding(
                    rule=f"languagetool:{rule_id}",
                    message=str(match.get("message", "LanguageTool finding")),
                    severity=severity,
                    excerpt=excerpt,
                    category=category,
                )
            )
        return findings


__all__ = ["LanguageToolUnavailable", "LocalLanguageToolBackend"]
