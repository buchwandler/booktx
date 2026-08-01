---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 2
entry_id: entry-0010
release_version: v0.4.0
kind: fixed
summary: Fixed judge run error reporting and added a continuous-integration test workflow
status: accepted
audience: null
scopes: []
source_refs:
  - git:145a3fcbdd671ec688e39ebb993201aae1ee66bb
paths:
  - booktx/commands/judge.py
  - booktx/errors.py
  - booktx/workflows/judge.py
  - .github/workflows/tests.yml
issues: []
prs: []
sources:
  - git:145a3fcbdd671ec688e39ebb993201aae1ee66bb
breaking: false
internal: false
order: 10
---
