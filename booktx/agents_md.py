"""Rendering and managed-file ownership for generated ``AGENTS.md`` files.

This module is intentionally free of :mod:`typer` / :mod:`rich` so it can be
unit-tested in isolation and reused by workflows and commands alike.

A booktx-managed ``AGENTS.md`` opens with a bounded metadata comment:

.. code-block:: markdown

    <!-- booktx-agents-md
    schema: booktx.agents-md.v1
    mode: isolated
    profile: de_gpt5_5
    source_id: sha256:...
    generated_by: booktx
    -->

The ownership signature is ``schema: booktx.agents-md.v1`` plus
``generated_by: booktx``. A stale ``source_id`` keeps the file managed; it is
reported as stale, not unmanaged. The parser only reads that opening comment
(one ``key: value`` line at a time) and never interprets the human prose that
follows it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from booktx.errors import BooktxError, _err
from booktx.io_utils import write_text_atomic
from booktx.judge_policy import (
    DEFAULT_JUDGE_BATCH_RECORDS,
    DEFAULT_JUDGE_BATCH_RENDERED_LINES,
    DEFAULT_JUDGE_BATCH_SENTENCES,
    DEFAULT_JUDGE_BATCH_WORDS,
)

__all__ = [
    "AGENTS_FILENAME",
    "AgentMode",
    "AgentsMdInspection",
    "AgentsMdMetadata",
    "AgentsMdSkippedPath",
    "AgentsMdState",
    "AgentsMdStatusEntry",
    "AgentsMdSyncResult",
    "agents_md_path",
    "delete_managed_agents_md",
    "inspect_agents_md",
    "render_agents_md",
    "write_managed_agents_md",
]

AGENTS_FILENAME = "AGENTS.md"
SCHEMA = "booktx.agents-md.v1"

AgentMode = Literal["isolated", "collaborative"]
AgentsMdState = Literal[
    "absent",
    "unmanaged",
    "managed-valid",
    "managed-malformed",
    "symlink",
]

_MARKER_OPEN = b"<!-- booktx-agents-md"
_MARKER_CLOSE = b"-->"
_MAX_COMMENT_BYTES = 4096
_REQUIRED_KEYS: tuple[str, ...] = (
    "schema",
    "mode",
    "profile",
    "source_id",
    "generated_by",
)
_VALID_KEYS = frozenset(_REQUIRED_KEYS)
_VALID_MODES: tuple[str, ...] = ("isolated", "collaborative")


@dataclass(frozen=True, slots=True)
class AgentsMdMetadata:
    """Parsed metadata from a managed ``AGENTS.md`` opening comment."""

    schema: str
    mode: AgentMode
    profile: str | None
    source_id: str
    generated_by: str = "booktx"


@dataclass(frozen=True, slots=True)
class AgentsMdSkippedPath:
    """A path that a write/clean workflow intentionally did not touch."""

    path: Path
    reason: str


@dataclass(frozen=True, slots=True)
class AgentsMdSyncResult:
    """Outcome of a write or clean reconciliation."""

    mode: AgentMode | Literal["all"]
    written: tuple[Path, ...]
    deleted: tuple[Path, ...]
    skipped: tuple[AgentsMdSkippedPath, ...]
    profile: str | None = None


@dataclass(frozen=True, slots=True)
class AgentsMdInspection:
    """Full ownership classification of a single ``AGENTS.md`` path."""

    state: AgentsMdState
    metadata: AgentsMdMetadata | None


@dataclass(frozen=True, slots=True)
class AgentsMdStatusEntry:
    """A ``status`` report row for one applicable ``AGENTS.md`` path."""

    path: Path
    scope: Literal["project", "profile"]
    profile: str | None
    inspection: AgentsMdInspection
    stale: bool | None


class _ManagedParseError(Exception):
    """Internal signal that a marker-prefixed file failed metadata parsing."""


def agents_md_path(root: Path) -> Path:
    """Return the ``AGENTS.md`` path inside ``root``."""
    return root / AGENTS_FILENAME


def _parse_managed_block(data: bytes) -> AgentsMdMetadata:
    """Parse and validate the opening metadata comment of a marker-prefixed file.

    Raises :class:`_ManagedParseError` for any structural, encoding, or
    semantic defect so the caller can classify the file as ``managed-malformed``.
    """
    marker_end = len(_MARKER_OPEN)
    close_index = data.find(_MARKER_CLOSE, marker_end)
    if close_index == -1 or close_index >= _MAX_COMMENT_BYTES:
        raise _ManagedParseError(
            "metadata comment is not closed within the first 4 KiB"
        )
    body = data[marker_end:close_index]
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _ManagedParseError(f"metadata comment is not valid UTF-8: {exc}") from exc

    seen: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if ":" not in line:
            raise _ManagedParseError(
                f"metadata line is not a key: value pair: {line!r}"
            )
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key not in _VALID_KEYS:
            raise _ManagedParseError(f"unknown metadata key: {key!r}")
        if key in seen:
            raise _ManagedParseError(f"duplicate metadata key: {key!r}")
        seen[key] = value

    for key in _REQUIRED_KEYS:
        if key not in seen:
            raise _ManagedParseError(f"missing metadata key: {key!r}")

    schema = seen["schema"]
    generated_by = seen["generated_by"]
    if schema != SCHEMA:
        raise _ManagedParseError(f"unexpected schema: {schema!r}")
    if generated_by != "booktx":
        raise _ManagedParseError(f"unexpected generated_by: {generated_by!r}")

    mode = seen["mode"]
    if mode not in _VALID_MODES:
        raise _ManagedParseError(f"invalid mode: {mode!r}")

    profile_raw = seen["profile"]
    if profile_raw == "" or profile_raw.lower() == "null":
        profile: str | None = None
    else:
        profile = profile_raw
    if mode == "isolated" and profile is None:
        raise _ManagedParseError("isolated mode requires a profile name")
    if mode == "collaborative" and profile is not None:
        raise _ManagedParseError("collaborative mode must not carry a profile")

    source_id = seen["source_id"]
    if not source_id:
        raise _ManagedParseError("source_id must not be empty")

    return AgentsMdMetadata(
        schema=schema,
        mode=mode,  # type: ignore[arg-type]
        profile=profile,
        source_id=source_id,
        generated_by=generated_by,
    )


def inspect_agents_md(path: Path) -> AgentsMdInspection:
    """Classify a single ``AGENTS.md`` path without mutation.

    Order matters: symbolic links are reported as ``symlink`` before any
    content is read or followed. A regular file without the booktx opening
    marker is ``unmanaged``. A marker-prefixed file that fails metadata
    parsing is ``managed-malformed``.
    """
    if path.is_symlink():
        return AgentsMdInspection(state="symlink", metadata=None)
    if not path.exists() or not path.is_file():
        return AgentsMdInspection(state="absent", metadata=None)
    data = path.read_bytes()
    if not data.startswith(_MARKER_OPEN):
        return AgentsMdInspection(state="unmanaged", metadata=None)
    try:
        metadata = _parse_managed_block(data)
    except _ManagedParseError:
        return AgentsMdInspection(state="managed-malformed", metadata=None)
    return AgentsMdInspection(state="managed-valid", metadata=metadata)


def _render_metadata_block(
    *,
    mode: AgentMode,
    profile: str | None,
    source_id: str,
) -> str:
    profile_value = profile if profile is not None else "null"
    return (
        "<!-- booktx-agents-md\n"
        f"schema: {SCHEMA}\n"
        f"mode: {mode}\n"
        f"profile: {profile_value}\n"
        f"source_id: {source_id}\n"
        "generated_by: booktx\n"
        "-->"
    )


def _ensure_single_trailing_newline(text: str) -> str:
    return text.rstrip("\n") + "\n"


def _terminology_change_playbook() -> str:
    return (
        "Terminology-change playbook:\n"
        "\n"
        "When the user says 'always translate X as Y', 'change term X to Y', "
        "or 'never translate X as Z':\n"
        "\n"
        "1. Use `booktx glossary mandate`, not a direct context mutation command,"
        " unless the user explicitly says the term is advisory.\n"
        "2. Add source variants and target variants when needed.\n"
        "3. Add forbidden targets for wrong forms that already appeared.\n"
        "4. Run `booktx glossary audit`.\n"
        "5. Generate or write a correction block for active violations.\n"
        "6. Apply corrections with `booktx translate revise-block`.\n"
        "7. Run `booktx validate . --fail-on-warnings`.\n"
        "8. Do not continue old translation tasks until the context/task"
        " freshness state is clean.\n"
        "\n"
    )


def _render_isolated_body(*, profile: str, target_locale: str) -> str:
    return (
        "\n\n"
        "# booktx isolated profile instructions\n"
        "\n"
        "You are inside a booktx translation profile root.\n"
        "\n"
        "Use only profile-local commands with project argument `.`. "
        "Do not use parent paths, absolute paths, shell globs, "
        "filesystem traversal snippets, or sibling profile commands. "
        "Do not pass a profile-selection flag in this directory.\n"
        "\n"
        "First checks:\n"
        "\n"
        "```bash\n"
        "booktx mode .\n"
        "booktx doctor isolation .\n"
        "booktx context status .\n"
        "```\n"
        "\n"
        "If context is missing, not ready, or has unresolved approval "
        "questions, stop and ask the user. Do not translate and do not "
        "approve or mark context ready.\n"
        "\n"
        "Profile:\n"
        "\n"
        f"- profile: {profile}\n"
        f"- target: {target_locale}\n"
        "- source access: brokered through booktx commands\n"
        "- mutable state: this directory only\n"
        "\n"
        "Read before translating:\n"
        "\n"
        "- `context.md`\n"
        "- latest todo in `todos/`, when present\n"
        "- task briefs in `tasks/`\n"
        "- task source blocks in `tasks/`\n"
        "- editable submissions in `ingest/`\n"
        "\n"
        "When the user says `continue`:\n"
        "\n"
        "1. Run `booktx translate todo-status . --latest`.\n"
        "2. If no todo exists, report that there is no bounded run to "
        "continue and ask the user for a scope. Do not invent one.\n"
        "3. If the todo is complete, report completion and stop.\n"
        "4. If the todo is incomplete, run "
        "`booktx translate todo-resume . --latest --format block`.\n"
        "5. Read `tasks/TASK.agent.md` first. Consult the full immutable context "
        "snapshot only when the brief identifies an unresolved ambiguity.\n"
        "6. Translate only the generated ingest block.\n"
        "7. Always lint the completed block before the first insert with "
        "`booktx translate lint-block . --task-id TASK --file ingest/TASK.block.txt "
        "--format block`. Lint is read-only.\n"
        "8. If lint fails, repair the same ingest file and rerun lint once.\n"
        "9. Submit it with the exact `booktx translate insert . ...` "
        "command printed by booktx.\n"
        "10. If the same failure class remains after the lint repair retry, stop "
        "and report it instead of looping.\n"
        "11. If the installed booktx package fails to import in isolated mode, "
        "stop and report that the checkout must be fixed from project-root "
        "development mode.\n"
        "12. Run the scoped check command printed by the todo workflow.\n"
        "13. Repeat status/resume only until the requested todo is complete "
        "or booktx reports a stop condition.\n"
        "\n"
        "When the user says `translate the next N chapters`:\n"
        "\n"
        "```bash\n"
        "booktx translate todo-next . --chapters N --batch-words 800 --write "
        "--resume --format block\n"
        "```\n"
        "\n"
        "Translation rules:\n"
        "\n"
        "- Preserve record ids and placeholders exactly.\n"
        "- Preserve inline XHTML tags and quote boundaries.\n"
        "- Do not invent context answers or mark context ready.\n"
        "- User-approved context is binding.\n"
        "- If booktx prints a parent path, sibling profile, or any "
        "parent-directory reference, stop and report an isolation bug.\n"
        "\n" + _terminology_change_playbook() + "Completion checks:\n"
        "\n"
        "- For a bounded todo, use the scoped check commands printed by "
        "booktx and stop when that todo is complete. Do not run a "
        "whole-book build merely because the bounded todo finished.\n"
        "- When the user requested a complete book and all translation "
        "work is complete:\n"
        "\n"
        "  ```bash\n"
        "  booktx validate . --fail-on-warnings\n"
        "  booktx build . --require-complete\n"
        "  ```\n"
        "\n"
        "- If the profile requires quality review, complete the configured "
        "review workflow and add `--require-reviewed` to the final build.\n"
        "\n"
        "Use the installed booktx skill when available. This file is the "
        "local harness entry contract; it does not replace the skill.\n"
    )


def _render_isolated_selection_body(*, profile: str, target_locale: str) -> str:
    return (
        "\n\n"
        "# booktx isolated judge profile instructions\n"
        "\n"
        "You are inside a booktx selection profile root.\n"
        "\n"
        "Use only profile-local commands with project argument `.`. "
        "Do not use parent paths, absolute paths, shell globs, "
        "filesystem traversal snippets, sibling profile commands, or "
        "`--profile`.\n"
        "\n"
        "Profile:\n"
        "\n"
        f"- profile: {profile}\n"
        f"- target: {target_locale}\n"
        "- source access: copied judge-sources snapshot\n"
        "- mutable state: this directory only\n"
        "\n"
        "First checks:\n"
        "\n"
        "```bash\n"
        "booktx mode .\n"
        "booktx doctor isolation .\n"
        "booktx context status .\n"
        "booktx judge status .\n"
        "```\n"
        "\n"
        "If context is missing, not ready, or has unresolved approval "
        "questions, stop and ask the user.\n"
        "\n"
        "Autonomy rule (read before running any command below):\n"
        "\n"
        "- Run one booktx command at a time. Never wrap judge commands in "
        "a shell loop, never chain them with `||`, `&&`, or `;`, and never "
        "append `|| true` to swallow failures. Read each command's output "
        "before continuing.\n"
        "\n"
        "When the user says `continue`:\n"
        "\n"
        "1. Run `booktx judge status .`.\n"
        "2. If `context` is not READY or `judge source snapshot` is not valid, "
        "stop. Do not approve context yourself unless the user explicitly "
        "approves the shown answers.\n"
        "3. Run the printed `booktx judge next . ... --format decisions` "
        "command.\n"
        "4. Read `judge-tasks/TASK.source.block.txt`.\n"
        "5. Edit only `judge-ingest/TASK.decisions.txt`.\n"
        "6. For `decision_kind: copy`, set `selected` and `reason`; leave "
        "`TARGET` empty.\n"
        "7. For `decision_kind: edited`, set `selected`, `reason`, and write "
        "the complete edited target under `TARGET`.\n"
        "8. Submit with the exact `booktx judge insert . ...` command "
        "printed by booktx.\n"
        "9. Do not chain insert and next in one shell command. Continue only "
        "after insert succeeds.\n"
        "10. Use whole-profile `booktx validate . --fail-on-warnings` only "
        "after the requested judging scope is complete or before the final "
        "build.\n"
        "11. Build only when the requested scope is complete.\n"
        "\n"
        "Rules:\n"
        "\n"
        "- Preserve record ids and placeholders exactly.\n"
        "- Preserve inline XHTML tags and quote boundaries.\n"
        "- Do not invent context answers or mark context ready.\n"
        "- Do not use Python, sed, perl, awk, regex scripts, or bulk "
        "filesystem edits to rewrite judge ingest files.\n"
        "- Do not paste candidate text into `TARGET` for copy decisions.\n"
        "- Do not edit `judge-tasks/*.source.block.txt`.\n"
        "- Do not hand-edit `translation-store.json` or "
        "`translation-selection-ledger.json`.\n"
        "- Do not mention sibling profile names except as data printed by "
        "`booktx judge status .`; never access sibling profiles manually. "
        "Those names are inert snapshot labels, not permission to inspect "
        "sibling profiles.\n"
        "- If booktx prints a parent path, sibling profile, or any "
        "parent-directory reference, stop and report an isolation bug.\n"
        "\n"
        "Use the installed booktx skill when available. This file is the "
        "local harness entry contract; it does not replace the skill.\n"
    )


def _render_isolated_revision_body(
    *, profile: str, target_locale: str, revision_focus: str = "general"
) -> str:
    if revision_focus == "grammar":
        return (
            "\n\n"
            "# booktx isolated judge revision profile\n"
            "\n"
            "You are in a single-source grammar-only judge revision profile.\n"
            "\n"
            "Use only profile-local commands with project argument `.`. "
            "Do not use parent paths, absolute paths, shell globs, "
            "filesystem traversal snippets, sibling profile commands, or "
            "`--profile`.\n"
            "\n"
            "Profile:\n"
            "\n"
            f"- profile: {profile}\n"
            f"- target: {target_locale}\n"
            "- source access: copied judge-sources snapshot\n"
            "- mutable state: this directory only\n"
            "\n"
            "Allowed commands:\n"
            "\n"
            "```bash\n"
            "booktx judge status .\n"
            "booktx judge todo-next . --chapters N "
            f"--batch-records {DEFAULT_JUDGE_BATCH_RECORDS} "
            f"--batch-sentences {DEFAULT_JUDGE_BATCH_SENTENCES} "
            f"--batch-words {DEFAULT_JUDGE_BATCH_WORDS} "
            f"--batch-rendered-lines {DEFAULT_JUDGE_BATCH_RENDERED_LINES} "
            "--write --resume --format decisions\n"
            "booktx judge next . --unit chapter --chapter CHAPTER "
            f"--batch-records {DEFAULT_JUDGE_BATCH_RECORDS} "
            f"--batch-sentences {DEFAULT_JUDGE_BATCH_SENTENCES} "
            f"--batch-words {DEFAULT_JUDGE_BATCH_WORDS} --format decisions\n"
            "booktx judge record . --record RECORD_ID --format decisions\n"
            "booktx judge lint-decisions . --judge-task-id TASK "
            "--file judge-ingest/TASK.decisions.txt --format decisions\n"
            "booktx judge insert . --judge-task-id TASK "
            "--file judge-ingest/TASK.decisions.txt --format decisions\n"
            "booktx validate . --fail-on-warnings\n"
            "booktx build . --require-complete\n"
            "```\n"
            "\n"
            "A judge task is one bounded batch; it is not the user's requested "
            "scope. The one-command-at-a-time rule is a safety rule, not a "
            "one-batch-per-turn rule.\n"
            "\n"
            "When the user says `continue`, or asks to finish a chapter range:\n"
            "\n"
            "1. Run `booktx judge todo-status . --latest --json`.\n"
            "2. If the todo is complete, stop and report its persisted status.\n"
            "3. If the todo is incomplete, run `booktx judge todo-resume . "
            "--latest --format decisions`.\n"
            "4. Read the generated task, inspect every BASE_TARGET, edit only the "
            "decisions file, lint it, and insert it.\n"
            "5. Run todo-status again and repeat steps 2–5 in this same turn. A "
            "successful insert is never a stop condition.\n"
            "\n"
            "Stop only when the todo is complete, context/snapshot/source state "
            "is invalid, a documented blocker persists, the user stops the run, "
            "or the harness reaches an unavoidable execution limit. If the "
            "harness limit is reached, report authoritative counts and the exact "
            "resume command; never imply completion.\n"
            "\n"
            "Forbidden commands (they bypass explicit per-record decisions and "
            "must fail in revision mode):\n"
            "\n"
            "```bash\n"
            "booktx judge accept-identical . --write\n"
            "booktx judge sweep-identical . --write\n"
            "booktx judge prefill-policy-fixes . --write\n"
            "booktx translate insert .\n"
            "booktx translate revise-record .\n"
            "booktx translate revise-block .\n"
            "```\n"
            "\n"
            "For every record, inspect BASE_TARGET sentence by sentence and "
            "acknowledge every sentence, then choose an explicit copy or edited "
            "decision. Use copy when the German is grammatically "
            "correct. Use edited only for the smallest necessary German grammar, "
            "syntax, agreement, inflection, orthography, capitalization, or "
            "punctuation correction.\n"
            "\n"
            "BASE_TARGET is authoritative for wording and terminology. SOURCE "
            "is only a semantic guard. Do not retranslate, paraphrase, polish, "
            "improve flow, replace words with synonyms, change terminology, "
            "alter tone or register, or split or merge sentences.\n"
            "\n"
            "Grammar checklist for every BASE_TARGET:\n"
            "\n"
            "1. subject–verb agreement\n"
            "2. case government and apposition agreement\n"
            "3. article and adjective endings\n"
            "4. pronoun reference\n"
            "5. verb position and separable-verb spacing\n"
            "6. tense and mood consistency\n"
            "7. punctuation and quotation marks\n"
            "8. capitalization and orthography\n"
            "9. dangling participial or infinitive constructions\n"
            "10. sentence-boundary preservation\n"
            "\n"
            "Decision semantics:\n"
            "\n"
            "- `selected: A` with `decision_kind: copy` and an empty `TARGET` keeps "
            "the BASE_TARGET unchanged.\n"
            "- `selected: A` with `decision_kind: edited` and a full `TARGET` revises "
            "the BASE_TARGET.\n"
            "- `selected: edited` with `decision_kind: edited` and a full `TARGET` "
            "replaces the target entirely.\n"
            "\n"
            "Autonomy rule:\n"
            "\n"
            "- Run one booktx command at a time. Never wrap judge commands in "
            "a shell loop, never chain them with `||`, `&&`, or `;`, and never "
            "append `|| true` to swallow failures. Read each command's output "
            "before continuing.\n"
            "\n"
            "Rules:\n"
            "\n"
            "- Preserve record ids, placeholders, inline XHTML tags, and quote "
            "boundaries exactly.\n"
            "- Do not invent context answers or mark context ready.\n"
            "- Do not use Python, sed, perl, awk, regex scripts, or bulk "
            "filesystem edits to rewrite judge ingest files.\n"
            "- Do not edit `judge-tasks/*.source.block.txt`.\n"
            "- Do not hand-edit `translation-store.json` or "
            "`translation-selection-ledger.json`; effective output is valid only "
            "while each active target has matching judge-decision provenance.\n"
            "- If booktx prints a parent path, sibling profile, or any "
            "parent-directory reference, stop and report an isolation bug.\n"
            "\n"
            "Use the installed booktx skill when available. This file is the "
            "local harness entry contract; it does not replace the skill.\n"
        )
    return (
        "\n\n"
        "# booktx isolated judge revision profile\n"
        "\n"
        "You are in a single-source judge revision profile.\n"
        "\n"
        "Use only profile-local commands with project argument `.`. "
        "Do not use parent paths, absolute paths, shell globs, "
        "filesystem traversal snippets, sibling profile commands, or "
        "`--profile`.\n"
        "\n"
        "Profile:\n"
        "\n"
        f"- profile: {profile}\n"
        f"- target: {target_locale}\n"
        "- source access: copied judge-sources snapshot\n"
        "- mutable state: this directory only\n"
        "\n"
        "Allowed commands:\n"
        "\n"
        "```bash\n"
        "booktx judge status .\n"
        "booktx judge next . --unit chapter --chapter CHAPTER "
        "--max-records 20 --format decisions\n"
        "booktx judge record . --record RECORD_ID --format decisions\n"
        "booktx judge insert . --judge-task-id TASK "
        "--file judge-ingest/TASK.decisions.txt --format decisions\n"
        "booktx judge continue . --max-records 20\n"
        "booktx validate . --fail-on-warnings\n"
        "booktx build . --require-complete\n"
        "```\n"
        "\n"
        "Forbidden commands (they bypass explicit per-record decisions and "
        "must fail in revision mode):\n"
        "\n"
        "```bash\n"
        "booktx judge accept-identical . --write\n"
        "booktx judge sweep-identical . --write\n"
        "booktx judge prefill-policy-fixes . --write\n"
        "booktx translate insert .\n"
        "booktx translate revise-record .\n"
        "booktx translate revise-block .\n"
        "```\n"
        "\n"
        "For every record in a judge task, choose an explicit copy or edited "
        "decision. Do not skip records. Later corrections must use "
        "`booktx judge record`.\n"
        "\n"
        "Use edited for grammar, flow, punctuation, style, or terminology "
        "corrections.\n"
        "\n"
        "Decision semantics:\n"
        "\n"
        "- `selected: A` with `decision_kind: copy` and an empty `TARGET` keeps "
        "the BASE_TARGET unchanged.\n"
        "- `selected: A` with `decision_kind: edited` and a full `TARGET` revises "
        "the BASE_TARGET.\n"
        "- `selected: edited` with `decision_kind: edited` and a full `TARGET` "
        "replaces the target entirely.\n"
        "\n"
        "Autonomy rule:\n"
        "\n"
        "- Run one booktx command at a time. Never wrap judge commands in "
        "a shell loop, never chain them with `||`, `&&`, or `;`, and never "
        "append `|| true` to swallow failures. Read each command's output "
        "before continuing.\n"
        "\n"
        "Rules:\n"
        "\n"
        "- Preserve record ids, placeholders, inline XHTML tags, and quote "
        "boundaries exactly.\n"
        "- Do not invent context answers or mark context ready.\n"
        "- Do not use Python, sed, perl, awk, regex scripts, or bulk "
        "filesystem edits to rewrite judge ingest files.\n"
        "- Do not edit `judge-tasks/*.source.block.txt`.\n"
        "- Do not hand-edit `translation-store.json` or "
        "`translation-selection-ledger.json`; effective output is valid only "
        "while each active target has matching judge-decision provenance.\n"
        "- If booktx prints a parent path, sibling profile, or any "
        "parent-directory reference, stop and report an isolation bug.\n"
        "\n"
        "Use the installed booktx skill when available. This file is the "
        "local harness entry contract; it does not replace the skill.\n"
    )


def _render_collaborative_body() -> str:
    return (
        "\n\n"
        "# booktx collaborative project instructions\n"
        "\n"
        "You are at the booktx project root.\n"
        "\n"
        "Project-root mode is for profile administration, cross-profile "
        "review, migration, debugging, and explicitly selected translation "
        "work. Cross-profile commands are allowed here.\n"
        "\n"
        "First checks:\n"
        "\n"
        "```bash\n"
        "booktx mode .\n"
        "booktx status .\n"
        "booktx profile list .\n"
        "```\n"
        "\n"
        "Rules:\n"
        "\n"
        "- Do not translate unless the user selects a target profile explicitly.\n"
        "- Use `--profile PROFILE` for profile-local translation, review, "
        "validate, and build commands from this directory.\n"
        "- Use `booktx profile compare` for cross-profile inspection.\n"
        "- For unbiased model evaluation, do not work from this directory. "
        "Generate isolated instructions instead:\n"
        "\n"
        "```bash\n"
        "booktx agents write . --mode isolated --profile PROFILE\n"
        "```\n"
        "\n"
        "Typical translation command from project root:\n"
        "\n"
        "```bash\n"
        "booktx translate todo-next . --profile PROFILE --chapters 3 "
        "--batch-words 800 --write --resume --format block\n"
        "```\n"
        "\n"
        "Context policy:\n"
        "\n"
        "- Context answers are user policy.\n"
        "- Agent recommendations must be stored as recommendations and "
        "approved by the user before translation begins.\n"
        "- Do not use `context mark-ready --force` during normal "
        "translation work.\n"
        "\n"
        + _terminology_change_playbook()
        + "Use the installed booktx skill when available. This file is the "
        "project-root harness entry contract; it does not replace the skill.\n"
    )


def render_agents_md(
    *,
    mode: AgentMode,
    profile: str | None,
    source_id: str,
    target_locale: str | None = None,
    profile_kind: str | None = None,
    selection_purpose: str | None = None,
    revision_focus: str | None = None,
) -> str:
    """Render a complete managed ``AGENTS.md`` document for ``mode``.

    The isolated body must never reference parent paths, absolute paths,
    ``translations/``, sibling profile names, or ``--profile``. Only the
    current profile name and its target locale appear, because those are
    local identity. A selection profile (``profile_kind == "selection"``)
    receives judge-isolation instructions instead of translation instructions;
    a revision selection profile (``selection_purpose == "revise"``) receives
    the stricter revision contract.
    """
    header = _render_metadata_block(mode=mode, profile=profile, source_id=source_id)
    if mode == "isolated":
        if profile is None:
            raise _err(
                "agents_isolated_profile_required",
                "isolated AGENTS.md rendering requires a profile name",
            )
        if not target_locale:
            raise _err(
                "agents_isolated_target_required",
                "isolated AGENTS.md rendering requires a target locale",
            )
        if profile_kind == "selection":
            if selection_purpose == "revise":
                body = _render_isolated_revision_body(
                    profile=profile,
                    target_locale=target_locale,
                    revision_focus=revision_focus or "general",
                )
            else:
                body = _render_isolated_selection_body(
                    profile=profile, target_locale=target_locale
                )
        else:
            body = _render_isolated_body(profile=profile, target_locale=target_locale)
    else:
        body = _render_collaborative_body()
    return _ensure_single_trailing_newline(header + body)


def write_managed_agents_md(
    path: Path, text: str, *, replace_unmanaged: bool = False
) -> None:
    """Atomically write a managed ``AGENTS.md`` at ``path``.

    Refuses malformed-managed and symlink targets unconditionally. Refuses an
    unmanaged target unless ``replace_unmanaged`` is supplied. A managed-valid
    or absent target is always written.
    """
    inspection = inspect_agents_md(path)
    state = inspection.state
    if state == "symlink":
        raise _err(
            "agents_symlink_target",
            "target AGENTS.md is a symbolic link; refusing to overwrite",
        )
    if state == "managed-malformed":
        raise _err(
            "agents_malformed_target",
            "target AGENTS.md is a malformed managed file; "
            "inspect it manually before replacing",
        )
    if state == "unmanaged" and not replace_unmanaged:
        raise _err(
            "agents_unmanaged_target",
            "AGENTS.md exists and is not managed by booktx",
        )
    write_text_atomic(path, text)


def delete_managed_agents_md(
    path: Path,
    *,
    expected_mode: AgentMode | None = None,
) -> bool:
    """Delete a managed ``AGENTS.md`` at ``path`` with full ownership guards.

    Returns ``True`` when a file was deleted and ``False`` when the target is
    absent. Any other state (unmanaged, malformed-managed, symlink, or a
    ``managed-valid`` file whose mode does not match ``expected_mode``) raises
    a :class:`booktx.errors.BooktxError` with a stable ``agents_*`` code so the
    workflow layer can record it as a skip. ``expected_mode=None`` is reserved
    for write reconciliation and deletes any ``managed-valid`` file.
    """
    inspection = inspect_agents_md(path)
    state = inspection.state
    if state == "absent":
        return False
    if state == "symlink":
        raise _err(
            "agents_delete_symlink",
            "AGENTS.md is a symbolic link; refusing to delete",
        )
    if state == "unmanaged":
        raise _err(
            "agents_delete_unmanaged",
            "AGENTS.md is not managed by booktx; refusing to delete",
        )
    if state == "managed-malformed":
        raise _err(
            "agents_delete_malformed",
            "AGENTS.md is a malformed managed file; refusing to delete",
        )
    # state == "managed-valid"
    metadata = inspection.metadata
    assert metadata is not None
    if expected_mode is not None and metadata.mode != expected_mode:
        raise _err(
            "agents_delete_mode_mismatch",
            f"AGENTS.md is managed as {metadata.mode}; "
            f"not deleting for mode {expected_mode}",
        )
    path.unlink()
    return True


# Re-export BooktxError for callers that import everything from this module.
_ = BooktxError
