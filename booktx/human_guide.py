"""Read-only lifecycle guide for human operators."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from booktx.command_hints import build_command, check_command
from booktx.config import (
    current_source_sha256,
    extracted_source_sha256,
    find_source_file,
    list_profiles,
    load_manifest,
)
from booktx.context import (
    load_context,
    unapproved_required_questions,
    unresolved_required_questions,
)
from booktx.workflows.agents import agents_status_workflow

if TYPE_CHECKING:
    from booktx.runtime import RuntimeContext
    from booktx.status import StatusBundle


@dataclass(frozen=True, slots=True)
class GuideAction:
    summary: str
    command: str | None = None


@dataclass(frozen=True, slots=True)
class GuideResult:
    stage: str
    project: str
    profile: str | None
    human_blockers: tuple[str, ...]
    human_next: GuideAction | None
    agent_next: GuideAction | None
    warnings: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "project": self.project,
            "profile": self.profile,
            "human_blockers": list(self.human_blockers),
            "human_next": None
            if self.human_next is None
            else {
                "summary": self.human_next.summary,
                "command": self.human_next.command,
            },
            "agent_next": None
            if self.agent_next is None
            else {
                "summary": self.agent_next.summary,
                "command": self.agent_next.command,
            },
            "warnings": list(self.warnings),
        }


def _project_arg(project_arg: str) -> str:
    return project_arg or "."


def _profile_fragment(runtime: RuntimeContext) -> str:
    if runtime.mode.isolated_output or not runtime.project.profile:
        return ""
    return f" --profile {runtime.project.profile}"


def _project_command(
    runtime: RuntimeContext,
    project_arg: str,
    command: str,
    *,
    suffix: str = "",
) -> str:
    return (
        f"booktx {command} {_project_arg(project_arg)}"
        f"{_profile_fragment(runtime)}{suffix}"
    )


def _translation_agent_action(runtime: RuntimeContext) -> GuideAction:
    profile = runtime.project.profile or runtime.mode.profile_name or ""
    if runtime.mode.isolated_output:
        return GuideAction(
            summary="Continue the isolated translation workflow in this profile root.",
            command=(
                "booktx translate next . --unit batch --max-words 800 --format block"
            ),
        )
    return GuideAction(
        summary=(
            "Start or continue the isolated coding-agent harness in"
            f" translations/{profile}/."
        ),
        command=None,
    )


def build_guide_result(
    runtime: RuntimeContext,
    *,
    bundle: StatusBundle | None = None,
    project_arg: str = ".",
) -> GuideResult:
    """Resolve the current human lifecycle stage and next actions."""
    project = runtime.project
    profiles = list_profiles(project.root)
    profile = project.profile
    warnings: list[str] = []

    try:
        find_source_file(project)
    except Exception:
        return GuideResult(
            stage="source_missing",
            project=str(project.root),
            profile=profile,
            human_blockers=("Source document is missing.",),
            human_next=GuideAction(
                summary=(
                    "Place the source Markdown or EPUB file under source/"
                    " and initialize extraction."
                ),
                command=f"booktx extract {_project_arg(project_arg)}",
            ),
            agent_next=None,
        )

    manifest = load_manifest(project)
    if manifest is None:
        return GuideResult(
            stage="extraction_missing",
            project=str(project.root),
            profile=profile,
            human_blockers=("Source extraction has not been run yet.",),
            human_next=GuideAction(
                summary="Extract the source into stable records.",
                command=f"booktx extract {_project_arg(project_arg)}",
            ),
            agent_next=None,
        )

    if current_source_sha256(project) != extracted_source_sha256(project):
        return GuideResult(
            stage="source_drifted",
            project=str(project.root),
            profile=profile,
            human_blockers=("The source changed after the last extraction.",),
            human_next=GuideAction(
                summary="Re-extract the source before continuing.",
                command=f"booktx extract {_project_arg(project_arg)}",
            ),
            agent_next=None,
        )

    if bundle is None:
        from booktx.status import build_status_snapshot

        ctx = load_context(project)
        bundle = build_status_snapshot(
            project,
            context_exists=ctx is not None,
            context_ready=bool(ctx and ctx.ready),
        )

    if bundle.epub_audit is not None and bundle.epub_audit.has_blocking_errors:
        return GuideResult(
            stage="chapter_audit_blocking",
            project=str(project.root),
            profile=profile,
            human_blockers=("The EPUB chapter audit has blocking errors.",),
            human_next=GuideAction(
                summary="Review the chapter audit before creating new work.",
                command=f"booktx chapters {_project_arg(project_arg)} --audit",
            ),
            agent_next=None,
        )

    if not profiles:
        target = project.config.target_language or "<target-language>"
        return GuideResult(
            stage="no_profile",
            project=str(project.root),
            profile=None,
            human_blockers=("No translation profile exists yet.",),
            human_next=GuideAction(
                summary="Create the first translation profile.",
                command=(
                    f"booktx profile create {_project_arg(project_arg)}"
                    f" PROFILE --target {target}"
                ),
            ),
            agent_next=None,
        )

    if profile is None:
        return GuideResult(
            stage="choose_profile",
            project=str(project.root),
            profile=None,
            human_blockers=("Choose the profile to work on from the project root.",),
            human_next=GuideAction(
                summary=(
                    "List profiles, then rerun guide/status with --profile"
                    " or from the profile root."
                ),
                command=f"booktx profile list {_project_arg(project_arg)}",
            ),
            agent_next=None,
        )

    ctx = load_context(project)
    if ctx is None:
        return GuideResult(
            stage="context_missing",
            project=str(project.root),
            profile=profile,
            human_blockers=("The profile context has not been initialized.",),
            human_next=GuideAction(
                summary="Create the profile-local context.",
                command=_project_command(
                    runtime, project_arg, "context init", suffix=" --non-interactive"
                ),
            ),
            agent_next=None,
        )

    from booktx.source_analysis import read_canonical_report

    report = read_canonical_report(project)
    if report is None or report.source_sha256 != current_source_sha256(project):
        return GuideResult(
            stage="source_analysis_missing",
            project=str(project.root),
            profile=profile,
            human_blockers=("Canonical source analysis is missing or stale.",),
            human_next=GuideAction(
                summary="Refresh source analysis and profile snapshots.",
                command=f"booktx source analyze {_project_arg(project_arg)}"
                " --write --sync-profiles",
            ),
            agent_next=None,
        )

    from booktx.workflows.source_interview import interview_status

    interview = interview_status(project, profile=profile)
    if bool(interview["missing"]) or bool(interview["stale"]):
        return GuideResult(
            stage="source_interview_plan",
            project=str(project.root),
            profile=profile,
            human_blockers=("The source-policy interview ledger is missing or stale.",),
            human_next=GuideAction(
                summary="Regenerate the source-policy interview ledger.",
                command=_project_command(
                    runtime, project_arg, "source interview-plan", suffix=" --write"
                ),
            ),
            agent_next=None,
        )
    if int(cast(int, interview["open"])) > 0:
        return GuideResult(
            stage="source_interview_open",
            project=str(project.root),
            profile=profile,
            human_blockers=("Source-policy interview items are still open.",),
            human_next=GuideAction(
                summary=(
                    "Review the next open source-policy interview item"
                    f" ({interview['open']} remaining)."
                ),
                command=_project_command(
                    runtime,
                    project_arg,
                    "source interview-next",
                    suffix=" --format markdown",
                ),
            ),
            agent_next=None,
        )

    unresolved = unresolved_required_questions(ctx)
    unapproved = unapproved_required_questions(ctx)
    if unresolved or unapproved:
        blockers: list[str] = []
        if unresolved:
            blockers.append(
                f"{len(unresolved)} required context question(s) remain unanswered."
            )
        if unapproved:
            blockers.append(
                f"{len(unapproved)} required context answer(s) still"
                " need human approval."
            )
        return GuideResult(
            stage="context_approval",
            project=str(project.root),
            profile=profile,
            human_blockers=tuple(blockers),
            human_next=GuideAction(
                summary="Review and approve the required context questionnaire.",
                command=_project_command(
                    runtime, project_arg, "context questionnaire", suffix=" --stdout"
                ),
            ),
            agent_next=None,
        )

    if not ctx.ready:
        return GuideResult(
            stage="context_not_ready",
            project=str(project.root),
            profile=profile,
            human_blockers=("The context exists but is not marked ready.",),
            human_next=GuideAction(
                summary="Mark the approved context ready for translation.",
                command=_project_command(runtime, project_arg, "context mark-ready"),
            ),
            agent_next=None,
        )

    agents_entries = agents_status_workflow(
        project.root
        if not runtime.mode.isolated_output
        else runtime.mode.profile_root or project.root
    )
    relevant_profile_entry = next(
        (
            entry
            for entry in agents_entries
            if entry.scope == "profile" and entry.profile == profile
        ),
        None,
    )
    if (
        relevant_profile_entry is None
        or relevant_profile_entry.inspection.state != "managed-valid"
        or relevant_profile_entry.stale
    ):
        return GuideResult(
            stage="agents_missing",
            project=str(project.root),
            profile=profile,
            human_blockers=(
                "The isolated AGENTS.md workspace instructions are missing or stale.",
            ),
            human_next=GuideAction(
                summary="Write fresh isolated harness instructions for this profile.",
                command=(
                    f"booktx agents write {_project_arg(project_arg)}"
                    f" --mode isolated --profile {profile}"
                ),
            ),
            agent_next=None,
        )

    totals = bundle.snapshot.totals
    if totals.records_remaining > 0:
        if totals.invalid_translation_files or totals.stale_translation_files:
            warnings.append("Existing translation files have validation issues.")
        return GuideResult(
            stage="translating",
            project=str(project.root),
            profile=profile,
            human_blockers=(),
            human_next=GuideAction(
                summary="No policy decision is currently required. Monitor progress.",
                command=_project_command(runtime, project_arg, "status"),
            ),
            agent_next=_translation_agent_action(runtime),
            warnings=tuple(warnings),
        )

    if totals.invalid_translation_files or totals.stale_translation_files:
        return GuideResult(
            stage="validation_failing",
            project=str(project.root),
            profile=profile,
            human_blockers=(
                "The translation is complete, but validation findings remain.",
            ),
            human_next=GuideAction(
                summary="Run the normal pre-build checks and fix the reported issues.",
                command=check_command(project, mode=runtime.mode),
            ),
            agent_next=None,
        )

    return GuideResult(
        stage="ready_to_build",
        project=str(project.root),
        profile=profile,
        human_blockers=(),
        human_next=GuideAction(
            summary="The profile is ready for a final build.",
            command=build_command(project, mode=runtime.mode),
        ),
        agent_next=None,
    )
