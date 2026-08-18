"""Query/export workflow helpers extracted from ``workflows.translate``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from booktx.cli_support import (
    _die,
    _load_project_or_exit,
    _load_runtime_or_exit,
    _require_chunks,
    _StoreRecordReader,
    console,
    resolve_profile_local_path,
)
from booktx.context import load_context
from booktx.errors import BooktxError
from booktx.models import (
    StoredTranslationRecordV2,
    TranslatedChunk,
    TranslatedRecord,
    TranslationCandidate,
)
from booktx.progress import load_source_chunks
from booktx.record_refs import resolve_record_range
from booktx.source_record_index import build_source_record_index
from booktx.store import open_translation_store
from booktx.translation_store import active_candidate, find_candidate
from booktx.validate import Severity, validate_record_pair


def translate_export_workflow(  # noqa: C901
    project_dir: Path,
    profile: str | None = None,
    version_ref: str | None = None,
    track: int | None = None,
    latest_subversion: bool = False,
    all_versions: bool = False,
) -> None:
    """Export fully accepted store-backed chunks into translated/*.json."""
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    _require_chunks(proj)
    store_repo = open_translation_store(proj)
    if all_versions and (version_ref is not None or track is not None):
        _die("--all-versions cannot be combined with --version or --track")
        return
    if track is not None and not latest_subversion:
        _die("--track currently requires --latest-subversion")
        return

    from booktx.io_utils import write_json_model_atomic

    def _pick_candidate(
        stored: StoredTranslationRecordV2,
    ) -> TranslationCandidate | None:
        if all_versions:
            return None
        if version_ref is not None:
            candidate = find_candidate(stored, version_ref)
            return (
                candidate
                if candidate is not None and candidate.status == "accepted"
                else None
            )
        if track is not None:
            matches = [
                candidate
                for candidate in stored.versions
                if candidate.version == track and candidate.status == "accepted"
            ]
            if not matches:
                return None
            return max(matches, key=lambda item: item.subversion)
        candidate = active_candidate(stored)
        return (
            candidate
            if candidate is not None and candidate.status == "accepted"
            else None
        )

    exported = 0
    if all_versions:
        version_map: dict[str, dict[str, list[TranslatedRecord]]] = {}
        for chunk in load_source_chunks(proj):
            store_records = dict(store_repo.iter_chunk_records(chunk.chunk_id))
            for record in chunk.records:
                stored = store_records.get(record.id)
                if stored is None:
                    continue
                for candidate in stored.versions:
                    if candidate.status != "accepted":
                        continue
                    version_map.setdefault(candidate.version_ref, {}).setdefault(
                        chunk.chunk_id, []
                    ).append(
                        TranslatedRecord(
                            id=record.id,
                            version=candidate.version_ref,
                            target=candidate.target,
                        )
                    )
        for ref, chunks in version_map.items():
            if proj.translated_dir is None:
                continue
            export_dir = proj.translated_dir / ref
            export_dir.mkdir(parents=True, exist_ok=True)
            for chunk_id, records in chunks.items():
                write_json_model_atomic(
                    export_dir / f"{chunk_id}.json",
                    TranslatedChunk(chunk_id=chunk_id, records=records),
                )
                exported += 1
        console.print(f"exported: {exported} chunk file(s) to {proj.translated_dir}")
        return

    context = load_context(proj)
    for chunk in load_source_chunks(proj):
        store_records = dict(store_repo.iter_chunk_records(chunk.chunk_id))
        translated_records: list[TranslatedRecord] = []
        for record in chunk.records:
            stored = store_records.get(record.id)
            if stored is None:
                translated_records = []
                break
            picked = _pick_candidate(stored)
            if picked is None:
                translated_records = []
                break
            translated_records.append(
                TranslatedRecord(
                    id=record.id,
                    version=picked.version_ref,
                    target=picked.target,
                )
            )
        if not translated_records:
            continue
        translated_chunk = TranslatedChunk(
            chunk_id=chunk.chunk_id, records=translated_records
        )
        findings = []
        for source_record, translated_record in zip(
            chunk.records, translated_chunk.records, strict=True
        ):
            findings.extend(
                validate_record_pair(
                    source_record, translated_record, chunk.chunk_id, context
                )
            )
        if any(finding.severity == Severity.ERROR for finding in findings):
            continue
        if proj.translated_dir is None:
            continue
        write_json_model_atomic(
            proj.translated_dir / f"{chunk.chunk_id}.json", translated_chunk
        )
        exported += 1
    console.print(f"exported: {exported} chunk(s) to {proj.translated_dir}")


def translation_list_workflow(
    project_dir: Path,
    range_spec: str | None = None,
    chapter: int | None = None,
    version: str | None = None,
    profile: str | None = None,
    as_json: bool = False,
) -> None:
    """List records for a range or chapter in source reading order."""
    proj = _load_project_or_exit(project_dir, profile=profile, require_profile=True)
    if (range_spec is None) == (chapter is None):
        _die("use exactly one of --range or --chapter")
        return
    source_index = build_source_record_index(proj)
    spec = range_spec if range_spec is not None else f"chapter:{chapter}"
    try:
        selected_ids = resolve_record_range(
            spec,
            ordered_record_ids=source_index.ordered_record_ids,
            chapter_record_ids=source_index.record_ids_by_chapter,
        )
    except ValueError as exc:
        _die(str(exc))
        return
    store_reader = _StoreRecordReader(proj)
    payload: list[dict[str, Any]] = []
    for record in source_index.ordered_records:
        if record.record_id not in selected_ids:
            continue
        item = {
            "id": record.record_id,
            "chunk_id": record.chunk_id,
            "source": record.source,
        }
        stored = store_reader.get(record.record_id)
        if stored is not None:
            if stored.active_version is not None:
                item["active_version"] = stored.active_version
            candidate = (
                find_candidate(stored, version)
                if version is not None
                else active_candidate(stored)
            )
            if candidate is not None:
                item["target"] = candidate.target
                item["status"] = candidate.status
                item["version_ref"] = candidate.version_ref
        payload.append(item)
    if as_json:
        console.print_json(json.dumps({"records": payload}, ensure_ascii=False))
        return
    for item in payload:
        suffix = f" [{item['version_ref']}]" if "version_ref" in item else ""
        console.print(f"{item['id']}{suffix}  {item['source']}")


def translation_search_cmd_workflow(  # noqa: C901
    project_dir: Path,
    profile: str | None = None,
    target: str | None = None,
    source: str | None = None,
    chapter: str | None = None,
    record: str | None = None,
    before: int = 0,
    after: int = 0,
    jsonl: bool = False,
    *,
    target_regex: str | None = None,
    source_regex: str | None = None,
    exclude_source: str | None = None,
    exclude_source_regex: str | None = None,
    match: str = "any",
    write_block: Path | None = None,
    as_json: bool = False,
    limit: int | None = None,
    count_only: bool = False,
    show_source: bool = True,
) -> None:
    """Search effective translations without scripting against the store file."""
    runtime = _load_runtime_or_exit(project_dir, profile=profile, require_profile=True)
    proj = runtime.project
    if match not in {"any", "all"}:
        _die("--match must be 'any' or 'all'")
        return
    if limit is not None and limit < 1:
        _die("--limit must be >= 1")
        return
    import re as _re

    try:
        source_pat = _re.compile(source_regex, _re.IGNORECASE) if source_regex else None
        target_pat = _re.compile(target_regex, _re.IGNORECASE) if target_regex else None
        exclude_source_pat = (
            _re.compile(exclude_source_regex, _re.IGNORECASE)
            if exclude_source_regex
            else None
        )
    except _re.error as exc:
        _die(f"invalid regex: {exc}")
        return
    if record is None and not any([source, target, source_pat, target_pat]):
        _die("provide at least one positive search criterion or --record")
        return

    from booktx.translation_store import effective_target_candidate

    source_index = build_source_record_index(proj)
    store_reader = _StoreRecordReader(proj)
    store_records = (
        {record_id: stored for record_id, stored in store_reader.repo.iter_records()}
        if chapter is None
        else None
    )
    source_by_id = source_index.source_by_id
    chapters_to_search = (
        [chapter] if chapter is not None else list(source_index.record_ids_by_chapter)
    )

    if record is not None:
        stored = store_reader.get(record)
        if stored is None:
            _die(f"record {record} not found in store")
            return
        eff = effective_target_candidate(stored)
        source_view = source_by_id.get(record)
        if jsonl:
            console.print_json(
                json.dumps(
                    {
                        "id": record,
                        "source": source_view.source if source_view else "",
                        "target": eff.target if eff else "",
                        "effective_ref": (
                            getattr(eff, "review_ref", None)
                            or getattr(eff, "version_ref", None)
                            or ""
                        ),
                    },
                    ensure_ascii=False,
                )
            )
        else:
            console.print(
                f"record: {record}"
                f" chapter={source_index.record_to_chapter.get(record, '?')}"
            )
            console.print(f"source: {source_view.source if source_view else ''}")
            console.print(f"target: {eff.target if eff else ''}")
            if eff:
                ref = getattr(eff, "review_ref", None) or getattr(
                    eff, "version_ref", "?"
                )
                console.print(f"ref: {ref}")
        return

    def _neighbor_target(rid: str) -> str:
        stored = (
            store_records.get(rid)
            if store_records is not None
            else store_reader.get(rid)
        )
        if stored is None:
            return ""
        eff = effective_target_candidate(stored)
        return eff.target if eff is not None else ""

    matches: list[dict[str, object]] = []
    records_scanned = 0
    for cid in chapters_to_search:
        flat = list(source_index.record_ids_by_chapter.get(cid, []))
        for idx, record_id in enumerate(flat):
            stored = (
                store_records.get(record_id)
                if store_records is not None
                else store_reader.get(record_id)
            )
            if stored is None:
                continue
            eff = effective_target_candidate(stored)
            if eff is None:
                continue
            records_scanned += 1
            source_view = source_by_id.get(record_id)
            source_text = source_view.source if source_view else ""
            target_text = eff.target

            source_hits = []
            target_hits = []
            if source is not None and source.lower() in source_text.lower():
                source_hits.append(source)
            if source_pat is not None and source_pat.search(source_text):
                source_hits.append(source_regex or "")
            if target is not None and target.lower() in target_text.lower():
                target_hits.append(target)
            if target_pat is not None and target_pat.search(target_text):
                target_hits.append(target_regex or "")
            if (
                exclude_source is not None
                and exclude_source.lower() in source_text.lower()
            ):
                continue
            if exclude_source_pat is not None and exclude_source_pat.search(
                source_text
            ):
                continue
            groups: list[bool] = []
            if source is not None or source_pat is not None:
                groups.append(bool(source_hits))
            if target is not None or target_pat is not None:
                groups.append(bool(target_hits))
            matched = all(groups) if match == "all" else any(groups)

            if matched:
                match_item = {
                    "id": record_id,
                    "chapter_id": cid,
                    "source": source_text
                    if not (
                        target is not None and source is None and source_pat is None
                    )
                    else "",
                    "target": target_text,
                    "effective_ref": (
                        getattr(eff, "review_ref", None)
                        or getattr(eff, "version_ref", None)
                        or ""
                    ),
                    "matched_source": source_hits,
                    "matched_target": target_hits,
                }

                if before > 0 or after > 0:
                    before_ids = flat[max(0, idx - before) : idx]
                    after_ids = flat[idx + 1 : idx + 1 + after]
                    match_item["before"] = [
                        {"id": rid, "target": _neighbor_target(rid)}
                        for rid in before_ids
                    ]
                    match_item["after"] = [
                        {"id": rid, "target": _neighbor_target(rid)}
                        for rid in after_ids
                    ]

                matches.append(match_item)

    total_matches = len(matches)
    visible_matches = matches[:limit] if limit is not None else matches
    if count_only:
        visible_matches = []

    if write_block is not None and not count_only:
        try:
            block_path = resolve_profile_local_path(
                proj, write_block, purpose="--write-block"
            )
        except BooktxError as exc:
            _die(str(exc))
            return
        block_path.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        source_lines: list[str] = []
        for item in visible_matches:
            rid = str(item["id"])
            lines.extend([f">>> {rid}", str(item["target"]), ""])
            source_lines.extend(
                [
                    f">>> {rid}",
                    f"source: {item.get('source', '')}",
                    f"target: {item.get('target', '')}",
                    "",
                ]
            )
        block_path.write_text("\n".join(lines).rstrip() + "\n", "utf-8")
        block_path.with_suffix(block_path.suffix + ".sources.txt").write_text(
            "\n".join(source_lines).rstrip() + "\n", "utf-8"
        )
        console.print(f"wrote block: {block_path}")

    if as_json:
        console.print_json(
            json.dumps(
                {
                    "matches": [] if count_only else visible_matches,
                    "match_count": total_matches,
                    "rendered_count": len(visible_matches),
                    "truncated": len(visible_matches) < total_matches,
                    "records_scanned": records_scanned,
                },
                ensure_ascii=False,
            )
        )
    elif jsonl:
        for match_item in visible_matches:
            console.print(
                json.dumps(match_item, ensure_ascii=False),
                soft_wrap=True,
                markup=False,
            )
    else:
        console.print(
            f"found {total_matches} matches (records scanned: {records_scanned})"
        )
        for match_item in visible_matches:
            rec_id = match_item.get("id", "")
            target_text = str(match_item.get("target", ""))
            disp = f"{rec_id}: {target_text[:100]}"
            if len(disp) < len(target_text):
                disp += "..."
            if show_source:
                disp += f" | source: {match_item.get('source', '')}"
            console.print(f"  {disp}", soft_wrap=True, markup=False)
