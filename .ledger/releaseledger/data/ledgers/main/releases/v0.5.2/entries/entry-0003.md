---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0003
release_version: v0.5.2
kind: fixed
summary:
  Fixed bounded translation submission to apply the configured quality gate
  before accepting records
status: accepted
audience: null
scopes: []
source_refs:
  - git:383e7b520edfa973c9e630f26da782f1140f5ebd
paths:
  - booktx/acceptance.py
  - booktx/workflows/translate.py
  - tests/test_acceptance.py
  - tests/test_cli_translate_todo.py
  - tests/test_translation_quality.py
issues: []
prs: []
sources:
  - git:383e7b520edfa973c9e630f26da782f1140f5ebd
contributors: []
breaking: false
internal: false
order: 3
---
