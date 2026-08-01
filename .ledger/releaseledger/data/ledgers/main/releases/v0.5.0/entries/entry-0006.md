---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0006
release_version: v0.5.0
kind: fixed
summary:
  Fixed v3 store lock acquisition to probe liveness on Windows and surface
  a clear stale-lock repair error
status: accepted
audience: null
scopes: []
source_refs:
  - git:e5152072c832fbc7deb5542370ea2ca3f255d46e
paths:
  - booktx/store/transactions.py
  - tests/test_store_transactions.py
issues: []
prs: []
sources: []
breaking: false
internal: false
order: 6
---

The lock owner liveness check now probes `OpenProcess` with `PROCESS_QUERY_LIMITED_INFORMATION` on Windows so the v3 store can detect stale locks from a dead process. When stale-lock repair is requested, the lock directory is removed with a guarded `shutil.rmtree` that surfaces a clear `translation_store_locked` error if the directory cannot be removed.
