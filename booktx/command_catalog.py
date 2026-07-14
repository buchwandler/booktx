"""Human-first command metadata and CLI help patching."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Literal

from typer.models import CommandInfo, TyperInfo

if TYPE_CHECKING:
    import typer

WritesMode = Literal["never", "always", "with_write_flag", "conditional"]
RuntimeModeName = Literal["project_root", "profile_root", "either"]


class CommandAudience(str, Enum):
    HUMAN_CORE = "human_core"
    HUMAN_GATED = "human_gated"
    HUMAN_ADVANCED = "human_advanced"
    AGENT_PROTOCOL = "agent_protocol"
    MAINTENANCE = "maintenance"


@dataclass(frozen=True, slots=True)
class CommandDescriptor:
    path: str
    audience: CommandAudience
    stage: str
    summary: str
    writes: WritesMode = "conditional"
    modes: frozenset[RuntimeModeName] = frozenset({"either"})
    requires_profile: bool = False
    replacement: str | None = None
    help_panel: str | None = None
    hidden: bool = False
    deprecated: bool = False
    next_human_action: str | None = None
    next_agent_action: str | None = None
    example: str | None = None
    group_help: bool = False

    def render_help(self) -> str:
        lines = [self.summary]
        audience = {
            CommandAudience.HUMAN_CORE: "Human operator",
            CommandAudience.HUMAN_GATED: "Human approval required",
            CommandAudience.HUMAN_ADVANCED: "Advanced operator",
            CommandAudience.AGENT_PROTOCOL: "Coding-agent harness",
            CommandAudience.MAINTENANCE: "Maintenance and recovery",
        }[self.audience]
        modes = {
            frozenset({"project_root"}): "Project root",
            frozenset({"profile_root"}): "Profile root",
            frozenset({"either"}): "Project root or profile root",
            frozenset({"project_root", "profile_root"}): "Project root or profile root",
        }.get(self.modes, "Project root or profile root")
        writes = {
            "never": "Read only",
            "always": "Writes files",
            "with_write_flag": "Only with --write",
            "conditional": "Depends on options and workflow state",
        }[self.writes]
        lines.extend(
            [
                "",
                f"Audience: {audience}",
                f"Runs from: {modes}",
                f"Writes: {writes}",
            ]
        )
        if self.requires_profile:
            lines.append("Requires: A resolved profile.")
        if self.replacement:
            lines.append(f"Canonical replacement: {self.replacement}")
        if self.example:
            lines.extend(["", "Example:", f"  {self.example}"])
        if self.next_human_action:
            lines.extend(["", "Next:", f"  {self.next_human_action}"])
        elif self.next_agent_action:
            lines.extend(["", "Next:", f"  {self.next_agent_action}"])
        return "\n".join(lines)


ROOT_HELP = """booktx prepares books for agent-assisted translation, keeps each
translation profile isolated, records human-approved policy, validates the
result, and rebuilds Markdown or EPUB output.

Start or resume:
  booktx guide PROJECT [--profile PROFILE]

