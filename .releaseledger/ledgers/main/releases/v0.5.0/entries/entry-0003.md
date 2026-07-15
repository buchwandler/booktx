---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0003
release_version: v0.5.0
kind: added
summary:
  Added `booktx translate lint-block`, a per-task agent brief, and a shared
  block-protocol module
status: accepted
audience: null
scopes: []
source_refs:
  - git:38a74d94653647b671210434e8ed98eafe9f324e
paths:
  - booktx/commands/translate.py
  - booktx/workflows/translate.py
  - booktx/tasks.py
  - booktx/agents_md.py
  - booktx/block_protocol.py
  - booktx/command_hints.py
  - booktx/command_catalog.py
  - booktx/glossary_tasking.py
  - booktx/todo_status.py
  - booktx/rendering.py
  - booktx/models.py
  - booktx/progress.py
  - booktx/judge_tasks.py
  - booktx/submissions.py
  - booktx/config.py
  - docs/agent-workflow.md
  - docs/human-workflows.md
  - docs/troubleshooting.md
  - skills/booktx/SKILL.md
  - tests/test_cli_translate.py
  - tests/test_cli_translate_todo.py
  - tests/test_cli_isolation.py
  - tests/test_command_catalog.py
  - tests/test_agents_md.py
  - tests/test_import_health.py
  - tests/test_judge.py
  - tests/test_tasks_submissions.py
  - tests/test_config.py
issues: []
prs: []
sources: []
breaking: false
internal: false
order: 3
---

`booktx translate lint-block` validates an ingest block against task coverage and validation rules before the first `insert`; it is read-only and never touches the canonical store. Each task now emits a `tasks/TASK.agent.md` brief that documents the action contract, the task-relevant glossary and termbase entries, the protected terms, and the full lint and insert commands. `booktx/block_protocol.py` centralizes the source-only directive prefixes (`# glossary:`, `# style:`, `# termbase:`) used by the brief, the lint-block check, and the agents-md templates. The bounded workflow can now be created and resumed in one call via `booktx translate todo-next . --write --resume --format block`.
