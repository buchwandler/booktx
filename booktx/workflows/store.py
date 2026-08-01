"""Operational store commands."""

from __future__ import annotations

import json
from pathlib import Path

from booktx.cli_support import _load_runtime_or_exit, console
from booktx.store.status import build_store_status


def translate_store_status_workflow(
    project_dir: Path,
    profile: str | None = None,
    as_json: bool = False,
) -> None:
    runtime = _load_runtime_or_exit(
        project_dir,
        profile=profile,
        require_profile=True,
    )
    payload = build_store_status(runtime.project)
    if as_json:
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return
    console.print(f"canonical: {payload['canonical_format'] or 'missing'}")
    console.print(f"path: {payload['canonical_path'] or 'none'}")
    console.print(f"schema version: {payload['schema_version'] or 'unknown'}")
    console.print(f"records/chunks: {payload['record_count']}/{payload['chunk_count']}")
    console.print(f"source hash: {payload['source_hash_status']}")
    console.print(
        f"pending transaction: {'yes' if payload['pending_transaction'] else 'no'}"
    )
    legacy = payload["legacy_copy"]
    if legacy["present"]:
        label = "canonical" if legacy["canonical"] else "present, non-canonical"
        console.print(f"legacy copy: {label}")
    else:
        console.print("legacy copy: absent")
    if payload["findings"]:
        console.print("findings:")
        for finding in payload["findings"]:
            console.print(
                f"  {finding['severity']}: {finding['code']} — {finding['message']}"
            )
    console.print(f"next: {payload['suggested_next_command']}")
