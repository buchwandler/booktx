---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 2
entry_id: entry-0009
release_version: v0.4.0
kind: changed
summary:
  Removed the profile select command and --select flag; project-root profile
  commands require --profile
status: accepted
audience: null
scopes: []
source_refs:
  - git:19f4cc856ebe4edbb851ce5a8d08f078e8f5c8ca
paths:
  - booktx/commands/profile.py
  - booktx/cli_support.py
  - booktx/commands/context.py
  - booktx/commands/judge.py
  - booktx/commands/agents.py
  - docs/commands.md
  - docs/profiles.md
  - README.md
issues: []
prs: []
sources:
  - git:19f4cc856ebe4edbb851ce5a8d08f078e8f5c8ca
breaking: true
internal: false
order: 9
---