There is no global active profile. Pass --profile from the project root, or run
inside translations/PROFILE for isolated work."""


TOP_LEVEL: dict[str, CommandDescriptor] = {
    "init": CommandDescriptor(
        "init",
        CommandAudience.HUMAN_CORE,
        "setup",
        "Create a source-first booktx project.",
        writes="always",
        modes=frozenset({"project_root"}),
        help_panel="BOOK SETUP",
        next_human_action=(
            "Run `booktx extract PROJECT` after the source file is present."
        ),
    ),
    "extract": CommandDescriptor(
        "extract",
        CommandAudience.HUMAN_CORE,
        "setup",
        "Extract source records and chapter structure.",
        writes="always",
        modes=frozenset({"project_root"}),
        help_panel="BOOK SETUP",
        next_human_action=(
            "Review chapters with `booktx chapters PROJECT --audit` when"
            " working from EPUB."
        ),
    ),
    "chapters": CommandDescriptor(
        "chapters",
        CommandAudience.HUMAN_CORE,
        "setup",
        "Inspect detected chapter ranges and EPUB chapter-audit findings.",
        writes="conditional",
        modes=frozenset({"project_root"}),
        help_panel="BOOK SETUP",
    ),
    "profile": CommandDescriptor(
        "profile",
        CommandAudience.HUMAN_CORE,
        "setup",
        "Create and inspect isolated translation profiles.",
        writes="conditional",
        modes=frozenset({"project_root", "profile_root"}),
        help_panel="BOOK SETUP",
        group_help=True,
    ),
    "series": CommandDescriptor(
        "series",
        CommandAudience.HUMAN_CORE,
        "setup",
        "Prepare the next book in a translated series.",
        writes="conditional",
        modes=frozenset({"project_root"}),
        help_panel="BOOK SETUP",
        group_help=True,
    ),
    "guide": CommandDescriptor(
        "guide",
        CommandAudience.HUMAN_CORE,
        "navigation",
        "Show the current lifecycle stage and the next human action.",
        writes="never",
        help_panel="HUMAN DECISIONS",
    ),
    "source": CommandDescriptor(
        "source",
        CommandAudience.HUMAN_CORE,
        "context",
        "Review source-analysis evidence and source-policy interviews.",
        writes="conditional",
        modes=frozenset({"project_root"}),
        help_panel="HUMAN DECISIONS",
        group_help=True,
    ),
    "context": CommandDescriptor(
        "context",
        CommandAudience.HUMAN_CORE,
        "context",
        "Approve translation policy and readiness for one profile.",
        writes="conditional",
        modes=frozenset({"project_root", "profile_root"}),
        help_panel="HUMAN DECISIONS",
        group_help=True,
    ),
    "glossary": CommandDescriptor(
        "glossary",
        CommandAudience.HUMAN_CORE,
        "context",
        "Manage binding terminology decisions through the human glossary surface.",
        writes="conditional",
        modes=frozenset({"project_root", "profile_root"}),
        help_panel="HUMAN DECISIONS",
        group_help=True,
    ),
    "agents": CommandDescriptor(
        "agents",
        CommandAudience.HUMAN_CORE,
        "translation",
        "Prepare and inspect generated AGENTS.md harness instructions.",
        writes="conditional",
        modes=frozenset({"project_root", "profile_root"}),
        help_panel="RUN AND MONITOR",
        group_help=True,
    ),
    "status": CommandDescriptor(
        "status",
        CommandAudience.HUMAN_CORE,
        "translation",
        "Show progress, blockers, and the current human/agent next steps.",
        writes="never",
        modes=frozenset({"project_root", "profile_root"}),
        help_panel="RUN AND MONITOR",
    ),
    "check": CommandDescriptor(
        "check",
        CommandAudience.HUMAN_CORE,
        "validation",
        "Run the normal pre-build checks for one project or profile.",
        writes="never",
        modes=frozenset({"project_root", "profile_root"}),
        help_panel="RUN AND MONITOR",
    ),
    "build": CommandDescriptor(
        "build",
        CommandAudience.HUMAN_CORE,
        "build",
        "Build the translated output into the profile output directory.",
        writes="always",
        modes=frozenset({"project_root", "profile_root"}),
        help_panel="RUN AND MONITOR",
    ),
    "review": CommandDescriptor(
        "review",
        CommandAudience.HUMAN_CORE,
        "quality",
        "Configure and monitor quality-review workflows.",
        writes="conditional",
        modes=frozenset({"project_root", "profile_root"}),
        help_panel="QUALITY WORKFLOWS",
        group_help=True,
    ),
    "judge": CommandDescriptor(
        "judge",
        CommandAudience.HUMAN_CORE,
        "quality",
        "Prepare and monitor comparison or revision profiles.",
        writes="conditional",
        modes=frozenset({"project_root", "profile_root"}),
        help_panel="QUALITY WORKFLOWS",
        group_help=True,
    ),
    "identity": CommandDescriptor(
        "identity",
        CommandAudience.HUMAN_ADVANCED,
        "advanced",
        "Set or clear default actor, harness, and model values for a profile.",
        writes="always",
        modes=frozenset({"project_root", "profile_root"}),
        help_panel="ADVANCED",
        group_help=True,
    ),
    "model": CommandDescriptor(
        "model",
        CommandAudience.HUMAN_ADVANCED,
        "advanced",
        "Manage translation model defaults for one profile.",
        writes="conditional",
        modes=frozenset({"project_root", "profile_root"}),
        group_help=True,
    ),
    "inspect": CommandDescriptor(
        "inspect",
        CommandAudience.HUMAN_ADVANCED,
        "advanced",
        "Estimate source records before extraction.",
        writes="never",
        modes=frozenset({"project_root"}),
        help_panel="ADVANCED",
    ),
    "qa-scan": CommandDescriptor(
        "qa-scan",
        CommandAudience.HUMAN_ADVANCED,
        "advanced",
        "Scan effective targets for QA findings.",
        writes="never",
        modes=frozenset({"project_root", "profile_root"}),
        help_panel="ADVANCED",
    ),
    "epub": CommandDescriptor(
        "epub",
        CommandAudience.HUMAN_ADVANCED,
        "advanced",
        "Inspect built EPUB output for human review.",
        writes="never",
        modes=frozenset({"project_root", "profile_root"}),
        help_panel="ADVANCED",
        group_help=True,
    ),
    "version": CommandDescriptor(
        "version",
        CommandAudience.HUMAN_ADVANCED,
        "advanced",
        "Inspect translation version history and advanced version controls.",
        writes="conditional",
        modes=frozenset({"project_root", "profile_root"}),
        help_panel="ADVANCED",
        group_help=True,
    ),
    "whoami": CommandDescriptor(
        "whoami",
        CommandAudience.HUMAN_ADVANCED,
        "advanced",
        "Show the resolved translation identity, context, and store state.",
        writes="never",
        modes=frozenset({"project_root", "profile_root"}),
        help_panel="ADVANCED",
    ),
    "translate": CommandDescriptor(
        "translate",
        CommandAudience.AGENT_PROTOCOL,
        "translation",
        "Run the durable translation task protocol.",
        writes="conditional",
        modes=frozenset({"project_root", "profile_root"}),
        hidden=True,
        group_help=True,
    ),
    "termbase": CommandDescriptor(
        "termbase",
        CommandAudience.MAINTENANCE,
        "maintenance",
        "Work with low-level termbase storage and schema operations.",
        writes="conditional",
        modes=frozenset({"project_root", "profile_root"}),
        hidden=True,
        group_help=True,
    ),
    "doctor": CommandDescriptor(
        "doctor",
        CommandAudience.MAINTENANCE,
        "maintenance",
        "Run diagnostic commands for isolation and recovery.",
        writes="never",
        modes=frozenset({"project_root", "profile_root"}),
        hidden=True,
        group_help=True,
    ),
    "validate": CommandDescriptor(
        "validate",
        CommandAudience.HUMAN_ADVANCED,
        "validation",
        "Run the full validation contract with detailed findings.",
        writes="never",
        modes=frozenset({"project_root", "profile_root"}),
        hidden=True,
    ),
    "mode": CommandDescriptor(
        "mode",
        CommandAudience.MAINTENANCE,
        "maintenance",
        "Show how booktx resolved the current runtime path.",
        writes="never",
        modes=frozenset({"project_root", "profile_root"}),
        hidden=True,
    ),
    "pass-through": CommandDescriptor(
        "pass-through",
        CommandAudience.MAINTENANCE,
        "maintenance",
        "Generate pass-through output for migration and recovery workflows.",
        writes="always",
        modes=frozenset({"project_root", "profile_root"}),
        hidden=True,
    ),
}


SUMMARY_OVERRIDES: dict[str, str] = {
    "profile create": "Create an isolated translation profile.",
    "profile list": "List available translation profiles.",
    "profile show": "Show one profile and its current readiness.",
    "profile compare": "Compare one source record across multiple profiles.",
    "profile migrate-current": "Migrate a legacy single-layout project into a profile.",
    "series prepare": "Prepare the next book in a series from a recipe or prior book.",
    "series recipe": "Create reusable series setup recipes.",
    "series recipe write": "Write a reusable series recipe from an existing profile.",
    "source analysis": "Read canonical or profile-local source-analysis evidence.",
    "judge create-profile": "Create a compare or revision profile for judge workflows.",
    "judge sync-sources": "Refresh judge source snapshots from translation profiles.",
    "judge prepare-isolation": "Prepare the isolated judge workspace and snapshots.",
    "judge prepare-grammar": (
        "Create and prepare a grammar-only revision profile in one command."
    ),
    "judge status": "Report judge workflow progress for a selection profile.",
    "judge next": "Create the next durable judge task.",
    "judge continue": "Continue an open judge task chain.",
    "judge record": "Create a focused judge task for one record.",
    "judge show": "Show one durable judge task payload.",
    "judge accept-identical": (
        "Accept identical judge candidates without manual editing."
    ),
    "judge sweep-identical": (
        "Accept identical judge candidates across a chapter range."
    ),
    "judge insert": "Accept a submitted judge decision block.",
    "judge reset-ingest": "Reset judge ingest files for one task.",
    "judge prefill-policy-fixes": (
        "Prefill judge policy corrections from current context."
    ),
    "judge finish-chapter-plan": "Finish a chapter judge plan once tasks are done.",
    "translate next": "Create the next durable translation task.",
    "translate insert": "Accept a durable translation submission.",
    "translate lint-block": "Validate a translation block before submission.",
    "translate todo-next": "Create a bounded translation todo for an agent run.",
    "translate todo-status": "Show progress for a bounded translation todo.",
    "translate todo-resume": "Resume the next task from a bounded translation todo.",
    "translate import-legacy": "Import legacy translated chunk files into the store.",
    "translate migrate-store": (
        "Inspect, migrate, verify, or roll back the profile's canonical "
        "translation store format."
    ),
    "translate export": "Export accepted translations as legacy-compatible files.",
    "translate export-index": "Export generated editor QA indexes.",
    "translate task-status": "Show status for one durable translation task.",
    "translate get-record": "Show one stored translation record with context.",
    "translate list": "List stored translation records.",
    "translate compare": "Compare stored versions for one translation record.",
    "translate activate": "Select the active stored version for one record.",
    "translate review": "Inspect one stored translation record before revision.",
    "translate set-record": "Store a direct task-scoped translation record update.",
    "translate revise-record": "Revise one accepted translation record.",
    "translate revise-block": "Apply a block of targeted translation corrections.",
    "translate audit-inline": "Audit stored translations for inline XHTML issues.",
    "translate migrate-inline-xhtml": "Normalize inline XHTML in stored translations.",
    "translate search": (
        "Search stored translations and optionally generate a fix block."
    ),
    "glossary status": "Show glossary coverage and active binding entries.",
    "glossary add": (
        "Add or update a glossary entry through the human glossary surface."
    ),
    "glossary remove": "Remove one glossary entry through the human glossary surface.",
    "glossary reset": (
        "Replace one glossary entry atomically through the glossary surface."
    ),
    "glossary mandate": "Record a binding glossary decision with required enforcement.",
    "glossary audit": "Audit effective translations against one glossary entry.",
    "glossary export": "Export glossary entries for reuse or review.",
    "glossary import": "Import glossary entries from a shard or bundle.",
    "termbase status": "Inspect the low-level termbase state.",
    "termbase add": "Write a raw termbase entry.",
    "termbase validate-entry": "Validate one termbase JSON entry.",
    "termbase export": "Export raw termbase shards or bundles.",
    "termbase import": "Import raw termbase shards or bundles.",
    "termbase scan-source": "Scan source records for low-level termbase candidates.",
    "termbase audit": "Audit effective translations against termbase rules.",
    "termbase promote-candidate": (
        "Promote one source-analysis candidate into the termbase."
    ),
    "termbase promote-context": "Promote one approved context term into the termbase.",
    "termbase write-review": "Write a termbase review report.",
    "identity set": "Set one or more default identity fields for a profile.",
    "identity clear": "Clear one or more default identity fields for a profile.",
    "context add-term": "Compatibility alias for glossary entry updates.",
    "context remove-term": "Compatibility alias for glossary entry removal.",
    "context reset-term": "Compatibility alias for atomic glossary entry replacement.",
    "context mandate-term": "Compatibility alias for mandatory glossary decisions.",
    "model whoami": "Show the resolved model default for translation versioning.",
    "model set": "Persist the model default used for new version tracks.",
    "model clear": "Clear the stored model default back to the local fallback.",
}


PANEL_BY_AUDIENCE = {
    CommandAudience.HUMAN_CORE: "Human workflow",
    CommandAudience.HUMAN_GATED: "Human decisions",
    CommandAudience.HUMAN_ADVANCED: "Advanced",
    CommandAudience.AGENT_PROTOCOL: "Agent protocol",
    CommandAudience.MAINTENANCE: "Maintenance",
}


REMOVED_ROOT_COMMANDS = {"next", "next-chapter"}
REMOVED_CONTEXT_COMMANDS = {"add-term", "remove-term", "reset-term", "mandate-term"}


def _top_level_descriptor(path: str) -> CommandDescriptor | None:
    return TOP_LEVEL.get(path)


def _leaf_audience(group: str, command: str) -> CommandAudience:
    """Resolve audience for a leaf command within its group."""
    # (group, command) -> overridden audience
    _OVERRIDES: dict[tuple[str, str], CommandAudience] = {
        ("translate", "next"): CommandAudience.AGENT_PROTOCOL,
        ("translate", "insert"): CommandAudience.AGENT_PROTOCOL,
        ("translate", "lint-block"): CommandAudience.AGENT_PROTOCOL,
        ("translate", "todo-next"): CommandAudience.AGENT_PROTOCOL,
        ("translate", "todo-status"): CommandAudience.AGENT_PROTOCOL,
        ("translate", "todo-resume"): CommandAudience.AGENT_PROTOCOL,
        ("translate", "task-status"): CommandAudience.AGENT_PROTOCOL,
        ("translate", "set-record"): CommandAudience.AGENT_PROTOCOL,
        ("translate", "import-legacy"): CommandAudience.MAINTENANCE,
        ("translate", "migrate-store"): CommandAudience.MAINTENANCE,
        ("translate", "export"): CommandAudience.MAINTENANCE,
        ("translate", "audit-inline"): CommandAudience.MAINTENANCE,
        ("translate", "migrate-inline-xhtml"): CommandAudience.MAINTENANCE,
        ("review", "configure"): CommandAudience.HUMAN_CORE,
        ("review", "status"): CommandAudience.HUMAN_CORE,
        ("judge", "create-profile"): CommandAudience.HUMAN_CORE,
        ("judge", "prepare-isolation"): CommandAudience.HUMAN_CORE,
        ("judge", "prepare-grammar"): CommandAudience.HUMAN_CORE,
        ("judge", "status"): CommandAudience.HUMAN_CORE,
        ("judge", "sync-sources"): CommandAudience.MAINTENANCE,
        ("context", "recommend"): CommandAudience.AGENT_PROTOCOL,
        ("context", "answer"): CommandAudience.MAINTENANCE,
        ("context", "import-md"): CommandAudience.MAINTENANCE,
        ("context", "doctor"): CommandAudience.HUMAN_ADVANCED,
        ("context", "render"): CommandAudience.HUMAN_ADVANCED,
        ("context", "audit-term"): CommandAudience.HUMAN_ADVANCED,
        ("context", "export-pack"): CommandAudience.HUMAN_ADVANCED,
        ("context", "import-pack"): CommandAudience.HUMAN_ADVANCED,
        ("context", "sync"): CommandAudience.HUMAN_ADVANCED,
        ("context", "prefill"): CommandAudience.HUMAN_GATED,
        ("context", "promote-candidate"): CommandAudience.HUMAN_GATED,
        ("context", "add-question"): CommandAudience.HUMAN_GATED,
        ("context", "chapter-note"): CommandAudience.HUMAN_GATED,
        ("source", "record"): CommandAudience.HUMAN_ADVANCED,
        ("source", "chapter"): CommandAudience.HUMAN_ADVANCED,
        ("profile", "compare"): CommandAudience.HUMAN_ADVANCED,
        ("profile", "migrate-current"): CommandAudience.MAINTENANCE,
        ("profile", "create-pass-through"): CommandAudience.MAINTENANCE,
        ("glossary", "export"): CommandAudience.HUMAN_ADVANCED,
        ("glossary", "import"): CommandAudience.HUMAN_ADVANCED,
        ("glossary", "audit"): CommandAudience.HUMAN_ADVANCED,
        ("termbase", "status"): CommandAudience.HUMAN_ADVANCED,
        ("termbase", "scan-source"): CommandAudience.HUMAN_ADVANCED,
        ("termbase", "audit"): CommandAudience.HUMAN_ADVANCED,
        ("termbase", "write-review"): CommandAudience.HUMAN_ADVANCED,
        ("termbase", "promote-candidate"): CommandAudience.HUMAN_GATED,
        ("termbase", "promote-context"): CommandAudience.HUMAN_GATED,
        ("version", "select"): CommandAudience.MAINTENANCE,
        ("version", "set-label"): CommandAudience.MAINTENANCE,
        ("version", "fork-context"): CommandAudience.MAINTENANCE,
        ("series", "recipe"): CommandAudience.HUMAN_ADVANCED,
        ("series", "recipe write"): CommandAudience.HUMAN_ADVANCED,
    }
    override = _OVERRIDES.get((group, command))
    if override is not None:
        return override

    # Default audience per group
    _DEFAULT: dict[str, CommandAudience] = {
        "translate": CommandAudience.HUMAN_ADVANCED,
        "review": CommandAudience.AGENT_PROTOCOL,
        "judge": CommandAudience.AGENT_PROTOCOL,
        "context": CommandAudience.HUMAN_CORE,
        "source": CommandAudience.HUMAN_CORE,
        "profile": CommandAudience.HUMAN_CORE,
        "glossary": CommandAudience.HUMAN_CORE,
        "termbase": CommandAudience.MAINTENANCE,
        "series": CommandAudience.HUMAN_CORE,
        "identity": CommandAudience.HUMAN_ADVANCED,
        "agents": CommandAudience.HUMAN_CORE,
        "version": CommandAudience.HUMAN_ADVANCED,
        "epub": CommandAudience.HUMAN_ADVANCED,
        "doctor": CommandAudience.HUMAN_CORE,
        "model": CommandAudience.HUMAN_ADVANCED,
    }
    return _DEFAULT.get(group, CommandAudience.HUMAN_ADVANCED)


def descriptor_for_path(path: str) -> CommandDescriptor:
    top = _top_level_descriptor(path)
    if top is not None:
        return top
    parts = path.split()
    if len(parts) == 1:
        raise KeyError(path)
    summary = SUMMARY_OVERRIDES.get(path)
    if summary is None:
        summary = f"Run the `{path}` workflow."
    audience = _leaf_audience(parts[0], parts[-1])
    writes: WritesMode = "conditional"
    if any(
        flag in path
        for flag in ("status", "show", "list", "compare", "inspect", "grep")
    ):
        writes = "never"
    if path in {
        "context init",
        "context approve",
        "context mark-ready",
        "glossary add",
        "glossary remove",
        "glossary reset",
        "glossary mandate",
        "identity set",
        "identity clear",
        "judge create-profile",
        "judge prepare-grammar",
        "profile create",
        "build",
        "model set",
    }:
        writes = "always"
    if path in {"series prepare", "source analyze", "source interview-plan"}:
        writes = "with_write_flag"
    modes: frozenset[RuntimeModeName] = frozenset({"either"})
    if parts[0] in {"source", "series"}:
        modes = frozenset({"project_root"})
    if parts[0] == "review" and parts[-1] == "configure":
        modes = frozenset({"project_root", "profile_root"})
    requires_profile = parts[0] in {
        "context",
        "glossary",
        "translate",
        "review",
        "judge",
        "identity",
        "version",
        "model",
    }
    if path in {
        "profile list",
        "profile show",
        "agents status",
        "agents clean",
        "status",
    }:
        requires_profile = False
    hidden = False
    if path == "context recommend" or path in {
        "context add-term",
        "context remove-term",
        "context reset-term",
        "context mandate-term",
    }:
        hidden = True
    return CommandDescriptor(
        path=path,
        audience=audience,
        stage=parts[0],
        summary=summary,
        writes=writes,
        modes=modes,
        requires_profile=requires_profile,
        help_panel=PANEL_BY_AUDIENCE[audience],
        hidden=hidden,
    )


def apply_command_catalog(
    app: typer.Typer,
    *,
    root_app: typer.Typer,
) -> None:
    """Mutate Typer registrations so help output reflects the command catalog."""
    app.info.help = ROOT_HELP
    app.info.short_help = "Human-first book translation preparation."

    def apply_command(path: str, info: CommandInfo) -> None:
        descriptor = descriptor_for_path(path)
        info.help = descriptor.render_help()
        info.short_help = descriptor.summary
        info.rich_help_panel = descriptor.help_panel
        info.hidden = descriptor.hidden
        info.deprecated = descriptor.deprecated

    def apply_group(path: str, info: TyperInfo) -> None:
        descriptor = descriptor_for_path(path)
        info.help = descriptor.render_help()
        info.short_help = descriptor.summary
        info.rich_help_panel = descriptor.help_panel
        info.hidden = descriptor.hidden
        info.deprecated = descriptor.deprecated

    for command_info in root_app.registered_commands:
        if command_info.name is None:
            continue
        apply_command(command_info.name, command_info)

    def walk(prefix: str, group: typer.Typer) -> None:
        for command_info in group.registered_commands:
            if command_info.name is None:
                continue
            apply_command(f"{prefix} {command_info.name}", command_info)
        for group_info in group.registered_groups:
            if not isinstance(group_info.name, str):
                continue
            child_path = f"{prefix} {group_info.name}"
            apply_group(child_path, group_info)
            typer_instance = group_info.typer_instance
            if typer_instance is not None:
                walk(child_path, typer_instance)

    for group_info in app.registered_groups:
        if not isinstance(group_info.name, str):
            typer_instance = group_info.typer_instance
            if typer_instance is None:
                continue
            for command_info in typer_instance.registered_commands:
                if command_info.name is None:
                    continue
                apply_command(command_info.name, command_info)
            continue
        apply_group(group_info.name, group_info)
        typer_instance = group_info.typer_instance
        if typer_instance is not None:
            walk(group_info.name, typer_instance)
