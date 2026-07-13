"""Domain workflow functions for the quality-review pass (Phase 3 slice 6).

Wraps the review_status / review_tasks / review_todo / review_acceptance
service layers and the translation store mutations so the Typer command
layer never imports ``booktx.config``, ``booktx.translation_store``, or
``booktx.review_*`` mutations directly. User-facing error cases raise
:class:`booktx.errors.BooktxError`; CLI-only rendering and exit-code
mapping live in :mod:`booktx.commands.review`.

The review-gap API boundary is preserved: this module (and the service
modules it calls, e.g. :mod:`booktx.review_status.build_review_gap_index`)
own the gap-index computation. The Typer commands in
:mod:`booktx.commands.review` never call ``build_review_gap_index``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from booktx.config import (
    Project,
    _err,
    load_translation_review_task,
    load_translation_store,
    translation_review_ingest_block_path,
    translation_review_source_block_path,
    write_profile_config,
)
from booktx.errors import BooktxError
from booktx.models import QualityReviewConfig, ReviewPassConfig, TranslationStoreV2
from booktx.record_refs import parse_record_ref
from booktx.review_status import compute_review_snapshot

if TYPE_CHECKING:
    from booktx.models import ReviewTodo, TranslationReviewTask
    from booktx.review_status import ReviewStatusSnapshot
    from booktx.review_todo import ReviewTodoStatus
    from booktx.runtime import RuntimeContext
    from booktx.status import StatusBundle


# --- configure --------------------------------------------------------------


def configure_review_workflow(
    proj: Project,
    *,
    show: bool,
    enable: bool,
    disable: bool,
    pass_number: int | None,
    name: str | None,
    mode: str | None,
    enforce: str | None,
    before: int | None,
    after: int | None,
    batch_words: int | None,
    instructions: str | None,
    base: str | None,
    required_base_pass: int | None,
) -> dict[str, Any] | None:
    """Show or update profile quality-review configuration.

    Returns a payload describing the resulting config (or ``None`` to signal
    the "not configured" hint path that the command prints verbatim).
    """
    if enable and disable:
        raise _err("review_configure_flags", "use only one of --enable or --disable")
    cfg = proj.profile_config
    if cfg is None:
        raise _err("review_configure_no_config", "profile config is not available")
    if cfg.kind == "pass-through" and (enable or pass_number is not None):
        raise _err(
            "review_configure_passthrough",
            "pass-through profiles cannot configure quality review",
        )

    quality = cfg.quality_review or QualityReviewConfig()
    if (
        show
        and not enable
        and not disable
        and pass_number is None
        and cfg.quality_review is None
    ):
        return None  # signal the "not configured" hint path

    if disable:
        quality.enabled = False
    else:
        if enable:
            quality.enabled = True
        if pass_number is not None:
            existing = next(
                (p for p in quality.passes if p.pass_number == pass_number), None
            )
            if existing is None:
                existing = ReviewPassConfig(pass_number=pass_number)
                quality.passes.append(existing)
                quality.passes.sort(key=lambda p: p.pass_number)
            if pass_number not in quality.active_passes:
                quality.active_passes.append(pass_number)
                quality.active_passes.sort()
            updates: dict[str, Any] = {
                k: v
                for k, v in [
                    ("name", name),
                    ("mode", mode),
                    ("enforce", enforce),
                    ("before_records", before),
                    ("after_records", after),
                    ("batch_words", batch_words),
                    ("instructions", instructions),
                    ("base", base),
                    ("required_base_pass", required_base_pass),
                ]
                if v is not None
            }
            if updates:
                updated = existing.model_copy(update=updates)
                quality.passes = [
                    updated if p.pass_number == pass_number else p
                    for p in quality.passes
                ]
    cfg.quality_review = quality
    from pydantic import ValidationError as _ValidationError

    try:
        cfg = cfg.model_validate(cfg.model_dump(mode="json"))
    except _ValidationError as exc:
        raise _err(
            "review_configure_invalid",
            "invalid quality review configuration: " + str(exc),
        ) from exc
    write_profile_config(proj.root, cfg)
    quality = cfg.quality_review or QualityReviewConfig()
    return {
        "enabled": quality.enabled,
        "active_passes": list(quality.active_passes),
        "passes": [
            {
                "pass_number": p.pass_number,
                "name": p.name,
                "mode": p.mode,
                "enforce": p.enforce,
                "base": p.base,
                "before_records": p.before_records,
                "after_records": p.after_records,
                "batch_words": p.batch_words,
                "required_base_pass": p.required_base_pass,
            }
            for p in quality.passes
        ],
    }


# --- status -----------------------------------------------------------------


def build_review_status_snapshot(
    proj: Project,
    runtime: RuntimeContext,
    *,
    bundle: StatusBundle,
) -> ReviewStatusSnapshot:
    """Compute the review-status snapshot for ``review status``."""
    cfg = (
        proj.profile_config.quality_review if proj.profile_config is not None else None
    )
    store = load_translation_store(proj)
    record_order: list[tuple[str, str]] = []
    for chapter_id, rids in bundle.index.record_ids_by_chapter.items():
        record_order.extend((rid, chapter_id) for rid in rids)
    snapshot = compute_review_snapshot(store, cfg, record_order=record_order)
    # Populate the top-level next command from the first actionable pass.
    if snapshot.first_missing_record is not None:
        first_pass = next(
            (p for p in snapshot.passes if p.first_missing_record is not None),
            None,
        )
        if first_pass is not None:
            from booktx.command_hints import review_next_command

            snapshot.next_command = review_next_command(
                proj,
                mode=runtime.mode,
                pass_number=first_pass.pass_number,
                chapter_id=snapshot.first_missing_chapter,
            )
    return snapshot


# --- next -------------------------------------------------------------------


def create_next_review_task_workflow(
    proj: Project,
    runtime: RuntimeContext,
    *,
    bundle: StatusBundle,
    pass_number: int,
    chapter: str | None,
    max_words: int,
    selection: str,
    base: str | None,
    require_chunks: bool = True,
    require_no_drift: bool = True,
) -> TranslationReviewTask:
    """Create the next durable review task for a pass.

    The CLI-layer guards (``_require_chunks``, ``_require_no_source_drift``,
    ``_selected_chapter``) are invoked by the command before calling this
    workflow; the workflow only validates review-domain preconditions.
    """
    cfg = (
        proj.profile_config.quality_review if proj.profile_config is not None else None
    )
    if cfg is None or not cfg.enabled:
        raise _err(
            "review_not_enabled",
            "quality review is not enabled for this profile",
        )
    if pass_number not in cfg.active_passes:
        raise _err(
            "review_pass_not_active",
            f"pass {pass_number} is not in active_passes {cfg.active_passes}",
        )
    from booktx.review_tasks import (
        REVIEW_SELECTIONS,
        create_review_task,
        parse_review_base,
        select_review_records,
    )

    if selection not in REVIEW_SELECTIONS:
        raise _err(
            "review_selection_invalid",
            f"invalid --selection {selection!r}; expected one of: "
            + ", ".join(REVIEW_SELECTIONS),
        )
    pcfg = next((p for p in cfg.passes if p.pass_number == pass_number), None)
    try:
        parse_review_base(base, pcfg)
    except ValueError as exc:
        raise _err("review_base_invalid", str(exc)) from exc

    from booktx.status import selected_chapter

    selected_chapter_obj = selected_chapter(bundle, chapter)
    if selected_chapter_obj is None and chapter is not None:
        raise _err("review_unknown_chapter", f"unknown chapter id: {chapter}")
    if selected_chapter_obj is None:
        raise BooktxError("review_no_chapter", "No eligible records for review.")
    store = load_translation_store(proj)
    selected = select_review_records(
        bundle,
        store.records,
        cfg,
        pass_number=pass_number,
        chapter_id=selected_chapter_obj.chapter_id,
        max_words=max_words,
        selection=selection,
        base=base,
    )
    if not selected:
        raise BooktxError("review_no_records", "No records need review for this pass.")
    task = create_review_task(
        proj,
        bundle,
        cfg,
        selected,
        pass_number=pass_number,
        chapter=selected_chapter_obj,
    )
    return task


def review_task_block_paths(
    proj: Project, task: TranslationReviewTask
) -> tuple[str, str]:
    """Return the (source_block, ingest_block) project-relative paths."""
    src_path = str(translation_review_source_block_path(proj, task.review_task_id))
    ingest_path = str(translation_review_ingest_block_path(proj, task.review_task_id))
    return src_path, ingest_path


# --- insert -----------------------------------------------------------------


def accept_review_submission_workflow(
    proj: Project,
    *,
    bundle: StatusBundle,
    review_task_id: str,
    file: Path,
    activate: bool,
    no_activate: bool,
) -> dict[str, Any]:
    """Parse a review task submission and create review candidates."""
    from booktx.acceptance import SubmissionValidationError
    from booktx.review_acceptance import SubmittedReview, accept_review_submission
    from booktx.submissions import parse_block_submission

    task = load_translation_review_task(proj, review_task_id)
    if task is None:
        raise _err("review_task_not_found", f"review task not found: {review_task_id}")
    text = file.read_text("utf-8")
    parsed = parse_block_submission(text)
    submitted = [SubmittedReview(id=r.id, target=r.target) for r in parsed.records]
    cfg = (
        proj.profile_config.quality_review if proj.profile_config is not None else None
    )
    try:
        result = accept_review_submission(
            proj,
            task,
            submitted,
            bundle=bundle,
            quality_cfg=cfg,
            activate=activate,
            no_activate=no_activate,
        )
    except BooktxError:
        raise
    except SubmissionValidationError as exc:
        raise BooktxError(
            "review_submission_validation",
            "review submission failed validation: "
            + "; ".join(f.message for f in exc.findings),
        ) from exc
    return {
        "accepted_records": result.accepted_records,
        "activated": result.activated,
        "review_refs": list(result.review_refs),
    }


# --- activate / deactivate --------------------------------------------------


def activate_review_workflow(proj: Project, *, record_ref: str, review_ref: str) -> str:
    """Activate an existing review candidate for a single record."""
    from booktx.store import StoreFormat, open_translation_store
    from booktx.translation_store import find_review_candidate, review_chain_is_stale

    record_id = parse_record_ref(record_ref).canonical_id
    repo = open_translation_store(proj, default_format=StoreFormat.V2)
    activated_ref: str | None = None

    def _mutate(store: TranslationStoreV2) -> None:
        nonlocal activated_ref
        stored = store.records.get(record_id)
        if stored is None:
            raise _err(
                "review_no_record", f"record {record_id} has no stored translations"
            )
        candidate = find_review_candidate(stored, review_ref)
        if candidate is None:
            raise _err(
                "review_no_candidate",
                f"record {record_id} has no review {review_ref}",
            )
        if candidate.status != "accepted":
            raise _err(
                "review_not_accepted",
                f"review {review_ref} is {candidate.status!r}, not accepted",
            )
        if review_chain_is_stale(stored, candidate.review_ref):
            raise _err(
                "review_stale_chain",
                f"review {review_ref} has a stale derivation chain",
            )
        stored.active_review = candidate.review_ref
        activated_ref = candidate.review_ref

    repo.edit_v2(_mutate, summary="activate review candidate")
    assert activated_ref is not None
    return f"{record_id} -> {activated_ref}"


def deactivate_review_workflow(
    proj: Project,
    *,
    bundle: StatusBundle,
    record_ref: str,
) -> tuple[str, str]:
    """Deactivate the active review for a record.

    Returns ``(deactivation_message, record_id)`` so the command can render
    the recheck hint with the chapter id.
    """
    record_id = parse_record_ref(record_ref).canonical_id
    from booktx.store import StoreFormat, open_translation_store

    repo = open_translation_store(proj, default_format=StoreFormat.V2)
    old_ref: str | None = None

    def _mutate(store: TranslationStoreV2) -> None:
        nonlocal old_ref
        stored = store.records.get(record_id)
        if stored is None:
            raise _err(
                "review_no_record", f"record {record_id} has no stored translations"
            )
        if stored.active_review is None:
            raise _err("review_no_active", "no active review to deactivate")
        old_ref = stored.active_review
        stored.active_review = None

    repo.edit_v2(_mutate, summary="deactivate review candidate")
    assert old_ref is not None
    return f"{record_id}: deactivated review {old_ref}", record_id


# --- revise-record ----------------------------------------------------------


def revise_review_record_workflow(
    proj: Project,
    *,
    bundle: StatusBundle,
    record_ref: str,
    base_review: str,
    target_text: str,
    activate: bool,
) -> tuple[str, str]:
    """Revise an accepted review candidate by creating a new same-pass rerun.

    Returns ``(revision_message, record_id)`` so the command can render the
    recheck hint with the chapter id.
    """
    from hashlib import sha256

    from booktx.io_utils import utc_timestamp
    from booktx.models import TranslationReviewCandidate
    from booktx.translation_store import find_review_candidate, review_chain_is_stale

    record_id = parse_record_ref(record_ref).canonical_id
    from booktx.store import StoreFormat, open_translation_store

    repo = open_translation_store(proj, default_format=StoreFormat.V2)
    new_ref: str | None = None

    def _mutate(store: TranslationStoreV2) -> None:
        nonlocal new_ref
        stored = store.records.get(record_id)
        if stored is None:
            raise _err(
                "review_no_record", f"record {record_id} has no stored translations"
            )
        existing = find_review_candidate(stored, base_review)
        if existing is None:
            raise _err(
                "review_no_candidate", f"record {record_id} has no review {base_review}"
            )
        if existing.status != "accepted":
            raise _err(
                "review_not_accepted",
                f"review {base_review} is {existing.status!r}, not accepted",
            )
        if review_chain_is_stale(stored, existing.review_ref):
            raise _err(
                "review_stale_chain",
                f"review {base_review} has a stale derivation chain",
            )

        next_run = (
            max(
                (
                    r.run_number
                    for r in stored.reviews
                    if r.pass_number == existing.pass_number
                ),
                default=0,
            )
            + 1
        )
        new_ref = f"R{existing.pass_number}.{next_run}"
        created_at = utc_timestamp()
        candidate = TranslationReviewCandidate(
            pass_number=existing.pass_number,
            run_number=next_run,
            review_ref=new_ref,
            base_kind="review",
            base_ref=existing.review_ref,
            base_target_sha256=existing.target_sha256,
            target=target_text,
            target_sha256=sha256(target_text.encode("utf-8")).hexdigest(),
            status="accepted",
            created_at=created_at,
            updated_at=created_at,
            review_task_id=None,
            review_note=f"Revised from {base_review}.",
        )
        stored.reviews.append(candidate)
        if activate:
            stored.active_review = new_ref

    repo.edit_v2(_mutate, summary="revise review record")
    assert new_ref is not None
    message = f"revised: {record_id} -> {new_ref}" + (
        " (activated)" if activate else ""
    )
    return message, record_id


def validate_review_revision_workflow(
    proj: Project,
    *,
    bundle: StatusBundle,
    record_id: str,
    target_text: str,
) -> None:
    """Validate the revised record pair; raise on validation errors.

    Raises :class:`BooktxError` (with ``code="review_revision_invalid"``) when
    the pair validation reports blocking errors. The command layer renders
    the findings.
    """
    from booktx.models import TranslatedRecord
    from booktx.validate import Severity, load_validation_context, validate_record_pair

    source_view = bundle.index.source_by_id.get(record_id)
    if source_view is None:
        raise _err(
            "review_no_source",
            f"record {record_id} has no matching source record",
        )
    source_chunk = bundle.index.source_chunks.get(source_view.chunk_id)
    if source_chunk is None:
        raise _err(
            "review_no_source_chunk", f"record {record_id} has no matching source chunk"
        )
    source_record = next((r for r in source_chunk.records if r.id == record_id), None)
    if source_record is None:
        raise _err(
            "review_no_source_record",
            f"record {record_id} not found in source chunk",
        )
    context = load_validation_context(proj)
    pair_findings = validate_record_pair(
        source_record,
        TranslatedRecord(id=record_id, target=target_text),
        source_chunk.chunk_id,
        context,
    )
    pair_errors = [f for f in pair_findings if f.severity == Severity.ERROR]
    if pair_errors:
        # Attach the findings so the command can render them.
        raise ReviewValidationError(pair_errors)


class ReviewValidationError(BooktxError):
    """Carries pair-validation findings for ``review revise-record``.

    The command layer renders the findings via ``_render_submission_failures``
    and exits non-zero.
    """

    def __init__(self, findings: list[Any]) -> None:
        super().__init__("review_revision_invalid", "review revision failed validation")
        self.findings = findings


# --- todo -------------------------------------------------------------------


def build_review_todo_workflow(
    proj: Project,
    *,
    bundle: StatusBundle,
    chapters: int,
    batch_words: int,
) -> ReviewTodo:
    """Create a bounded multi-pass review todo with chapter selection."""
    cfg = proj.profile_config
    if cfg is None:
        raise _err("review_configure_no_config", "profile config is not available")
    quality = cfg.quality_review
    if quality is None or not quality.enabled:
        raise _err(
            "review_not_enabled",
            "quality review is not enabled; run `booktx review configure . --enable`",
        )
    from booktx.review_todo import build_review_todo

    try:
        todo = build_review_todo(
            proj,
            bundle,
            quality,
            chapters=chapters,
            batch_words=batch_words,
        )
    except ValueError as exc:
        raise _err("review_todo_build", str(exc)) from exc
    return todo


def write_review_todo_workflow(
    proj: Project,
    runtime: RuntimeContext,
    todo: ReviewTodo,
) -> tuple[Path, Path]:
    """Write the review todo to disk; return ``(json_path, md_path)``."""
    from booktx.review_todo import write_review_todo

    return write_review_todo(proj, todo, mode=runtime.mode)


def require_quality_review_enabled(proj: Project) -> QualityReviewConfig:
    """Return the quality-review config or raise ``BooktxError``."""
    cfg = proj.profile_config
    if cfg is None:
        raise _err("review_configure_no_config", "profile config is not available")
    quality = cfg.quality_review
    if quality is None or not quality.enabled:
        raise _err("review_not_enabled", "quality review is not enabled")
    return quality


def load_review_todo_for_status(
    proj: Project,
    *,
    bundle: StatusBundle,
    quality: QualityReviewConfig,
    review_todo_id: str | None,
    latest: bool,
) -> ReviewTodo:
    """Resolve the review todo for ``review todo-status``."""
    from booktx.review_todo import (
        latest_incomplete_review_todo,
        load_review_todo,
    )

    if review_todo_id is not None:
        todo = load_review_todo(proj, review_todo_id)
        if todo is None:
            raise _err(
                "review_todo_unknown", f"unknown review todo id: {review_todo_id}"
            )
        return todo
    if latest:
        todo = latest_incomplete_review_todo(proj, bundle, quality)
        if todo is None:
            raise _err(
                "review_todo_none_incomplete", "no incomplete review todo was found"
            )
        return todo
    raise _err("review_todo_no_selector", "pass --review-todo-id or --latest")


def compute_review_todo_status_workflow(
    proj: Project,
    *,
    bundle: StatusBundle,
    quality: QualityReviewConfig,
    runtime: RuntimeContext,
    todo: ReviewTodo,
) -> ReviewTodoStatus:
    from booktx.review_todo import compute_review_todo_status

    return compute_review_todo_status(todo, proj, bundle, quality, mode=runtime.mode)


def resume_review_todo_workflow(
    proj: Project,
    *,
    bundle: StatusBundle,
    quality: QualityReviewConfig,
    runtime: RuntimeContext,
    review_todo_id: str | None,
    latest: bool,
) -> TranslationReviewTask:
    """Create the next bounded review task for an open review todo."""
    from booktx.review_todo import resume_review_todo

    try:
        task = resume_review_todo(
            proj,
            bundle,
            quality,
            mode=runtime.mode,
            review_todo_id=review_todo_id,
            latest=latest,
        )
    except Exception as exc:  # noqa: BLE001 - resume surfaces assorted value errors
        raise _err("review_todo_resume", str(exc)) from exc
    return task  # type: ignore[no-any-return]


__all__ = [
    "ReviewValidationError",
    "accept_review_submission_workflow",
    "activate_review_workflow",
    "build_review_status_snapshot",
    "build_review_todo_workflow",
    "compute_review_todo_status_workflow",
    "configure_review_workflow",
    "create_next_review_task_workflow",
    "deactivate_review_workflow",
    "load_review_todo_for_status",
    "require_quality_review_enabled",
    "resume_review_todo_workflow",
    "review_task_block_paths",
    "revise_review_record_workflow",
    "validate_review_revision_workflow",
    "write_review_todo_workflow",
]
