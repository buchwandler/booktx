---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0006
release_version: v0.5.2
kind: changed
summary:
  Improved large-store status, search, list, and export reads with chunk-scoped
  repository access
status: accepted
audience: null
scopes: []
source_refs:
  - git:5c6294ecbd342a784cf68e4e466f843fe03d8144
paths:
  - .ledger/archledger/data/document-state.json
  - .ledger/archledger/data/profiles/arc42/sections/content-0002.md
  - .ledger/archledger/data/profiles/arc42/sections/content-0005.md
  - .ledger/archledger/data/profiles/arc42/sections/content-0006.md
  - .ledger/archledger/data/profiles/arc42/sections/content-0009.md
  - .ledger/archledger/data/profiles/arc42/sections/content-0011.md
  - .ledger/archledger/data/profiles/arc42/sections/content-0012.md
  - .ledger/archledger/data/records/constraints/constraint-0023.md
  - .ledger/archledger/data/records/decisions/adr-0070.md
  - .ledger/archledger/data/records/glossary/glossary-0090.md
  - .ledger/archledger/data/records/risks/risk-0082.md
  - .ledger/archledger/data/records/runtime/runtime-0053.md
  - ARCHITECTURE.md
  - README.md
  - booktx/config.py
  - booktx/judge_provenance.py
  - booktx/source_analysis_snapshot.py
  - booktx/source_record_index.py
  - booktx/status.py
  - booktx/store/__init__.py
  - booktx/store/doctor.py
  - booktx/store/migration.py
  - booktx/store/models.py
  - booktx/store/v1_v2.py
  - booktx/store/v3.py
  - booktx/validate.py
  - booktx/workflows/series.py
  - booktx/workflows/translate.py
  - booktx/workflows/translate_query.py
  - docs/architecture.md
  - pyproject.toml
  - tests/test_docs_consistency.py
  - tests/test_store_backend_v3.py
  - tests/test_translation_concordance.py
issues: []
prs: []
sources:
  - git:5c6294ecbd342a784cf68e4e466f843fe03d8144
contributors: []
breaking: false
internal: false
order: 6
---
