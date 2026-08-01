---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0004
release_version: v0.5.0
kind: changed
summary:
  Changed `booktx translate migrate-store` to accept `--to`, JSON output, custom
  backup, and a stale-lock policy
status: accepted
audience: null
scopes: []
source_refs:
  - git:f8b8081e2dd13fe0a3a3339236cb95ce2872d6ac
paths:
  - booktx/commands/translate.py
  - booktx/workflows/translate.py
  - booktx/command_catalog.py
issues: []
prs: []
sources: []
breaking: false
internal: false
order: 4
---

The workflow now takes an explicit `--to v2|v3` target, renders a structured JSON payload when `--json` is set, points its backup and JSON report at an optional `--backup-dir`, and can keep the original legacy store alongside the migrated v3 store with `--keep-legacy-copy`. The v3 lock path rejects stale locks by default and only repairs them when `--stale-lock-policy repair` is passed. The v1->v2 rewrite path is preserved when `--to` is omitted, and the catalog help summary now mentions both v2 and v3.
