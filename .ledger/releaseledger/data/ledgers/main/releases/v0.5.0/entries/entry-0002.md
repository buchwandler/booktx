---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0002
release_version: v0.5.0
kind: added
summary:
  Added a v3 store migration flow with plan, backup, parity, rollback, and
  a stale-lock policy gate
status: accepted
audience: null
scopes: []
source_refs:
  - git:dd3a35de5c95ae5388cd6ee29a00f2aafe2f12c6
paths:
  - booktx/store/migration.py
  - booktx/store/doctor.py
  - booktx/store/models.py
  - booktx/store/transactions.py
  - booktx/store/paths.py
  - booktx/store/v3.py
  - booktx/store/v1_v2.py
  - booktx/commands/translate.py
  - booktx/workflows/translate.py
  - booktx/workflows/review.py
  - booktx/tasks.py
  - booktx/build.py
  - booktx/editor_indexes.py
  - booktx/glossary_audit.py
  - booktx/judge_acceptance.py
  - booktx/judge_sources.py
  - booktx/qa_scan.py
  - booktx/review_acceptance.py
  - booktx/review_status.py
  - booktx/review_tasks.py
  - booktx/review_todo.py
  - booktx/status.py
  - booktx/termbase_audit.py
  - booktx/validate.py
  - booktx/acceptance.py
  - booktx/config.py
  - docs/architecture.md
  - docs/concepts.md
  - docs/maintenance.md
  - docs/project-layout.md
  - docs/translation-contract.md
  - tests/store_backend_fixtures.py
  - tests/test_store_backend_parity.py
  - tests/test_store_backend_v3.py
  - tests/test_store_migration_v3.py
  - tests/test_store_models_v3.py
  - tests/test_store_transactions.py
  - tests/test_build.py
  - tests/test_review_todo.py
  - tests/test_validate.py
issues: []
prs: []
sources: []
breaking: false
internal: false
order: 2
---

The migration module now plans and applies a real migration: it inspects the store, dry-runs by default, requires `--write` to mutate, stamps a backup directory with sha256 receipts, writes a JSON report that records the migration id, source and target formats, record count, changed chunk ids, source-drift check, and parity-verification result, and can roll back from the stamped backup. The expanded doctor reports integrity findings in the JSON payload, and the expanded commit journal acquires a per-store-root write lock, verifies expected state, and cleans up on success. The new parity fixtures exercise v2 and v3 through the same flows.
