---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0004
release_version: v0.5.2
kind: changed
summary:
  Changed translation workflows to use backend-neutral store operations and
  transactional v3 writes
status: accepted
audience: null
scopes: []
source_refs:
  - git:c58ed292f118aea79bc88dd87a26d493300537c9
paths:
  - booktx/cli_support.py
  - booktx/collection_utils.py
  - booktx/commands/judge.py
  - booktx/commands/judge_presenters.py
  - booktx/config.py
  - booktx/context.py
  - booktx/context_packs.py
  - booktx/context_sync.py
  - booktx/glossary_match.py
  - booktx/identity.py
  - booktx/inline_audit.py
  - booktx/judge_acceptance.py
  - booktx/judge_provenance.py
  - booktx/judge_sources.py
  - booktx/judge_store.py
  - booktx/path_display.py
  - booktx/source_analysis.py
  - booktx/source_analysis_context.py
  - booktx/source_analysis_render.py
  - booktx/source_analysis_snapshot.py
  - booktx/store/models.py
  - booktx/store/transactions.py
  - booktx/store/v1_v2.py
  - booktx/store/v3.py
  - booktx/translation_concordance.py
  - booktx/validate.py
  - booktx/workflows/judge.py
  - booktx/workflows/profile.py
  - booktx/workflows/root.py
  - booktx/workflows/termbase.py
  - booktx/workflows/translate.py
  - docs/agent-workflow.md
  - docs/api.md
  - docs/architecture.md
  - docs/concepts.md
  - docs/development.md
  - docs/profiles.md
  - docs/project-layout.md
  - docs/translation-contract.md
  - docs/translation-store.md
  - docs/troubleshooting.md
  - tests/test_config.py
  - tests/test_epub_inline_xhtml.py
  - tests/test_store_backend_parity.py
  - tests/test_store_backend_v3.py
  - tests/test_validate.py
issues: []
prs: []
sources:
  - git:c58ed292f118aea79bc88dd87a26d493300537c9
contributors: []
breaking: false
internal: false
order: 4
---
