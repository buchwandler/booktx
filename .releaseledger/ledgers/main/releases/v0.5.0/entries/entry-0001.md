---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0001
release_version: v0.5.0
kind: added
summary:
  Added a v3 translation store with a backend-neutral repository, a store doctor,
  and a crash-safe commit journal
status: accepted
audience: null
scopes: []
source_refs:
  - git:de9bab5a1a049a58767d039a576588624087ed07
paths:
  - booktx/store/__init__.py
  - booktx/store/detect.py
  - booktx/store/doctor.py
  - booktx/store/migration.py
  - booktx/store/models.py
  - booktx/store/paths.py
  - booktx/store/transactions.py
  - booktx/store/v1_v2.py
  - booktx/store/v3.py
  - booktx/acceptance.py
  - booktx/identity.py
  - booktx/judge_acceptance.py
  - booktx/pass_through.py
  - booktx/review_acceptance.py
  - booktx/build.py
  - booktx/workflows/review.py
  - booktx/workflows/translate.py
  - booktx/workflows/root.py
  - booktx/commands/translate.py
  - booktx/config.py
  - booktx/command_catalog.py
  - docs/architecture.md
  - docs/concepts.md
  - docs/api.md
  - docs/profiles.md
  - docs/project-layout.md
  - docs/troubleshooting.md
  - skills/booktx/SKILL.md
  - README.md
  - tests/test_store_backend_v3.py
  - tests/test_store_migration_v3.py
  - tests/test_store_models_v3.py
  - tests/test_store_transactions.py
  - tests/test_acceptance.py
  - tests/test_cli.py
  - tests/test_cli_translate.py
  - tests/test_config.py
  - tests/test_profiles.py
  - tests/test_validate.py
issues: []
prs: []
sources: []
breaking: false
internal: false
order: 1
---

The new `booktx/store/` package introduces `open_translation_store` as the single entry point and a `V3TranslationStoreRepository` that stores a manifest plus per-chunk current, translation-candidate, and review-candidate shards under `translations/<profile>/translation-store/`. `TranslationStoreV2` remains the compatibility materialization model returned by the loader surface. Acceptance, review, judge, identity, build, status, and import-legacy writers now mutate the canonical store through the repository API so v2 and v3 share one code path. New profiles still default to the v2 canonical store; v3 is reached by running `booktx translate migrate-store . --to v3 --write`.
