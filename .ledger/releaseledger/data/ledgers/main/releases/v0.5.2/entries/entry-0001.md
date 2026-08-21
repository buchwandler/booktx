---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0001
release_version: v0.5.2
kind: added
summary:
  Added configurable first-pass translation quality gates and optional local
  LanguageTool checks
status: accepted
audience: null
scopes: []
source_refs:
  - git:c176bc173a286003cddf35ffcaa44cde35c35c2e
paths:
  - booktx/acceptance.py
  - booktx/agent_todo.py
  - booktx/agents_md.py
  - booktx/command_hints.py
  - booktx/commands/translate.py
  - booktx/judge_tasks.py
  - booktx/linguistic_audit.py
  - booktx/models.py
  - booktx/quality_backends/__init__.py
  - booktx/quality_backends/languagetool.py
  - booktx/quality_benchmark.py
  - booktx/tasks.py
  - booktx/todo_resume.py
  - booktx/todo_status.py
  - booktx/translation_quality.py
  - booktx/validation_receipts.py
  - booktx/workflows/agents.py
  - booktx/workflows/translate.py
  - docs/agent-workflow.md
  - docs/commands.md
  - docs/translation-contract.md
  - pyproject.toml
  - skills/booktx/SKILL.md
  - tests/fixtures/translation_quality/book6.json
  - tests/test_quality_benchmark.py
  - tests/test_translation_batch_budget.py
  - tests/test_translation_quality.py
  - tests/test_validation_receipts.py
issues: []
prs: []
sources:
  - git:c176bc173a286003cddf35ffcaa44cde35c35c2e
contributors: []
breaking: false
internal: false
order: 1
---
