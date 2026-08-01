---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0005
release_version: v0.5.0
kind: fixed
summary:
  Fixed `write_text_atomic` to preserve newlines on Windows and updated the
  subprocess test to use `sysconfig`
status: accepted
audience: null
scopes: []
source_refs:
  - git:1ce70f973bf17374cd634b43d7ab5c05ad1e8b20
  - git:6d6fd5b4c08ee806e038c8c631908ae9f02bfa09
paths:
  - booktx/io_utils.py
  - tests/test_import_health.py
  - tests/test_store_transactions.py
issues: []
prs: []
sources: []
breaking: false
internal: false
order: 5
---

The temporary file inside `write_text_atomic` is now opened with `newline=""` so Windows does not translate LF to CRLF during atomic writes. The import-health subprocess harness resolves the `booktx` entrypoint through `sysconfig.get_path("scripts")` with the `.exe` suffix on Windows, and the optimistic-revision mismatch fixture writes the existing-text payload as bytes to match the JSON the test expects.
